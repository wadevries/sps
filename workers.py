#  Copyright 2011 Tijmen Roberti
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""
This file contains all task handler for work that can be performed in
the background.
"""
import os
import logging
from google.appengine.api import users
from google.appengine.api import taskqueue
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
import simplejson as json

import api
from model import Domain, Task, TaskIndex, AssigneeIndex, Context, User

class UpdateTaskIndex(webapp.RequestHandler):
    """
    Handler to update or create the TaskIndex for a single task.
    If the index is updated, then tasks are queued to update all
    the subtasks of the task as well.
    """
    def post(self):
        domain_identifier = self.request.get('domain')
        task_identifier = self.request.get('task')
        force_update = self.request.get('force_update')

        def txn():
            # Returns (task, changed) tuple, where changed is set if
            # the task index was updated.
            task = api.get_task(domain_identifier, task_identifier)
            if not task:
                logging.error("Task '%s/%s' does not exist",
                              domain_identifier, task_identifier)
                return None, False
            index = TaskIndex.get_by_key_name(task_identifier, parent=task)
            new_index = False
            if not index:
                index = TaskIndex(parent=task,
                                  key_name=task_identifier,
                                  hierarchy=[],
                                  level=0)
                new_index = True
            parent_identifier = task.parent_task_identifier()
            parent_hierarchy = []
            if parent_identifier:
                parent_key = task.parent_task_key()
                parent_index = TaskIndex.get_by_key_name(parent_identifier,
                                                         parent=parent_key)
                if not parent_index:
                    logging.error("Missing index for parent task '%s/%s'",
                                  domain_identifier, parent_identifier)
                    self.error(400) # Retry
                    return None, False
                parent_hierarchy = parent_index.hierarchy

            hierarchy = parent_hierarchy
            if parent_identifier:
                hierarchy.append(parent_identifier)
            if (force_update
                or new_index
                or set(index.hierarchy) ^ set(hierarchy)):
                index.hierarchy = hierarchy
                index.level = len(hierarchy)
                index.put()
                return task, True
            return task, False

        task, changed = db.run_in_transaction(txn)

        if not changed:
            logging.info("Task '%s/%s' index is unchanged",
                         domain_identifier, task_identifier)
            return

        query = Task.all(keys_only=True).\
            ancestor(Domain.key_from_name(domain_identifier)).\
            filter('parent_task =', task.key())
        for subtask_key in query:
            subtask_identifier = subtask_key.id_or_name()
            # TODO(tijmen): Batch queue tasks
            UpdateTaskIndex.queue_task(domain_identifier,
                                       subtask_identifier,
                                       force_update)

    @staticmethod
    def queue_task(domain_identifier, task_identifier, force=False):
        """
        Queues a new task to update the task index of the task with
        the given identifier. If force is set to true, the update will
        always be done, even if the hierarchy is not changed.
        """
        queue = taskqueue.Queue('update-task-index')
        # TODO(tijmen): Create a unique task name based on some sort
        # of versioning number in the task.
        task = taskqueue.Task(url='/workers/update-task-index',
                              params={ 'task': task_identifier,
                                       'domain': domain_identifier,
                                       'force_update': force})
        try:
            queue.add(task)
        except taskqueue.TransientError:
            queue.add(task)


class UpdateAssigneeIndex(webapp.RequestHandler):
    """
    Updates or creates the AssigneeIndex for a given task. Only atomic
    tasks used starting point of this worker. If required, the assignee
    index of parent tasks will be updated as well.
    """
    def post(self):
        domain_identifier = self.request.get('domain')
        task_identifier = self.request.get('task')
        sequence = int(self.request.get('sequence'))
        add_assignee = self.request.get('add_assignee')
        remove_assignee = self.request.get('remove_assignee')
        assert add_assignee != remove_assignee

        def update_reference_count(reference_counts):
            if remove_assignee in reference_counts:
                count = reference_counts[remove_assignee]
                if not count > 0:
                    logging.error("Attempt to decrement 0 ref count")
                else:
                    reference_counts[remove_assignee] = count - 1
            if add_assignee:
                count = reference_counts.get(add_assignee, 0)
                reference_counts[add_assignee] = count + 1

        def _get_index(task):
            index = AssigneeIndex.get_by_key_name(task.identifier(),
                                                  parent=task)
            if not index:
                index = AssigneeIndex(key_name=task.identifier(), parent=task)
            return index

        def txn():
            task = api.get_task(domain_identifier, task_identifier)
            description = "%s/%s" % (domain_identifier, task_identifier)
            if not task:
                logging.error("Task '%s/%s' does not exist", description)
                return

            index = _get_index(task)
            if index.sequence < sequence: # Not our time yet, retry later
                self.error(400)
                return
            if index.sequence > sequence: # passed us, must be a duplicate
                return

            reference_counts = json.loads(index.reference_counts)
            update_reference_count(reference_counts)
            propagate_add_assignee = add_assignee
            propagate_remove_assignee = remove_assignee
            if reference_counts.get(add_assignee, None) == 1:
                # New assignee entry
                index.assignees.append(add_assignee)
            else:
                propagate_add_assignee = None # do not propagate
            if reference_counts.get(remove_assignee, None) == 0:
                # Assignee is completely gone
                del reference_counts[remove_assignee]
                index.assignees.remove(remove_assignee)
            else:
                propagate_remove_assignee = None # do not propagate

            index.assignee_count = len(index.assignees)
            index.reference_counts = json.dumps(reference_counts)
            index.sequence = index.sequence + 1 # move forward
            index.put()
            parent_task = task.parent_task
            if parent_task:
                UpdateAssigneeIndex.queue_worker(parent_task,
                                                 propagate_add_assignee,
                                                 propagate_remove_assignee)
        db.run_in_transaction(txn)


    @staticmethod
    def queue_worker(task, add_assignee=None, remove_assignee=None):
        """
        Queues a new worker to update the assignees of the given
        task.

        There are two arguments: add_assignee is used to add an
        assignee to a task and remove_assignee is used to remove one
        from the task. They can also be None if none is updated. They
        can also both be specified, to indicate a change in assignees.

        This function must be run as part of a transaction because
        this function increments the assignee sequence number of the
        tasks and queues a transactional task.

        If both assignee arguments are None, or are the same User
        instance, then this function will act as a no-op.

        Args:
            task: An instance of the Task mode.
            add_assignee: The identifier string of the user that is added
                as assignee. Can be None.
            remove_assignee: The identifier string of the user that is
                removed as assignee. Can be None.

        Raises:
            ValueError: If this function is called outside a transaction.
        """
        if not add_assignee and not remove_assignee:
            return
        if (add_assignee and remove_assignee
            and add_assignee.identifier() == remove_assignee.identifier()):
            return

        if not db.is_in_transaction():
            raise ValueError("Requires a transaction")

        sequence = task.assignee_index_sequence
        task.assignee_index_sequence = sequence + 1
        task.put()
        logging.info("Queuing worker: task '%s', add: '%s', remove: '%s'"
                     " sequence: %s" % (task.identifier(), add_assignee,
                                        remove_assignee, sequence))
        queue = taskqueue.Queue('update-assignee-index')
        task = taskqueue.Task(url='/workers/update-assignee-index',
                              params={ 'task': task.identifier(),
                                       'domain': task.domain_identifier(),
                                       'sequence': sequence,
                                       'add_assignee': add_assignee,
                                       'remove_assignee': remove_assignee})
        try:
            queue.add(task, transactional=True)
        except taskqueue.TransientError:
            queue.add(task, transactional=True)


mapping = [('/workers/update-task-index', UpdateTaskIndex),
           ('/workers/update-assignee-index', UpdateAssigneeIndex)]
application = webapp.WSGIApplication(mapping)

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

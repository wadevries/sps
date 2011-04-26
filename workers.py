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
                    self.error(500) # Retry
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
            UpdateTaskIndex.queue_worker(domain_identifier,
                                         subtask_identifier,
                                         force_update)

    @staticmethod
    def queue_worker(domain_identifier,
                     task_identifier,
                     force=False,
                     transactional=False):
        """
        Queues a new task to update the task index of the task with
        the given identifier.

        Args:
            domain_identifier: The domain identifier string
            task_identifier: The task identifier string
            force: If set to true, the entire hierarchy will be updated,
                even if there are no changes.
            transactional: If set to true, then the task will be added
                as a transactional task.

        Raises:
            ValueError: If transactional is set to True and the
                 function is not called as part of a transaction.
        """
        if transactional and not db.is_in_transaction():
            raise ValueError("Requires a transaction")

        queue = taskqueue.Queue('update-task-index')
        task = taskqueue.Task(url='/workers/update-task-index',
                              params={ 'task': task_identifier,
                                       'domain': domain_identifier,
                                       'force_update': force})
        try:
            queue.add(task, transactional=transactional)
        except taskqueue.TransientError:
            queue.add(task, transactional=transactional)


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
                return None, False

            index = _get_index(task)
            if index.sequence < sequence: # Not our time yet, retry later
                self.error(500)
                return task, False
            if index.sequence > sequence: # passed us, must be a duplicate
                return task, False

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
            return task, (propagate_add_assignee or propagate_remove_assignee)

        task, changed = db.run_in_transaction(txn)
        if changed and task:
            BakeAssigneeDescription.queue_worker(task)


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
        if add_assignee == remove_assignee:
            return

        if not db.is_in_transaction():
            raise ValueError("Requires a transaction")

        sequence = task.assignee_index_sequence
        task.assignee_index_sequence = sequence + 1
        task.put()
        queue = taskqueue.Queue('update-assignee-index')
        params = { 'task': task.identifier(),
                   'domain': task.domain_identifier(),
                   'sequence': sequence }
        if add_assignee:
            params['add_assignee'] = add_assignee
        if remove_assignee:
            params['remove_assignee'] = remove_assignee
        try:
            task = taskqueue.Task(url='/workers/update-assignee-index',
                                  params=params)
            queue.add(task, transactional=True)
        except taskqueue.TransientError:
            queue.add(task, transactional=True)


class BakeAssigneeDescription(webapp.RequestHandler):
    """
    Bakes the task assignee description based on the assignees in the
    assignee index.
    """
    def post(self):
        domain_identifier = self.request.get('domain')
        task_identifier = self.request.get('task')
        task = api.get_task(domain_identifier, task_identifier)
        if not task:
            logging.error("No task '%s/%s'", domain_identifier, task_identifier)
            return
        index = AssigneeIndex.get_by_key_name(task.identifier(),
                                              parent=task)
        if not index:
            logging.error("No assignee index for task '%s'", task)
            return

        assignees = index.assignees
        description = ""
        if len(assignees) == 1:
            user = api.get_user_from_identifier(assignees[0])
            description = user.name
        elif len(assignees) == 2:
            user0 = api.get_user_from_identifier(assignees[0])
            user1 = api.get_user_from_identifier(assignees[1])
            description = "%s, %s" % (user0.name, user1.name)
        elif len(assignees) > 2:
            user = api.get_user_from_identifier(assignees[0])
            description = "%s and %d others" % (user, len(assignees) - 1)
        task.baked_assignee_description = description
        task.put()

    @staticmethod
    def queue_worker(task):
        """Queues a worker to update task assignee description of the given
        task instance.
        """
        taskqueue.add(url='/workers/bake-assignee-description',
                      params={ 'task': task.identifier(),
                               'domain': task.domain_identifier()})


mapping = [('/workers/update-task-index', UpdateTaskIndex),
           ('/workers/update-assignee-index', UpdateAssigneeIndex),
           ('/workers/bake-assignee-description', BakeAssigneeDescription)]
application = webapp.WSGIApplication(mapping)

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

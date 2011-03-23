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

import api
from model import Domain, Task, TaskIndex, Context, User

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


application = webapp.WSGIApplication([('/workers/update-task-index',
                                       UpdateTaskIndex)])

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

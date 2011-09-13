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
Mappers, currently only used for schema migration etc.
"""
import logging
from mapreduce import operation as op, context
from google.appengine.ext import db

from model import Domain, Task, User, TaskIndex
import workers
import api


def rebuild_hierarchy(task):
    """
    Rebuilds all derived properties and hierarchies. This includes the
    TaskIndexes. This operation will only create tasks, which will do
    the actual work.
    """
    if task.root():
        workers.UpdateTaskHierarchy.enqueue(task.domain_identifier(),
                                            task.identifier())

    domain_key = Domain.key_from_name(task.domain_identifier())
    task_key = task.key()
    logging.info("Domain_key %s" % domain_key)
    def txn():
        # Actual test in the datastore to see if the task is atomic,
        # as it is a computed property.
        query = Task.all().\
            ancestor(domain_key).\
            filter('parent_task =', task_key)
        subtask = query.get()
        if not subtask:         # atomic
            workers.UpdateTaskCompletion.enqueue(task.domain_identifier(),
                                                 task.identifier())
    db.run_in_transaction(txn)


def migrate_user(user):
    if not 'sps' in user.domains:
        user.domains.append('sps')
    yield op.db.Put(user)

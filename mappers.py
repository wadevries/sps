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
from mapreduce import operation as op, context
from google.appengine.ext import db

from model import Domain, Task, User, TaskIndex
import workers
import api

def clear_all_indices(task):
    """
    Clears all old indices, must be called before all
    tasks indices are rebuild.

    This because to sequence number of all tasks must be
    zeroed before the indices start to rebuild.
    """
    task_index = TaskIndex.get_by_key_name(task.identifier(),
                                           parent=task)
    if task_index:
        db.delete(task_index)
    task.assignee_index_sequence = 0
    yield op.db.Put(task)

def rebuild_indices(task):
    """
    Rebuilds all TaskIndices. The old indices MUST be cleared first.
    """

    def txn():
        workers.UpdateTaskIndex.queue_worker(task.domain_identifier(),
                                             task.identifier(),
                                             transactional=True)
        instance = api.get_task(task.domain_identifier(),
                                task.identifier())
        if instance:
            workers.UpdateAssigneeIndex.queue_worker(
                instance,
                add_assignee=instance.assignee_identifier())
    db.run_in_transaction(txn)


def migrate_user(user):
    if not 'sps' in user.domains:
        user.domains.append('sps')
    yield op.db.Put(user)

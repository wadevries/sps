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

from model import Domain, Task, User, AssigneeIndex
import workers
import api

def clear_assignee_index(task):
    index = AssigneeIndex.get_by_key_name(task.identifier(),
                                          parent=task)
    if index:
        db.delete(index)
    task.assignee_index_sequence = 0
    yield op.db.Put(task)


def migrate_task(task):
    def txn():
        index = AssigneeIndex.get_by_key_name(task.identifier(),
                                              parent=task)
        if index:
            return
        instance = api.get_task(task.domain_identifier(),
                                task.identifier())
        if instance.assignee_key():
            workers.UpdateAssigneeIndex.queue_worker(
                instance,
                add_assignee=instance.assignee_identifier())
    db.run_in_transaction(txn)


def migrate_user(user):
    if not 'sps' in user.domains:
        user.domains.append('sps')
    yield op.db.Put(user)

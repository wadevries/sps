from mapreduce import operation as op, context

from model import Domain, Task, User
import workers

def migrate_task(task):
    # create indices for each task
    if task.root():
        workers.UpdateTaskIndex.queue_task(task.domain_identifier(),
                                           task.identifier())


def migrate_user(user):
    if not 'sps' in user.domains:
        user.domains.append('sps')
    yield op.db.Put(user)

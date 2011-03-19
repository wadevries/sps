from mapreduce import operation as op, context

from model import Domain, Task, User

def migrate_task(task):
    new_task = Task(parent=Domain.key_from_name('sps'),
                    description=task.description,
                    user=task.user,
                    assignee=task.assignee,
                    completed=task.completed)
    yield op.db.Put(new_task)

def migrate_user(user):
    if not 'sps' in user.domains:
        user.domains.append('sps')
    yield op.db.Put(user)

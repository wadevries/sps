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
All functions that are used to interface with the system. At some
point, these functions have to be transformed to a JSON-based api, but
for now just return python objects. The transformation to JSON should
be pretty straightforward.
"""
from google.appengine.ext import db

from model import Domain, Task, Context, User

import re

VALID_DOMAIN_IDENTIFIER = r'[a-z][a-z0-9-]{1,100}'



def member_of_domain(domain, user, *args):
    """Returns true iff all the users are members of the domain.

    Args:
        domain: The domain key name.
        user: Instance of the User model class
        *args: Instances of the User model class

    Returns:
        True if all the users are members of the domain.
    """
    if not domain in user.domains:
        return False
    for other in args:
        if not domain in other.domains:
            return False
    return True


def get_task(domain, task):
    """Gets a task in a domain.

    Args:
        domain: The domain key name
        task: The task key id or name. Can either be an int
            or a string.

    Returns:
        A task instance or None if no task exists.
    """
    domain_key = Domain.key_from_name(domain)
    try:
        task_id = int(task)
        return Task.get_by_id(task_id, parent=domain_key)
    except (ValueError):
        return Task.get_by_name(task, parent=domain_key)


def create_task(domain, user, description, assignee=None):
    """Create and store a task in the Datastore.

    The task will be stored in the specified domain. The user
    must be a member of the domain to create the task.

    Args:
        domain: The key name of the domain in which the task is created.
        user: The User model instance of the user that creates the task.
        description: The task description. Must be a non-empty string.
        assignee: The user model instance of the user to whom this task is
            assigned. The assignee must be in the same domain as the user.
            A value of None indicates no assignee for this task.

    Returns:
        The model instance of the created task. If the user is not
        a member of domain, then the task will not be created and
        None will be returned.

    Raises:
        ValueError: The |assignee| and |user| domain do not match.
    """
    if assignee and not member_of_domain(domain, user, assignee):
        raise ValueError("Assignee and user domain do not match")
    task = Task(parent=Domain.key_from_name(domain),
                description=description,
                user=user,
                assignee=assignee,
                context=user.default_context_key())
    task.put()
    return task


def assign_task(domain, user, task, assignee):
    """Assign |task| to assignee.

    Sets the assignee property of |task|. |user| is the user
    performing the operation. The assignment will only succeed if one
    of the following conditions is true:
    - The |user| is the current assignee of the task. Assignees can
      change the assignee of their tasks.
    - The |task| does not have an assignee yet and the user assigns the
      task to himself (assignee == user).
    - The |user| has admin rights. Admins can always change the assignee.

    Args:
        domain: The domain identifier
        user: A User model instance of the user performing the assignment
            operation.
        task: A Task model instance of the task whose assignee is being set.
        assignee: A User model instance of the future assignee of the task.

    Returns:
        The task instance. The assignee will be set and the task instance
        is stored in the datastore.

    Raises:
        ValueError: If none of the above conditions are met or if
            the user and assignee are not in the same domain.
    """
    if not member_of_domain(domain, user, assignee):
        raise ValueError("Assignee and user domain do not match")

    def can_assign():
        if not task.assignee_key() and user.key() == assignee.key():
            return True
        if user.key() == task.assignee_key():
            return True
        if user.admin:
            return True
        return False

    if not can_assign():
        raise ValueError("Cannot assign")
    task.assignee = assignee
    task.put()
    return task


def set_task_completed(domain, user, task, completed):
    """Sets the completion status of a task.

    A task can only be set to completed if |user| is the assignee of
    task, or an admin. The updated task will be stored in the
    datstore.

    Args:
        domain: The domain identifier string
        user: An instance of the User model
        task: The task id or key name
        completed: The new value of the completed property of the task

    Returns:
        An instance of the Task model if setting the property was
        succesful.

    Raises:
        ValueError: The task does not exist or the user is not the
            assignee of the task.
    """
    task_instance = get_task(domain, task)
    if (not task_instance or
        not task_instance.assignee_key() == user.key()):
        raise ValueError("Invalid task or user rights")
    import logging
    task_instance.completed = completed
    task_instance.put()
    return task


def create_domain(domain, domain_title, user):
    """Creates a new domain, if none already exists with that identifier.

    The user will become an admin on the newly created domain, and the
    domain will be added to the list of domains of the user. The updates
    will be stored in the datastore.

    Args:
        domain: The domain identifier of the new domain. Must be a lowercase
            alphanumeric string of length less than 100. The identifier
            must match the VALID_DOMAIN_IDENTIFIER regexp.
        domain_title: The string title of the new domain. The string must
            be non-empty.
        user: Instance of the User model that creates the domain.

    Returns:
        The newly created Domain instance. |user| will be set as
        admin of the new domain. Returns None if a domain already
        exists with that identifier, the identifier is not valid or
        the domain_title is empty.
    """
    domain_title = domain_title.splitlines()[0].strip()
    if (not re.match(VALID_DOMAIN_IDENTIFIER, domain) or
        not domain_title):
        return None
    existing = Domain.get_by_key_name(domain)
    if existing:
        return None
    new_domain = Domain(key_name=domain,
                        name=domain_title,
                        admins=[user.key().name()])
    new_domain.put()
    def txn(user_key):
        txn_user = User.get(user_key)
        txn_user.domains.append(domain)
        txn_user.put()
    db.run_in_transaction(txn, user.key())
    return new_domain


def get_all_open_tasks(domain):
    """
    Returns all tasks from |domain| that are not yet completed and not
    assigned to anyone.

    Args:
        domain: The domain identifier string

    Returns:
        A list of Task model instances that are not yet
        completed and do not have an assignee. The tasks will be ordered
        by their time, with the oldest tasks first.
    """
    query = Task.all().ancestor(Domain.key_from_name(domain)).\
        filter('completed =', False).\
        filter('assignee =', None).\
        order('-time')
    return query.fetch(50)


def get_all_assigned_tasks(domain, user):
    """Returns all tasks that are assigned to |user| in |domain|.

    Args:
        domain: The domain identifier string
        user: An instance of the User model.

    Returns:
        A list of tasks instances that the given |user| is the assignee for.
        At most 50 instances will be returned. The order will be on completion
        status, with uncompleted tasks first. A secondary order is on time,
        with newest tasks first.
    """
    query = user.assigned_tasks.ancestor(Domain.key_from_name(domain)).\
        order("completed").\
        order("-time")
    return query.fetch(50)


def get_all_tasks(domain):
    """Returns all the tasks in the |domain|.

    Args:
        domain: The domain identifier string

    Returns:
        A list of at most 50 task instances of |domain|, ordered on task
        creation time, with the newest task first.
    """
    query = Task.all().ancestor(Domain.key_from_name(domain)).\
        order('-time')
    return query.fetch(50)

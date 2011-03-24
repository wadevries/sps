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
import re
from google.appengine.ext import db
from google.appengine.api import users
from model import Domain, Task, TaskIndex, Context, User
import workers

VALID_DOMAIN_IDENTIFIER = r'[a-z][a-z0-9-]{1,100}'


def member_of_domain(domain, user, *args):
    """Returns true iff all the users are members of the domain.

    Args:
        domain: The domain identifier
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


def get_user():
    """Gets the currently logged in user.

    The login is based on the GAE user system, but Users are stored as
    separate entities. If the user does not have an entity, one will
    be created using the information in his Google account.

    Returns:
        An instance of the User model, or None if the user is not
        logged in.
    """
    guser = users.get_current_user()
    if not guser:
        return None
    user = User.get_by_key_name(guser.user_id())
    if not user:
        user = User(key_name=guser.user_id(), name=guser.nickname())
        user.put()
    return user

def get_user_from_identifier(user_identifier):
    """Returns the user corresponding to the given identifier"""
    # TODO(tijmen): Name this get_user and rename the current
    # function to something like get_logged_in_user().
    return User.get_by_key_name(user_identifier)


def get_and_validate_user(domain_identifier):
    """Gets the currently logged in user and validates if he
    is a member of the domain.

    If the user is not logged in, or is not a member of the domain
    with |domain_identifier|, then None will be returned.

    Args:
        domain_identifier: The domain identifier string.

    Returns:
        An instance of the User model, or None if the user
        is not logged in or not a member of the domain.
    """
    user = get_user()
    if not user or not member_of_domain(domain_identifier, user):
        return None
    return user


def get_domain(domain_identifier):
    """
    Returns the Domain model instance corresponding to the identifier.

    Args:
        domain_identifier: The domain identifier string

    Returns:
        An instance of the Domain model, or None if no domain exist
        with the given identifier.
    """
    return Domain.get_by_key_name(domain_identifier)


def get_all_domains_for_user(user):
    """
    Returns a list with domain instances of the domains that
    the given user is a member of.

    Args:
        user: An instance of the User model

    Returns:
        A list of Domain model instances.
    """
    keys = [db.Key.from_path('Domain', domain)
            for domain in user.domains]
    return Domain.get(keys)


def get_task(domain, task):
    """Gets a task in a domain.

    Args:
        domain: The domain identifier
        task: The task key id or name. Can either be an int
            or a string.

    Returns:
        A task instance or None if no task exists.
    """
    domain_key = Domain.key_from_name(domain)
    try:
        task_id = int(task)
        return Task.get_by_id(task_id, parent=domain_key)
    except ValueError:
        return Task.get_by_key_name(task, parent=domain_key)


def can_complete_task(task, user):
    """Returns true if the task can be completed by the user.

    Task can only be completed if the user is the assignee and
    the task is an atomic task. Composite tasks are automatically
    completed when all its subtasks are completed.

    Args:
        task: An instance of the Task model
        user: An instance of the User model

    Returns:
        True if the user can set the task to completed.
    """
    return task.atomic() and task.assignee_key() == user.key()


def can_assign_task(task, user, assignee):
    """Checks whether a user can assign the task to assignee.

    A task can only be assigned if the task is an atomic task and
    one of the following conditions is true:
    - The |user| is the current assignee of the task. Assignees
    can change the assignee of their tasks.
    - The |task| does not have an assignee yet and the user assigns
    the task to himself (assignee == user).
    - The |user| has admin rights. Admins can always change the assignee.

    Args:
        task: A Task model instance
        user: A User model instance
        assignee: A User model instance, or None

    Returns:
        True if user is allowed to set the assignee of the task to the
        given assignee and the task is atomic.

    Raises:
        ValueError: If the user and assignee are not both members of
            the domain of the task.
    """
    domain_identifier = task.domain_identifier()
    if not member_of_domain(domain_identifier, user, assignee):
        raise ValueError("User and assignee not in the same domain")

    if not task.atomic():
        return False
    if user.identifier() == task.assignee_identifier():
        return True
    if not task.assignee_key() and user.identifier() == assignee.identifier():
        return True
    if user.admin:
        # TOOD(tijmen): Old admin code, change
        return True
    return False


def create_task(domain, user, description, assignee=None, parent_task=None):
    """Create and store a task in the Datastore.

    The task will be stored in the specified domain. The user must be
    a member of the domain to create the task. If a |parent_task| is
    specified, the new task will be added as subtask of that task. All
    tasks in the task hierarchy will also be updated.

    Args:
        domain: The key name of the domain in which the task is created.
        user: The User model instance of the user that creates the task.
        description: The task description. Must be a non-empty string.
        assignee: The user model instance of the user to whom this task is
            assigned. The assignee must be in the same domain as the user.
            A value of None indicates no assignee for this task.
        parent_task: The task identifier of the optional parent task.

    Returns:
        The model instance of the newly created task.

    Raises:
        ValueError: The |assignee| and |user| domain do not match or
            the user is not a member of domain.
        ValueError: The parent task does not exist.
    """
    if not member_of_domain(domain, user):
        raise ValueError("User '%s' not a member of domain '%s'" %
                         (user.name, domain))
    if assignee and not member_of_domain(domain, user, assignee):
        raise ValueError("Assignee and user domain do not match")

    def txn():
        super_task = None
        if parent_task:
            super_task = get_task(domain, parent_task)
            if not super_task:
                raise ValueError("Parent task does not exist")
        task = Task(parent=Domain.key_from_name(domain),
                    description=description,
                    user=user,
                    context=user.default_context_key(),
                    parent_task=super_task,
                    level=super_task.level + 1 if super_task else 0)
        if super_task:
            super_task.number_of_subtasks = super_task.number_of_subtasks + 1
            super_task.increment_incomplete_subtasks()
            super_task.put()
        task.put()
        return task

    task = db.run_in_transaction(txn)
    workers.UpdateTaskIndex.queue_task(domain, task.identifier())
    if assignee:
        assign_task(domain, task.identifier(), user, user)
    return task


def assign_task(domain_identifier, task_identifier, user, assignee):
    """Assigns a task to an assignee.

    Sets the assignee property of task. user is the user performing
    the operation. The assignment will only succeed if the user is
    allowed to perform the operation, which can be checked beforehand
    through can_assign_task().

    Args:
        domain_identifier: The domain identifier string
        task_identifier: The task identifier of the task that is assigned
        user: An instance of the User model that is performing the
            assignment operation.
        assignee: An instance of the User model to whom the task is
            assigned to.

    Returns:
        The task instance. The assignee will be set and the task instance
        is stored in the datastore.

    Raises:
        ValueError: If the assignment operation is invalid, or if the
            task does not exist.
    """
    def txn():
        task = get_task(domain_identifier, task_identifier)
        if not task:
            raise ValueError("Task does not exist")
        if not can_assign_task(task, user, assignee):
            raise ValueError("Cannot assign")

        previous_assignee = task.assignee_identifier()
        task.assignee = assignee
        workers.UpdateAssigneeIndex.queue_worker(
            task,
            add_assignee=assignee.identifier(),
            remove_assignee=previous_assignee)
        task.put()
        return task

    return db.run_in_transaction(txn)


def set_task_completed(domain, user, task_identifier, completed):
    """Sets the completion status of a task.

    A task can only be set to completed if |user| is the assignee of
    the task and if the task is an atomic task. This function will
    also propagate the complete status up the task hierarchy.

    Args:
        domain: The domain identifier string
        user: An instance of the User model
        task: The task identifier
        completed: The new value of the completed property of the task

    Returns:
        An instance of the Task model if setting the property was
        succesful.

    Raises:
        ValueError: The task does not exist or the user is not the
            assignee of the task.
    """
    def txn():
        task = get_task(domain, task_identifier)
        if not task or not task.atomic() or not can_complete_task(task, user):
            raise ValueError("Invalid task")

        if not task.completed ^ completed:
            return task # no changes

        task.completed = completed
        task.put()
        parent_task = task.parent_task
        while parent_task:
            propagate = False
            if completed:
                parent_task.decrement_incomplete_subtasks()
                propagate = parent_task.completed
            else:              # Task went from complete to incomplete
                parent_completed = parent_task.completed
                parent_task.increment_incomplete_subtasks()
                propagate = parent_task.completed ^ parent_completed
            parent_task.put()
            parent_task = parent_task.parent_task if propagate else None
        return task

    return db.run_in_transaction(txn)


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
        if not domain in txn_user.domains:
            txn_user.domains.append(domain)
            txn_user.put()
    db.run_in_transaction(txn, user.key())
    return new_domain


def _group_tasks(tasks, complete_hierarchy=False, domain=None):
    """
    Reorders the list of tasks such that supertasks are listed before
    their subtasks.

    The original order is retained as much as possible while still
    satisfying the above listed constraint.

    If complete_hierarchy is set to true, then tasks are fetched from
    the datastore are made to fill in the blanks, all the way up the
    hierarchy. The function must also be run in the same transaction
    as where the input tasks are fetched to get consistent results.

    Args:
        tasks: A list of Task model instances
        complete_hierarchy: If set to True, then the parent tasks will
            be fetched to complete the hierarchy.
        domain: The domain identifier string. Required if
            complete_hierarchy is set to True.

    Returns:
        A list of Task model instances, ordered such that supertasks are
        before their subtasks.

    Raises:
        ValueError: If complete_hierarchy is enabled but not domain
            identifier is specified.
    """
    if complete_hierarchy and not domain:
        raise ValueError("Domain identifier is required")

    index = dict([(task.identifier(), task) for task in tasks])
    trees = {}                  # Index of all tree nodes, by task
    # The output of the algorithm, all the tree root nodes
    roots = []

    class _Tree(object):
        """Very basic n-tree node"""
        def __init__(self, value, parent=None):
            self.value = value
            self.parent = parent
            if self.parent:
                self.parent.children.append(self)
            self.children = []

        def pre_order(self, output):
            output.append(self.value)
            for child in self.children:
                child.pre_order(output)


    def fetch_tree(task_identifier):
        if not task_identifier:
            return None

        task = index.get(task_identifier)
        if not task and complete_hierarchy:
            task = get_task(domain, task_identifier)
            if task:
                index[task_identifier] = task
        if not task:
            return None

        tree = trees.get(task)
        if not tree:
            parent_tree = fetch_tree(task.parent_task_identifier())
            tree = _Tree(task, parent=parent_tree)
            if not parent_tree:
                roots.append(tree)
        trees[task] = tree
        return tree

    for task in tasks:
        fetch_tree(task.identifier())
    output = []
    for root in roots:
        root.pre_order(output)
    return output


def get_all_subtasks(domain, task, limit=50, depth_limit=None):
    """
    Returns a list of all subtasks of the given task, in the order
    as a pre-order traversal through the task hierarchy.

    This function will perform one query for each level of the subtask
    hierarchy.

    Args:
        domain: The domain identifier string.
        task: An instance of the Task model.
        limit: The maximum number of subtasks to return.
        depth_limit: The maximum depth of subtasks in the task
            hierarchy.

    Returns:
        A list with all subtasks of the given task.

    Raises:
        ValueError: The depth_limit or limit are not positive integers
    """
    if not depth_limit:
        #  ListProperties cannot contain more than 5000 elements anyway
        depth_limit = 5000
    if depth_limit < 0 or limit < 0:
        raise ValueError("Invalid limits")

    task_level = task.level
    tasks = []
    for depth in range(depth_limit):
        query = TaskIndex.all(keys_only=True).\
            ancestor(Domain.key_from_name(domain)).\
            filter('level = ', task_level + depth + 1).\
            filter('hierarchy = ', task.identifier())
        fetched = query.fetch(limit)
        tasks.extend(Task.get([key.parent() for key in fetched]))
        limit = limit - len(fetched)
        if not fetched or limit < 1:
            break               # stop

    # Sort the tasks on completion status and then on time, as this is
    # not possible in the query.
    def task_cmp(t1, t2):
        if t1.completed != t2.completed:
            return cmp(t1.completed, t2.completed)
        return -cmp(t1.time, t2.time)

    tasks.sort(cmp=task_cmp)
    return _group_tasks(tasks)


def get_all_open_tasks(domain):
    """
    Returns all tasks from |domain| that are not yet completed and not
    assigned to anyone.

    Args:
        domain: The domain identifier string

    Returns:
        A list of Task model instances that are not yet completed
        and do not have an assignee. The tasks will be ordered on
        hierarchy.
    """
    def txn():
        query = Task.all().ancestor(Domain.key_from_name(domain)).\
            filter('completed =', False).\
            filter('assignee =', None).\
            order('-time')
        return _group_tasks(query.fetch(50),
                            complete_hierarchy=True,
                            domain=domain)
    return db.run_in_transaction(txn)


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
    def txn():
        query = user.assigned_tasks.ancestor(Domain.key_from_name(domain)).\
            order('completed').\
            order('-time')
        return _group_tasks(query.fetch(50),
                            complete_hierarchy=True,
                            domain=domain)
    return db.run_in_transaction(txn)


def get_all_tasks(domain):
    """Returns all the tasks in the |domain|.

    Args:
        domain: The domain identifier string

    Returns:
        A list of at most 50 task instances of |domain|, ordered on task
        creation time, with the newest task first.
    """
    def txn():
        query = Task.all().ancestor(Domain.key_from_name(domain)).\
            order('-time')
        return _group_tasks(query.fetch(50),
                            complete_hierarchy=True,
                            domain=domain)
    return db.run_in_transaction(txn)

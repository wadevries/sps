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
import logging
from google.appengine.ext import db
from google.appengine.api import users
from model import Domain, Task, TaskIndex, Context, User
import workers

VALID_DOMAIN_IDENTIFIER = r'[a-z][a-z0-9-]{1,100}'

# A mapping of identifiers to their task instance
_TASK_MEMORY_STORE = {}

def _get_task_from_memory(domain_identifier, task_identifier):
    """
    This function operates the same as get_task() except it will first
    look if the task instance is already stored in memory. This is
    used to operate on the same set of task instances during a
    transaction, as writes are not reflected during a transaction.

    At the start of a transaction, the memory store should be flushed
    to ensure there are no stale results through a call to
    _flush_tasks_from_memory().

    Args:
        domain_identifier: The domain identifier string
        task_identifier: The task identifier. Can be None.

    Returns:
        An instance of the Task model, or None if no such task exists
        or the provided task_identifier was None.
    """
    if not task_identifier:
        return None
    # TODO(tijmen): When new tasks are created, they are not saved in
    # memory.
    key = "%s/%s" % (domain_identifier, task_identifier)
    task = _TASK_MEMORY_STORE.get(key, None)
    if not task:
        task = get_task(domain_identifier, task_identifier)
        _TASK_MEMORY_STORE[key] = task
    return task


def _flush_tasks_from_memory():
    """ Flush all stored tasks in memory. Call before or at the start of a
    transaction that operates on the task hierarchy."""
    _TASK_MEMORY_STORE.clear()


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


def admin_of_domain(domain_identifier, user):
    """Returns true iff the user is a member and admin of the domain.

    Args:
        domain: The domain identifier
        user: Instance of the user model

    Returns:
        True if the user is a member and an admin of the domain.
    """
    if not member_of_domain(domain_identifier, user):
        return False
    query = Domain.all(keys_only=True).\
        filter('__key__ =', Domain.key_from_name(domain_identifier)).\
        filter('admins =', user.identifier())
    if not query.fetch(1):
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


def can_assign_to_self(task, user):
    """Returns true if a user can assign the task to himself.

    Args:
        task: An instance of the Task model
        user: A User model instance

    Returns:
        True if the user can assign the task to himself. If the user
        is already assigned to the task, then this function will
        return false.
    """
    if not task.atomic() or task.assignee_identifier():
        return False
    return can_assign_task(task, user, user)


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
        _flush_tasks_from_memory()
        super_task = _get_task_from_memory(domain, parent_task)
        task = Task(parent=Domain.key_from_name(domain),
                    description=description,
                    user=user,
                    context=user.default_context_key(),
                    parent_task=super_task,
                    level=super_task.level + 1 if super_task else 0)
        if super_task:
            completed = super_task.completed
            super_task.number_of_subtasks = super_task.number_of_subtasks + 1
            super_task.increment_incomplete_subtasks()
            super_task.put()
            if completed ^ super_task.completed:
                _propagate_completion(super_task)
        if assignee:
            task.baked_assignee_description = assignee.name
        task.put()
        workers.UpdateTaskIndex.queue_worker(domain,
                                             task.identifier(),
                                             transactional=True)
        return task

    task = db.run_in_transaction(txn)
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


def _propagate_completion(task):
    """
    Propagate the effects of the completed field of the given task
    to the parent tasks in the hierarchy.

    If the task is set to complete, then this function will decrement
    the number of incomplete subtasks of the parents and set their
    completed status accordingly. Conversely, if the task is set to
    incomplete, then this will set all parent tasks to incomplete as
    well.

    Note that care must be taken not to call this function more than
    once every change, or the subtask count will become invalid. As
    such, this function must be run in a transaction.

    This function reads all tasks from the memory store, so ensure
    that its contents are correct before calling this function.

    Args:
        task: An instance of the Task model

    Raises:
        ValueError: When not called as part of a transaction
    """
    if not db.is_in_transaction():
        raise ValueError("Must be part of a transaction")

    parent_task = _get_task_from_memory(task.domain_identifier(),
                                        task.parent_task_identifier())
    completed = task.completed
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
        parent_task = _get_task_from_memory(
            parent_task.domain_identifier(),
            parent_task.parent_task_identifier()) if propagate else None


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
        _flush_tasks_from_memory()
        task = _get_task_from_memory(domain, task_identifier)
        if not task or not task.atomic() or not can_complete_task(task, user):
            raise ValueError("Invalid task")

        if not task.completed ^ completed:
            return task # no changes

        task.completed = completed
        task.put()
        _propagate_completion(task)
        return task

    return db.run_in_transaction(txn)


def _check_for_cycle(task, new_parent):
    """
    Check if assigning new_parent as the parent of task would result
    in a cycle. This function must be run as part of a transaction
    to get consistent results.

    Args:
        task: An instance of the Task model
        new_parent: An instance of the Task model, or None, in which
            case the function will always return True.

    Returns:
        False if the assignment would result in a cycle. True if the
        assignment is allowed.

    Raises:
        ValueError: If used outside of a transaction or the tasks
            are not in the same domain.
    """
    if not task.domain_identifier() == new_parent.domain_identifier():
        raise ValueError("Tasks must be in the same domain")
    if not db.is_in_transaction():
        raise ValueError("Must be part of a transaction")
    visited = set([task.identifier()])
    while new_parent:
        if new_parent.identifier() in visited:
            return False
        visited.add(new_parent.identifier())
        parent_identifier = new_parent.parent_task_identifier()
        if parent_identifier:
            new_parent = _get_task_from_memory(task.domain_identifier(),
                                               parent_identifier)
        else:
            new_parent = None
    return True

def _update_task_levels(task, level):
    """
    Updates all level property of the given task and propagates this
    to all subtasks in the hierarchy as wel. The updated tasks will be
    stored in the datastore.

    This function has to be called as part of a transaction.

    Args:
        task: An instance of the Task model
        level: The new level of the task

    Raises:
        ValueError: If not called as part of a transaction
    """
    if not db.is_in_transaction():
        raise ValueError("Must be part of a transaction")
    if task.level == level:
        return

    difference = level - task.level
    task.level = level
    task.put()
    query = TaskIndex.all(keys_only=True).\
        ancestor(task.domain_key()).\
        filter('hierarchy = ', task.identifier())

    subtasks = Task.get(key.parent() for key in query)
    for subtask in subtasks:
        subtask.level = subtask.level + difference
    db.put(subtasks)


def change_task_parent(domain_identifier,
                       user,
                       task_identifier,
                       new_parent_identifier):
    """Changes the parent of the given task.

    Changes the parent of the task to new_parent. The operation can
    only be performed by the user who originally created the task. No
    cycles can be created in this way. If the operation succeeds, then
    the TaskIndex and AssigneeIndex will be updated.

    Args:
        domain_identifier: The domain identifier string
        user: An instance of the User model
        task_identifier: An identifier of the task that will have its
            parent changed
        new_parent_identifier: The identifier for the new parent.
            Can be None, in which case the task will end up as a root task.

    Returns:
        An instance of the Task model, which is the task with his
        new parent.

    Raises:
        ValueError: The task does not exist, or the user is not
        allowed to change the task.
    """
    if not member_of_domain(domain_identifier, user):
        raise ValueError("User is not a member of the domain")

    user_is_admin = admin_of_domain(domain_identifier, user)

    def remove_task_from_parent(task, parent):
        parent.number_of_subtasks = parent.number_of_subtasks - 1
        if not task.completed:
            parent.decrement_incomplete_subtasks()
        parent_was_completed = parent.completed
        if parent.completed and parent.number_of_subtasks == 0:
            # Removed the last subtask that made the parent completed
            parent.completed = False
        if (not parent.completed
            and parent.remaining_subtasks == 0
            and parent.number_of_subtasks > 0):
            # Removed the only subtask that was not completed
            parent.completed = True
        if parent.completed ^ parent_was_completed:
            _propagate_completion(parent)

    def add_task_to_parent(task, parent):
        parent.number_of_subtasks = parent.number_of_subtasks + 1
        parent_was_completed = parent.completed
        if not task.completed:
            parent.increment_incomplete_subtasks()
        if parent.remaining_subtasks == 0:
            parent.completed = True
        if parent.completed ^ parent_was_completed:
            _propagate_completion(parent)

    def txn():
        _flush_tasks_from_memory()
        task = _get_task_from_memory(domain_identifier,
                                     task_identifier)
        new_parent = _get_task_from_memory(domain_identifier,
                                           new_parent_identifier)
        if (not task.user_identifier() == user.identifier()
            and not user_is_admin):
            raise ValueError("User did not create task")
        if not _check_for_cycle(task, new_parent):
            raise ValueError("Cycle detected")

        parent = _get_task_from_memory(domain_identifier,
                                       task.parent_task_identifier())
        if parent:
            remove_task_from_parent(task, parent)
            workers.UpdateAssigneeIndex.queue_worker(
                parent,
                remove_assignee=task.assignee_identifier())
            parent.put()

        task.parent_task = new_parent
        level = 0
        if new_parent:
            add_task_to_parent(task, new_parent)
            level = new_parent.level + 1
            new_parent.put()
            workers.UpdateAssigneeIndex.queue_worker(
                new_parent,
                add_assignee=task.assignee_identifier())
        _update_task_levels(task, level)
        workers.UpdateTaskIndex.queue_worker(domain_identifier,
                                             task.identifier(),
                                             transactional=True)
        task.put()
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


def _group_tasks(tasks,
                 complete_hierarchy=False,
                 domain=None,
                 min_task_level=0):
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
        min_task_level: The minimum level of the tasks that will be
            returned as part of the hierarchy. Tasks with a level lower
            than this level will not be returned, nor fetched when
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
        if not task or task.level < min_task_level:
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


def _sort_tasks(tasks):
    """
    Sorts the list of Task instances, in place, on their completion
    state and then on time, newest tasks first.

    Args:
        tasks: A list of Task model instances

    Returns:
        Nothing. The list is sorted in place.
    """
    def task_cmp(t1, t2):
        if t1.completed != t2.completed:
            return cmp(t1.completed, t2.completed)
        return -cmp(t1.time, t2.time)

    tasks.sort(cmp=task_cmp)


def get_all_subtasks(task, limit=50, depth_limit=None):
    """
    Returns a list of all subtasks of the given task, in the order
    as a pre-order traversal through the task hierarchy.

    This function will perform one query for each level of the subtask
    hierarchy.

    Args:
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
            ancestor(Domain.key_from_name(task.domain_identifier())).\
            filter('level = ', task_level + depth + 1).\
            filter('hierarchy = ', task.identifier())
        fetched = query.fetch(limit)
        tasks.extend(Task.get([key.parent() for key in fetched]))
        limit = limit - len(fetched)
        if not fetched or limit < 1:
            break               # stop

    _sort_tasks(tasks)
    return _group_tasks(tasks)

def get_open_subtasks(task, limit=50, depth_limit=None):
    """
    Returns all subtasks of task that are not yet completed and not
    assigned to anyone.

    Args:
        task: An instance of the Task model.
        limit: The maximum number of subtasks to return.
        depth_limit: The maximum depth of subtasks in the task
            hierarchy.

    Returns:
        A list of Task model instances that are not yet completed
        and do not have an assignee. All tasks are subtasks of the
        given tasks.
    """
    if not depth_limit:
        depth_limit = 5000
    if depth_limit < 0 or limit < 0:
        raise ValueError("Invalid limits")

    # As open tasks cannot be queried yet through the TaskIndex,
    # all tasks are retrieved and then filtered in memory.
    task_level = task.level
    tasks = []
    for depth in range(depth_limit):
        query = TaskIndex.all(keys_only=True).\
            ancestor(Domain.key_from_name(task.domain_identifier())).\
            filter('level = ', task_level + depth + 1).\
            filter('hierarchy = ', task.identifier())
        fetched = query.fetch(limit)
        tasks.extend(Task.get([key.parent() for key in fetched]))
        limit = limit - len(fetched)
        if not fetched or limit < 1:
            break               # stop
    tasks = [task for task in tasks
             if task.assignee == None and not task.completed]
    _sort_tasks(tasks)
    grouped = _group_tasks(tasks,
                           complete_hierarchy=True,
                           min_task_level=task_level + 1,
                           domain=task.domain_identifier())
    return grouped

def get_assigned_subtasks(task, user, limit=50, depth_limit=None):
    """
    Returns a list of all subtasks of the given task, that are assigned
    to the given user.

    This function will perform one query for each level of the subtask
    hierarchy.

    Args:
        task: An instance of the Task model.
        user: An instance of the user model.
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
            ancestor(Domain.key_from_name(task.domain_identifier())).\
            filter('level = ', task_level + depth + 1).\
            filter('hierarchy = ', task.identifier()).\
            filter('assignees = ', user.identifier())
        fetched = query.fetch(limit)
        tasks.extend(Task.get([key.parent() for key in fetched]))
        limit = limit - len(fetched)
        if not fetched or limit < 1:
            break               # stop

    _sort_tasks(tasks)
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
            filter('number_of_subtasks =', 0).\
            filter('completed =', False).\
            filter('assignee =', None).\
            order('-time')
        return _group_tasks(query.fetch(50),
                            complete_hierarchy=True,
                            domain=domain)
    return db.run_in_transaction(txn)


def get_all_assigned_tasks(domain, user, limit=50):
    """Returns all top level tasks, which has a subtasks that is assigned
    to |user| in |domain|. The subtask can be arbitrarily deep in the
    hierarchy.

    Args:
        domain: The domain identifier string
        user: An instance of the User model
        limit: The maximum number of results that will be returned

    Returns:
        A list of tasks instances that the given |user| is the assignee for
        and has not yet completed. The tasks will returned in the order
        of the hierarchy, and then on time, with newest tasks first.
    """
    query = TaskIndex.all(keys_only=True).\
        ancestor(Domain.key_from_name(domain)).\
        filter('level = ', 0).\
        filter('assignees = ', user.identifier())
    fetched = query.fetch(limit)
    tasks = (Task.get([key.parent() for key in fetched]))

    def task_cmp(t1, t2):
        if t1.completed != t2.completed:
            return cmp(t1.completed, t2.completed)
        return -cmp(t1.time, t2.time)

    tasks.sort(cmp=task_cmp)
    return tasks


def get_all_tasks(domain, limit=50):
    """Returns all the top level tasks in the |domain|.

    Args:
        domain: The domain identifier string
        limit: The maximum number of tasks that will be returned.

    Returns:
        A list of at most limit task instances of |domain|, ordered on task
        creation time, with the newest task first.
    """
    query = TaskIndex.all(keys_only=True).\
        ancestor(Domain.key_from_name(domain)).\
        filter('level = ', 0)
    fetched = query.fetch(limit)
    tasks = (Task.get([key.parent() for key in fetched]))

    _sort_tasks(tasks)
    return tasks

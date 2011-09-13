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
Model classes used in the planner.
"""

from google.appengine.ext import db
import simplejson as json
import aetycoon

class Domain(db.Model):
    """
    The top level entity that is used as a parent entity of all Tasks
    and Contexes for transaction support. Not really used in any other
    way at the moment, although the name could be used as some sort of
    title.
    """
    name = db.StringProperty(required=True)
    # The key names of all users that have 'admin' rights in this
    # domain. The user who creates a domain becomes its admin by
    # default, others have to be added later
    admins = db.ListProperty(str, default=[])

    @staticmethod
    def key_from_name(domain_identifier):
        """
        Returns the datastore key of the domain entity with the given
        identifier. It is not checked if the entity actually exists.

        Returns:
            An instance of db.Key pointing to a Domain entity.
        """
        return db.Key.from_path('Domain', domain_identifier)

    def identifier(self):
        """Returns a string identifier for this domain."""
        return self.key().name()


class Context(db.Model):
    """
    A context is a second hierarchy structure that serves as a
    'container' for tasks. Contexts are mostly used to designate
    owners/groups that have to finish a certain set of tasks.
    """
    name = db.StringProperty()
    # Parent context. In general, there should be only one context for
    # which this reference is None. This context is then selected as
    # the default for new tasks.
    parent_context = db.SelfReferenceProperty(default=None,
                                              collection_name="sub_contexts")


class User(db.Model):
    """
    Wrapper model around all users in the app. Uses Google accounts.

    The key_name used is the string representation of the identifier
    of the Google account.
    """
    name = db.StringProperty(required=True)
    # Whether the user has admin rights. Admins can edit tasks that
    # are not their own.
    admin = db.BooleanProperty(default=False)
    # A list of all domain identifiers (key names) that this user is
    # a member of.
    domains = db.ListProperty(str, default=[])
    # The default context for new tasks for this user
    default_context = db.ReferenceProperty(reference_class=Context)

    def identifier(self):
        """Returns a string identifier for this user"""
        return self.key().name()

    def default_context_key(self):
        """
        Returns the key of the |default_context| without dereferencing
        the property.
        """
        return User.default_context.get_value_for_datastore(self)


# PicklePropert from the aetycoon repository:
# (https://github.com/Arachnid/aetycoon/blob/master/__init__.py)
class _PickleProperty(db.Property):
  """A property for storing complex objects in the datastore in pickled form.

  Example usage:

  >>> class PickleModel(db.Model):
  ...   data = PickleProperty()

  >>> model = PickleModel()
  >>> model.data = {"foo": "bar"}
  >>> model.data
  {'foo': 'bar'}
  >>> model.put() # doctest: +ELLIPSIS
  datastore_types.Key.from_path(u'PickleModel', ...)

  >>> model2 = PickleModel.all().get()
  >>> model2.data
  {'foo': 'bar'}
  """

  data_type = db.Blob

  def get_value_for_datastore(self, model_instance):
    value = self.__get__(model_instance, model_instance.__class__)
    if value is not None:
      return db.Blob(pickle.dumps(value))

  def make_value_from_datastore(self, value):
    if value is not None:
      return pickle.loads(str(value))

  def default_value(self):
    """If possible, copy the value passed in the default= keyword argument.
    This prevents mutable objects such as dictionaries from being shared across
    instances."""
    return copy.copy(self.default)


class Task(db.Model):
    """
    A record for every task. Tasks can form a hierarchy. Tasks have
    single description. The title of a task is defined as the first
    line of this description.

    All tasks and their related components such as statuses are stored
    in the same entity group, so transactions can be easily used both
    for updating (moving tasks etc) and traversing the data. As tasks
    are all linked through references, a transaction must be used to
    get a consistent view on the data traversing through the
    hierarchy/graph. Using an entity group in this way does limit the
    writes to about 1/sec across the entire system, but in practice
    that should not pose a problem as the application is read
    dominated.

    Tasks do not have a specific keyname, but use the auto-generated
    numeric ids.

    Some properties are replicated in the TaskIndex model. Each
    Task has a corresponding TaskIndex, which is used to perform
    some queries.

    All functions specified in this class do not perform any RPC
    calls, and can be used freely.

    The logic that is used to compute the derived properties is
    located in workers.py.
    """
    #
    # FIXED PROPERTIES
    #
    # Description of the task. The first line of the description
    # is used as the title of the task.
    description = db.TextProperty(required=True)
    # Link to a parent task. Tasks that do not have a parent are all
    # considered to be in the 'backlog'.
    parent_task = db.SelfReferenceProperty(default=None,
                                           collection_name="subtasks")
    # TODO(tijmen): Add statuses

    # The user who created the task.
    user = db.ReferenceProperty(reference_class=User,
                                required=True,
                                collection_name="created_tasks")
    # The user that has been assigned to complete this task. Can be
    # None. If this task is a composite task, then this value can
    # be ignored.
    assignee = db.ReferenceProperty(default=None,
                                    reference_class=User,
                                    collection_name="assigned_tasks")
    context = db.ReferenceProperty(reference_class=Context,
                                   collection_name="tasks")
    # A list of tasks identifiers that this tasks depends on to be
    # completed first.
    dependencies = db.StringListProperty(default=[])
    # Time of creation of the task. Just for reference.
    time = db.DateTimeProperty(auto_now_add=True)
    # The estimated time that this task will take to complete.
    duration = db.TimeProperty()
    # Whether or not the task is completed. This value is set by
    # the user. To get the value of the completed status in the
    # hierarchy, check the derived_completed field.
    completed = db.BooleanProperty(default=False, indexed=False)
    #
    # DERIVED PROPERTIES
    #
    # The derived field of the completed flag. If this task is an
    # atomic task, it is set to the value of |completed|. Otherwise
    # it is set to true iff all subtasks are completed.
    derived_completed = db.BooleanProperty(default=False)
    # The size of the tree hierarchy formed by this tasks and all
    # its supertasks. If the task is an atomic task, the size is 1,
    # otherwise it is the sum of all its subtasks, plus one.
    derived_size = db.IntegerProperty(default=1, indexed=False)
    # The number of direct subtasks of this task. If this count is 0,
    # then this task is an atomic task.
    derived_number_of_subtasks = db.IntegerProperty(default=0)
    # The number of incomplete subtasks of this task. If this
    # value is 0, and this task is a composite task (it has subtasks),
    # then this task is completed.
    derived_remaining_subtasks = db.IntegerProperty(default=0, indexed=False)
    # Level of this task in hierarchy. A task without a parent task
    # has level 0, for all other tasks it is defined as the level as
    # its parent plus one.
    derived_level = db.IntegerProperty(default=0, indexed=False)
    # A dictionary of assignees of this (composite) task.  The
    # assignees of this list form the union of all the assignees of
    # the atomic subtasks of this task.
    #
    # The keys of each entry is the assignee identifier, which is also
    # stored as value in the record.
    #
    # Each assignee consists of a record with the following fields:
    #  id: a string with the identifier of the assignee
    #  completed: an integer describing the number of atomic subtasks
    #     completed by this assignee.
    #  all: an integer describing the total number of atomic subtasks
    #     assigned to this assignee.
    derived_assignees = aetycoon.PickleProperty(default={})

    def identifier(self):
        """Returns a string with the task identifier"""
        return str(self.key().id_or_name())

    def parent_task_key(self):
        """
        Returns the key of the |parent_task| without derefercing the
        property.
        """
        return Task.parent_task.get_value_for_datastore(self)

    def parent_task_identifier(self):
        """
        Returns a string identifier of the parent task of this
        task. If the task has no parent task, then None is
        returned. This function does not fetch from the datastore.
        """
        parent_key = self.parent_task_key()
        if parent_key:
            return str(parent_key.id_or_name())
        else:
            return None

    def domain_key(self):
        """
        Returns the key of the domain parent entity of this task.
        """
        return self.parent_key()

    def domain_identifier(self):
        """
        Returns the domain identifier of the domain of this task.
        """
        return self.parent_key().name()

    def title(self):
        """
        Returns the title of the task.

        The title is the first line in the description.
        """
        title = self.description.split('\r\n', 1)[0].split('\n', 1)[0]
        return title[:-1] if title[-1] == '.' else title

    def description_body(self):
        """
        Returns the body of the description, the part of the
        description that does not include the title.
        """
        parts = self.description.partition('\r\n')
        if parts[2]:
            return parts[2]
        parts = self.description.partition('\n')
        return parts[2]

    def user_key(self):
        """Returns the key of the |user| without dereferencing the property.
        """
        return Task.user.get_value_for_datastore(self)

    def user_identifier(self):
        """Returns the identifier of the user that has created this task."""
        key = self.user_key()
        return key.name()

    def assignee_key(self):
        """
        Returns the key of the |assignee| without dereferencing the property.
        """
        return Task.assignee.get_value_for_datastore(self)

    def assignee_identifier(self):
        """
        Returns the identifier of the assignee of this task. Returns
        None in case there is no assignee. Does not dereference the
        property.
        """
        key = self.assignee_key()
        return key.name() if key else None

    def assignee_description(self):
        """
        Returns a string describing the assignees of this task. If
        this task has no assignees, then this function returns the
        empty string.
        """
        # TODO(tijmen): Make this a proper string for lots of
        # assignees, ie:  tijmen, el and X others..
        return "TEMP"
#        return ','.join(assignee[name] for assignee in self.assignees)

    def is_completed(self):
        """
        Returns true iff this task is completed.
        """
        return self.derived_completed

    def atomic(self):
        """Returns true if this task is an atomic task"""
        return self.derived_number_of_subtasks == 0

    def root(self):
        """Returns true if this task has no parent task"""
        return not self.parent_task_key()

    def open(self):
        """Returns true if this task is an open task."""
        return (self.atomic() and
                not self.is_completed() and
                not self.assignee_identifier())

    def hierarchy_level(self):
        """Returns the level of this task in the task hierarchy."""
        return self.derived_level

    def number_of_subtasks(self):
        """Returns the number of subtasks of this task."""
        return self.derived_number_of_subtasks

    def __str__(self):
        return "%s/%s" % (self.domain_identifier(), self.identifier())


class TaskIndex(db.Model):
    """
    The TaskIndex stores the entire task identifier hierarchy of each
    task, which is the parent entity of the index. These indices help
    with certain task hierarchy queries.

    Additionally, it stores all the assignees to that task. This
    can also be used for a query.

    The key_name of each TaskIndex is set to the identifier of the
    Task that is the index of.
    """
    # An ordered list of all the parent identifiers of the task.
    # Empty if the task has no parents.
    hierarchy = db.StringListProperty(required=True, default=[])
    # The level in the hierarchy of this index. Equivalent to the
    # number of items in the hierarchy list.
    level = aetycoon.LengthProperty(hierarchy)
    # An ordered list of identifiers of all users that are
    # participating in this task, because they are assigned to an
    # atomic subtask.
    assignees = db.StringListProperty(default=[])
    # The size of the assignees list
    assignee_count = aetycoon.LengthProperty(assignees)


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


class Domain(db.Model):
    """
    The top level entity that is used as a parent entity of all Tasks
    and Contexes for transaction support. Not really used in any other
    way at the moment, although the name could be used as some sort of
    title.
    """
    name = db.StringProperty(required=True)


class Context(db.Model):
    """
    A context is a second heirarchy structure that serves as a
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
    # Domain this user belongs to. All tasks of this user will be set
    # in this domain.
    #
    # TODO(tijmen): Later this can be expanded to a
    # list of domains.
    domain = db.ReferenceProperty(required=True,
                                  reference_class=Domain,
                                  collection_name="users")
    # The default context for new tasks for this user
    default_context = db.ReferenceProperty(required=True,
                                           reference_class=Context)

    def domain_key(self):
        """
        Returns the key of the |domain| without dereferencing the
        property.
        """
        return User.domain.get_value_for_datastore(self)

    def default_context_key(self):
        """
        Returns the key of the |default_context| without dereferencing
        the property.
        """
        return User.default_context.get_value_for_datastore(self)


class Task(db.Model):
    """
    A record for every task. Tasks can form a heirarchy. Tasks have
    single description. The title of a tasks is defined as the first
    line of this description.

    All tasks and their related components such as statuses are stored
    in the same entity group, so transactions can be easily used both
    for updating (moving tasks etc) and traversing the data. As tasks
    are all linked through references, a transaction must be used to
    get a consistent view on the data traversing through the
    heirarchy/graph. Using an entity group in this way does limit the
    writes to about 1/sec across the entire system, but in practice
    that should not pose a problem as the application is read
    dominated.

    Tasks do not have a specific keyname, but use the auto-generated
    numeric ids.

    The heirarchy features in appengine for keys are not used to store
    the heirachy of tasks, as it is very likely that the task
    heirarchy changes.
    """
    description = db.TextProperty(required=True)
    # Link to a parent task. Tasks that do not have a parent are all
    # considered to be in the 'backlog'.
    parent_task = db.SelfReferenceProperty(default=None,
                                           collection_name="sub_tasks")

    # TODO(tijmen): Add statuses

    # The user who created the task. At this moment also the one
    # who has to complete it.
    user = db.ReferenceProperty(reference_class=User,
                                required=True,
                                collection_name="tasks")
    # The user that has been assigned to complete this task. Can be
    # None.
    assignee = db.ReferenceProperty(default=None,
                                    reference_class=User,
                                    collection_name="assigned_tasks")
    context = db.ReferenceProperty(reference_class=Context,
                                   required=True,
                                   collection_name="tasks")
    # Whether or not the task is completed.
    completed = db.BooleanProperty(default=False)
    # A list of tasks that this tasks depends on to be completed
    # first. The list contains the key names of those tasks.
    dependent_on = db.StringListProperty(default=[])
    # The estimated time that this task will take to complete. If this
    # tasks has subtasks, then the duration becomes the sum of those
    # tasks.
    duration = db.TimeProperty()
    # Time of creation of the task. Just for reference.
    time = db.DateTimeProperty(auto_now_add=True)

    def title(self):
        """Returns the title of the task.

        The title is the first line in the description.
        """
        return self.description.split('\r\n', 1)[0].split('\n', 1)[0]

    def user_key(self):
        """Returns the key of the |user| without dereferencing the property.
        """
        return Task.user.get_value_for_datastore(self)

    def assignee_key(self):
        """Returns the key of the |assignee| without dereferencing the
        property.
        """
        return Task.assignee.get_value_for_datastore(self)

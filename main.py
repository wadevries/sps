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

import os
import logging
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db
from google.appengine.api import users
from appengine_utilities.sessions import Session

from model import Task, Context, Domain, User
import api


def add_message(session, message):
    """Adds a message to the current user's session.

    Args:
        session: a Session object, initialized with the request
        message: a string message

    The message is stored in the session, so it can be read in a later request.
    """
    if 'messages' not in session:
        session['messages'] = []
    session['messages'] = session['messages'] + [message]

def get_and_delete_messages(session):
    """Retrieves all messages in the current user's session, and clears them.

    Args:
        session: a Session object, initialized with the request

    Returns:
        A list of messages (strings)
    """
    messages = session.setdefault('messages', default=[])
    del session['messages']
    return messages


def get_user():
    """Gets the currently logged in user.

    The login is based on the GAE user system.

    Returns:
        An instance of the User model, or None if no user exists or
        is not logged in.
    """
    guser = users.get_current_user()
    if not guser:
        return None
    return User.get_by_key_name(guser.user_id())


def get_and_validate_user(domain_key_name):
    """Gets the currently logged in user and validates if he
    is a member of the domain.

    If the user is not logged in, or is not a member of the domain
    with |domain_key_name|, then None will be returned.

    Args:
        domain_key_name: The key name of the domain.

    Returns:
        An instance of the User model, or None if the user
        is not logged in or not a member of the domain.
    """
    user = get_user()
    if not user or not api.member_of_domain(domain_key_name, user):
        return None
    return user


def assignee_description(task):
    """Returns a string describing the assignee of a task"""
    return (task.assignee.name if task.assignee else "<not assigned>")


def _task_template_values(tasks, user):
    """
    Returns a list of dictionaries containing the template values for
    each task.

    Args:
        tasks: A list of Task model instance
        user: A User model instance

    Returns a list of dictionaries for each task, in the same order.
    """
    return [{ 'title': task.title(),
              'completed': task.completed,
              'is_assigned': task.assignee_key() != None,
              'assignee_description': assignee_description(task),
              'can_complete': api.can_complete_task(task, user),
              'num_subtasks': task.number_of_subtasks,
              'id': task.identifier() }
            for task in tasks]


class Landing(webapp.RequestHandler):
    """
    The main landing page. Shows the users domains and links to them.
    """
    def get(self):
        user = get_user()
        if not user:
            guser = users.get_current_user()
            user = User(key_name=guser.user_id(),
                        name=guser.nickname())
            user.put()

        keys = [db.Key.from_path('Domain', domain)
                for domain in user.domains]
        domains = Domain.get(keys)
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        template_values = {
            'username' : user.name,
            'domains' : [{ 'identifier': domain.key().name(),
                           'name': domain.name }
                         for domain in domains],
            'messages': get_and_delete_messages(session),
            }
        path = os.path.join(os.path.dirname(__file__),
                        'templates/landing.html')
        self.response.out.write(template.render(path, template_values))


class DomainOverview(webapp.RequestHandler):
    """
    Oveview of a domain for a current logged in user. Currently
    shows all tasks for a user and all the available tasks.
    """
    def get(self, domain_identifier):
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        user = get_and_validate_user(domain_identifier)
        if not user:
            self.error(404)
            return
        your_tasks = api.get_all_assigned_tasks(domain_identifier, user)
        open_tasks = api.get_all_open_tasks(domain_identifier)
        all_tasks = api.get_all_tasks(domain_identifier)
        domain = Domain.get_by_key_name(domain_identifier)
        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'username': user.name,
            'user_key_name': user.key().name(),
            'messages': get_and_delete_messages(session),
            'all_tasks': _task_template_values(all_tasks, user),
            'your_tasks': _task_template_values(your_tasks, user),
            'open_tasks': _task_template_values(open_tasks, user),
            }
        path = os.path.join(os.path.dirname(__file__),
                            'templates/overview.html')
        self.response.out.write(template.render(path, template_values))


class TaskDetail(webapp.RequestHandler):
    """
    Handler to show the full task details.
    """
    def get(self, domain_identifier, task_identifier):
        task = api.get_task(domain_identifier, task_identifier)
        if not task:
            error(404)
            return
        user = get_user()
        domain = Domain.get_by_key_name(domain_identifier)
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        subtasks = api.get_all_subtasks(domain_identifier, task)
        parent_task = task.parent_task
        parent_identifier = parent_task.identifier() if parent_task else ""
        parent_title = parent_task.title() if parent_task else ""
        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'user_name': user.name,
            'user_identifier': user.identifier(),
            'messages': get_and_delete_messages(session),
            'task_description': task.description,
            'task_assignee': assignee_description(task),
            'task_identifier':task.identifier(),
            'subtasks': _task_template_values(subtasks, user),
            'parent_identifier': parent_identifier,
            'parent_title': parent_title,
            }
        path = os.path.join(os.path.dirname(__file__),
                            'templates/taskdetail.html')
        self.response.out.write(template.render(path, template_values))


class CreateTask(webapp.RequestHandler):
    """
    Handler for POST requests to create new tasks.
    """
    def post(self):
        try:
            domain = self.request.get('domain')
            description = self.request.get('description')
            parent_identifier = self.request.get('parent', "")
            if not description:
                raise ValueError("Empty description")
            # The checkbox will return 'on' if checked and None
            # otherwise.
            self_assign = bool(self.request.get('assign_to_self'))
        except (TypeError, ValueError):
            self.error(400)
            return
        user = get_and_validate_user(domain)
        if not user:
            self.error(401)
            return
        self.session = Session(writer='cookie',
                               wsgiref_headers=self.response.headers)
        assignee = user if self_assign else None
        if not parent_identifier:
            parent_identifier = None
        task = api.create_task(domain,
                               user,
                               description,
                               assignee=assignee,
                               parent_task=parent_identifier)
        add_message(self.session, "Task '%s' created" % task.title())
        self.redirect('/d/%s/' % domain)


class TaskComplete(webapp.RequestHandler):
    """Handler for POST requests to set the completed flag on a task.

    A user can only complete tasks that are assigned to him, and not the
    tasks of other users.
    """
    def post(self):
        try:
            domain = self.request.get('domain')
            task_id = int(self.request.get('id'))
            completed = self.request.get('completed')
            completed = True if completed == 'true' else False
        except (TypeError, ValueError):
            self.error(400)
            return
        user = get_and_validate_user(domain)
        if not user:
            self.error(403)
            return
        try:
            api.set_task_completed(domain, user, task_id, completed)
        except ValueError:
            self.error(403)


class AssignTask(webapp.RequestHandler):
    """Handler for POST requests to set the assignee property of a task.
    """
    def post(self):
        try:
            domain = self.request.get('domain')
            task_id = int(self.request.get('id'))
            assignee = self.request.get('assignee')
        except (TypeError, ValueError):
            self.error(403)
            logging.error("Invalid input")
            return
        user = get_and_validate_user(domain)
        if not user:
            self.error(403)
            return
        task = Task.get_by_id(task_id, parent=Domain.key_from_name(domain))
        assignee = User.get_by_key_name(assignee)
        if not task or not assignee:
            self.error(403)
            logging.error("No task or assignee")
            return
        api.assign_task(domain, user, task, assignee)
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        add_message(session, "Task '%s' assigned to '%s'" %
                                (task.title(), assignee.name))
        self.redirect('/d/%s/' % domain)


class CreateDomain(webapp.RequestHandler):
    """Handler to create new domains.
    """
    def post(self):
        try:
            domain_id = self.request.get('domain')
            title = self.request.get('title')
        except (TypeError, ValueError):
            self.error(403)
            return
        user = get_user()
        domain = api.create_domain(domain_id, title, user)
        if not domain:
            self.response.out.write("Could not create domain")
            return
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        add_message(session, "Created domain '%s'" % domain.key().name())
        self.redirect('/d/%s/' % domain.key().name())



class CreateDomain(webapp.RequestHandler):
    """Handler to create new domains.
    """
    def post(self):
        try:
            domain_id = self.request.get('domain')
            title = self.request.get('title')
        except (TypeError, ValueError):
            self.error(403)
            return
        user = get_user()
        domain = api.create_domain(domain_id, title, user)
        if not domain:
            self.response.out.write("Could not create domain")
            return
        self.response.out.write("Created domain '%s'" % domain.key().name())


_VALID_DOMAIN_KEY_NAME = '[a-z][a-z0-9-]{1,100}'

_VALID_TASK_KEY_NAME = '[a-z0-9-]{1,100}'

_DOMAIN_URL = '/d/(%s)' % _VALID_DOMAIN_KEY_NAME

_DOMAIN_AND_TASK_URL = '%s/task/(%s)' % (_DOMAIN_URL, _VALID_TASK_KEY_NAME)

application = webapp.WSGIApplication([('/create-task', CreateTask),
                                      ('/set-task-completed', TaskComplete),
                                      ('/assign-task', AssignTask),
                                      ('/create-domain', CreateDomain),
                                      (_DOMAIN_URL + '/', DomainOverview),
                                      (_DOMAIN_AND_TASK_URL, TaskDetail),
                                      ('/', Landing)])

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

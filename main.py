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
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db
from google.appengine.api import users
import logging

from model import Task, Context, Domain, User
import api


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


def template_path(template_file):
    """Returns the full path of the |templat_file|"""
    return os.path.join(os.path.dirname(__file__),
                        'templates/overview.html')

def assignee_description(task):
    """Returns a string describing the assignee of a task"""
    return (task.assignee.name if task.assignee else "&#60not assigned&#62")


class Landing(webapp.RequestHandler):
    """
    The main landing page. Shows the users domains and links to them.
    """
    def get(self):
        user = get_user()
        if not user:
            # do something with login
            self.response.out.write("Not logged in")
            return
        template_values = {
            'username' : user.name,
            'domains' : [{ 'identifier': user.domain_key().name(),
                           'name': user.domain.name }],
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
        user = get_and_validate_user(domain_identifier)
        if not user:
            self.error(404)
            return
        your_tasks = api.get_all_assigned_tasks(user)
        open_tasks = api.get_all_open_tasks(user.domain_key())
        all_tasks = api.get_all_tasks(user.domain_key())
        template_values = {
            'domain': domain_identifier,
            'domain_name': user.domain.name,
            'username': user.name,
            'user_key_name': user.key().name(),
            'all_tasks': [{ 'title': task.title(),
                            'completed': task.completed,
                            'assignee': assignee_description(task),
                            'user': task.user,
                            'id': task.key().id_or_name() }
                          for task in all_tasks],
            'your_tasks': [{ 'title': task.title(),
                             'completed': task.completed,
                             'id': task.key().id_or_name() }
                           for task in your_tasks],
            'open_tasks': [{ 'title': task.title(),
                             'id': task.key().id_or_name() }
                           for task in open_tasks],
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
        template_values = {
            'domain_name': user.domain.name,
            'username': user.name,
            'task_description': task.description,
            'task_assignee': assignee_description(task),
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
        assignee = user if self_assign else None
        api.create_task(domain, user, description, assignee=assignee)
        self.response.out.write("Task created")


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
        task = Task.get_by_id(task_id, parent=user.domain_key())
        if not task or not user.can_edit_task(task):
            self.error(403)
            logging.error("No task with id '%d'" % task_id)
            return
        task.completed = completed
        task.put()


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
        task = Task.get_by_id(task_id, parent=user.domain_key())
        assignee = User.get_by_key_name(assignee)
        if not task or not assignee:
            self.error(403)
            logging.error("No task or assignee")
            return
        api.assign_task(user, task, assignee)
        self.response.out.write("Task '%s' assigned to '%s'" %
                                (task.title(), assignee.name))

_VALID_DOMAIN_KEY_NAME = '[a-z][a-z0-9-]{1,100}'

_VALID_TASK_KEY_NAME = '[a-z0-9-]{1,100}'

_DOMAIN_URL = '/d/(%s)' % _VALID_DOMAIN_KEY_NAME

_DOMAIN_AND_TASK_URL = '%s/task/(%s)' % (_DOMAIN_URL, _VALID_TASK_KEY_NAME)

application = webapp.WSGIApplication([('/create-task', CreateTask),
                                      ('/set-task-completed', TaskComplete),
                                      ('/assign-task', AssignTask),
                                      (_DOMAIN_URL + '/', DomainOverview),
                                      (_DOMAIN_AND_TASK_URL, TaskDetail),
                                      ('/', Landing)])

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

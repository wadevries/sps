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


def assignee_description(task):
    """Returns a string describing the assignee of a task"""
    return task.baked_assignee_description


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
              'levels': range(task.level),
              'completed': task.completed,
              'is_assigned': task.assignee_key() != None,
              'can_assign_to_self': api.can_assign_to_self(task, user),
              'assignee_description': assignee_description(task),
              'can_complete': api.can_complete_task(task, user),
              'num_subtasks': task.number_of_subtasks,
              'id': task.identifier() }
            for task in tasks]


def render_template(file, template_values):
    """
    Renders the template specified through file passing the given
    template_values.

    Args:
        file: relative path of the template file
        template_values: dictionary of template values

    Returns:
        A string containing the rendered template.
    """
    path = os.path.join(os.path.dirname(__file__), file)
    return template.render(path, template_values)


class Landing(webapp.RequestHandler):
    """
    The main landing page. Shows the users domains and links to them.
    """
    def get(self):
        user = api.get_user()
        domains = api.get_all_domains_for_user(user)
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        template_values = {
            'username' : user.name,
            'domains' : [{ 'identifier': domain.identifier(),
                           'name': domain.name }
                         for domain in domains],
            'messages': get_and_delete_messages(session),
            }
        path = os.path.join(os.path.dirname(__file__),
                        'templates/landing.html')
        self.response.out.write(template.render(path, template_values))


class YourTasksOverview(webapp.RequestHandler):
    """
    Overview of all the tasks of the logged in user.
    """
    def get(self, domain_identifier):
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        user = api.get_and_validate_user(domain_identifier)
        if not user:
            self.error(404)
            return

        domain = api.get_domain(domain_identifier)
        your_tasks = api.get_all_assigned_tasks(domain_identifier, user)
        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'username': user.name,
            'user_identifier': user.identifier(),
            'messages': get_and_delete_messages(session),
            'your_tasks': _task_template_values(your_tasks, user),
            }
        self.response.out.write(render_template('templates/yourtasks.html',
                                                template_values))


class OpenTasksOverview(webapp.RequestHandler):
    """
    Overview of all open tasks.
    """
    def get(self, domain_identifier):
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        user = api.get_and_validate_user(domain_identifier)
        if not user:
            self.error(404)
            return

        domain = api.get_domain(domain_identifier)
        open_tasks = api.get_all_open_tasks(domain_identifier)
        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'username': user.name,
            'user_identifier': user.identifier(),
            'messages': get_and_delete_messages(session),
            'open_tasks': _task_template_values(open_tasks, user),
            }
        self.response.out.write(render_template('templates/opentasks.html',
                                                template_values))


class AllTasksOverview(webapp.RequestHandler):
    """
    Overview of all open tasks.
    """
    def get(self, domain_identifier):
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        user = api.get_and_validate_user(domain_identifier)
        if not user:
            self.error(404)
            return
        all_tasks = api.get_all_tasks(domain_identifier, limit=200)
        domain = api.get_domain(domain_identifier)
        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'username': user.name,
            'user_identifier': user.identifier(),
            'messages': get_and_delete_messages(session),
            'all_tasks': _task_template_values(all_tasks, user),
            }
        self.response.out.write(render_template('templates/alltasks.html',
                                                template_values))


class TaskDetail(webapp.RequestHandler):
    """
    Handler to show the full task details.
    """
    def get(self, domain_identifier, task_identifier):
        task = api.get_task(domain_identifier, task_identifier)
        user = api.get_and_validate_user(domain_identifier)
        if not task or not user:
            self.error(404)
            return
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        user = api.get_user()
        domain = api.get_domain(domain_identifier)
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
            'task_title' : task.title(),
            'task_description': task.description_body(),
            'task_assignee': assignee_description(task),
            'task_identifier': task.identifier(),
            'task_can_assign_to_self': api.can_assign_to_self(task, user),
            'subtasks': _task_template_values(subtasks, user),
            'parent_identifier': parent_identifier,
            'parent_title': parent_title,
            }
        path = os.path.join(os.path.dirname(__file__),
                            'templates/taskdetail.html')
        self.response.out.write(template.render(path, template_values))


class TaskMoveView(webapp.RequestHandler):
    """
    Handler to show the Move task gui.
    """
    def get(self, domain_identifier, task_identifier):
        task = api.get_task(domain_identifier, task_identifier)
        user = api.get_and_validate_user(domain_identifier)
        if not task or not user:
            self.error(404)
            return

        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        user = api.get_user()
        domain = api.get_domain(domain_identifier)
        tasks = api.get_all_tasks(domain_identifier)

        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'user_name': user.name,
            'user_identifier': user.identifier(),
            'messages': get_and_delete_messages(session),
            'task_title' : task.title(),
            'task_description': task.description_body(),
            'task_identifier': task.identifier(),
            'task_num_subtasks': task.number_of_subtasks,
            'tasks': _task_template_values(tasks, user),
            }
        path = os.path.join(os.path.dirname(__file__),
                            'templates/movetask.html')
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
        user = api.get_and_validate_user(domain)
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
        if parent_identifier:
            self.redirect('/d/%s/task/%s' % (domain, parent_identifier))
        else:
            self.redirect('/d/%s/' % domain)


class MoveTask(webapp.RequestHandler):
    """
    Handler for POST requests to move the task to another
    parent task.
    """
    def post(self):
        try:
            domain_identifier = self.request.get('domain')
            task_identifier = self.request.get('task_id')
            new_parent_identifier = self.request.get('new_parent')
        except (TypeError, ValueError):
            self.error(400)
            return
        user = api.get_and_validate_user(domain_identifier)
        if not user:
            self.error(401)
            return
        self.session = Session(writer='cookie',
                               wsgiref_headers=self.response.headers)
        try:
            task = api.change_task_parent(domain_identifier,
                                          user,
                                          task_identifier,
                                          new_parent_identifier)
        except ValueError, error:
            self.error(401)
            self.response.out.write("Error while moving task: %s" % error)
            return

        add_message(self.session, "Task '%s' moved" % task.title())
        self.redirect('/d/%s/task/%s' % (domain_identifier, task_identifier))


class CompleteTask(webapp.RequestHandler):
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
        user = api.get_and_validate_user(domain)
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
        user = api.get_and_validate_user(domain)
        if not user:
            self.error(403)
            return
        assignee = User.get_by_key_name(assignee)
        if not assignee:
            self.error(403)
            logging.error("No assignee")
            return
        task = api.assign_task(domain, task_id, user, assignee)
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        add_message(session, "Task '%s' assigned to '%s'" %
                                (task.title(), assignee.name))
        self.redirect(self.request.headers.get('referer'))


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
        user = api.get_user()
        domain = api.create_domain(domain_id, title, user)
        if not domain:
            self.response.out.write("Could not create domain")
            return
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        add_message(session, "Created domain '%s'" % domain.key().name())
        self.redirect('/d/%s/' % domain.key().name())


_VALID_DOMAIN_KEY_NAME = '[a-z][a-z0-9-]{1,100}'

_VALID_TASK_KEY_NAME = '[a-z0-9-]{1,100}'

_DOMAIN_URL = '/d/(%s)/?' % _VALID_DOMAIN_KEY_NAME
_DOMAIN_ALL = '/d/(%s)/all/?' % _VALID_DOMAIN_KEY_NAME
_DOMAIN_OPEN = '/d/(%s)/open/?' % _VALID_DOMAIN_KEY_NAME

_TASK_URL = '%s/task/(%s)/?' % (_DOMAIN_URL, _VALID_TASK_KEY_NAME)

_TASK_MOVE_URL = "%s/move/?" % (_TASK_URL)

webapp.template.register_template_library('templatetags.templatefilters')

application = webapp.WSGIApplication([('/create-task', CreateTask),
                                      ('/set-task-completed', CompleteTask),
                                      ('/assign-task', AssignTask),
                                      ('/move-task', MoveTask),
                                      ('/create-domain', CreateDomain),
                                      (_DOMAIN_URL, YourTasksOverview),
                                      (_DOMAIN_ALL, AllTasksOverview),
                                      (_DOMAIN_OPEN, OpenTasksOverview),
                                      (_TASK_MOVE_URL, TaskMoveView),
                                      (_TASK_URL, TaskDetail),
                                      ('/', Landing)])

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

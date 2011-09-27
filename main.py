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


def _task_template_values(tasks, user, level=0):
    """
    Returns a list of dictionaries containing the template values for
    each task.

    Args:
        tasks: A list of Task model instance
        user: A User model instance

    Returns a list of dictionaries for each task, in the same order.
    """
    user_identifier = user.identifier()
    return [{ 'title': task.title(),
              # There are only 4 levels available in the css
              'level': min(level, 3),
              'completed': task.is_completed(),
              'is_assigned': task.assignee_key() != None,
              'can_assign_to_self': api.can_assign_to_self(task, user),
              'assignee_description': task.assignee_description(),
              'can_complete': api.can_complete_task(task, user),
              'summary': task.personalized_summary(user_identifier),
              'active': task.is_active(user_identifier),
              'atomic': task.atomic(),
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
        user = api.get_logged_in_user()
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


class Overview(webapp.RequestHandler):
    """
    Overview of all tasks in a domain. The tasks can be filtered by
    all/open and yours. Additionally, there is a New Task field to
    created new tasks with.
    """
    def get(self, domain_identifier):
        user = api.get_and_validate_user(domain_identifier)
        if not user:
            self.error(404)     # hides domain identifiers
            return
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        view = self.request.get('view', 'all')
        domain = api.get_domain(domain_identifier)
        if view == 'yours':
            tasks = api.get_assigned_tasks(domain_identifier,
                                           user,
                                           root_task=None,
                                           limit=200)
            no_tasks_message = "You do not have any unfinished tasks"
            tasks_heading = "Your Tasks"
        elif view == 'open':
            tasks = api.get_open_tasks(domain_identifier,
                                       root_task=None,
                                       limit=200)
            no_tasks_message = "No open subtasks in this domain"
            tasks_heading = "Open Tasks"
        else:                   # view == 'all' or None
            view = 'all'
            user_id = user.identifier()
            tasks = api.get_all_direct_subtasks(domain_identifier,
                                                root_task=None,
                                                limit=200,
                                                user_identifier=user_id)
            tasks_heading = "All Tasks"
            no_tasks_message = "No tasks are created in this domain"

        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'user_name': user.name,
            'user_identifier': user.identifier(),
            'messages': get_and_delete_messages(session),
            'tasks': _task_template_values(tasks, user),
            'tasks_heading': tasks_heading,
            'no_tasks_message': no_tasks_message,
            'view_mode': view,
            }
        self.response.out.write(render_template('templates/overview.html',
                                                template_values))


class TaskDetail(webapp.RequestHandler):
    """
    Handler to show the full task details.
    """
    def get(self, domain_identifier, task_identifier):
        task = api.get_task(domain_identifier, task_identifier)
        user = api.get_and_validate_user(domain_identifier)
        view = self.request.get('view', 'all')
        if not task or not user:
            self.error(404)
            return
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        user = api.get_logged_in_user()
        domain = api.get_domain(domain_identifier)
        if view == 'yours':
            subtasks = api.get_assigned_tasks(domain_identifier,
                                              user,
                                              root_task=task,
                                              limit=200)
            subtasks_heading = "Subtasks of '%s' Assigned to You" % task.title()
            no_subtasks_description = "No subtasks are assigned to you."
        elif view == 'open':
            subtasks = api.get_open_tasks(domain_identifier,
                                          root_task=task,
                                          limit=200)
            subtasks_heading = "Open Subtasks of '%s'" % task.title()
            no_subtasks_description = "No open subtasks for this task."
        else:                   # view == 'all' or None
            view = 'all'
            user_id = user.identifier()
            subtasks = api.get_all_direct_subtasks(domain_identifier,
                                                   root_task=task,
                                                   limit=200,
                                                   user_identifier=user_id)
            subtasks_heading = "All Subtasks of '%s'" % task.title()
            no_subtasks_description = "No subtasks for this task."

        parent_task = task.parent_task
        parent_identifier = parent_task.identifier() if parent_task else ""
        parent_title = parent_task.title() if parent_task else ""
        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'view_mode': view,
            'user_name': user.name,
            'user_identifier': user.identifier(),
            'messages': get_and_delete_messages(session),
            'task_title' : task.title(),
            'task_description': task.description_body(),
            'task_assignee': task.assignee_description(),
            'task_identifier': task.identifier(),
            'task_has_subtasks': not task.atomic(),
            'task_can_assign_to_self': api.can_assign_to_self(task, user),
            'task_can_edit': api.can_edit_task(domain, task, user),
            'subtasks': _task_template_values(subtasks, user),
            'parent_identifier': parent_identifier,
            'parent_title': parent_title,
            'subtasks_heading': subtasks_heading,
            'no_subtasks_description': no_subtasks_description,
            }
        self.response.out.write(render_template('templates/taskdetail.html',
                                                template_values))


class GetSubTasks(webapp.RequestHandler):
    """
    Handler for AJAX-requests to retrieve the direct subtasks of a
    task.  The returned output are html rows used in the task tables.

    The handler requires 3 GET parametesr:
        domain: The domain identifier string
        task: The parent task identifier string
        view: The type of requested subtasks: all, open or yours.
        level: The level of the task in the interface. This value is
           used to generate the property indentation in the template
           rendering.
        radio: If true, shows radio buttons instead of checkboxes next
           to tasks. Used in the move UI.
    """
    def get(self):
        try:
            domain_identifier = self.request.get('domain')
            task_identifier = self.request.get('task')
            view = self.request.get('view')
            level = int(self.request.get('level', 0))
            show_radio_buttons = bool(self.request.get('radio', False))
        except (ValueError,TypeError):
            self.error(400)
            return
        user = api.get_and_validate_user(domain_identifier)
        if not user:
            self.error(403)
            return

        domain = api.get_domain(domain_identifier)
        task = api.get_task(domain_identifier, task_identifier)
        if view == 'yours':
            tasks = api.get_assigned_tasks(domain_identifier,
                                           user,
                                           root_task=task,
                                           limit=200)
        elif view == 'open':
            tasks = api.get_open_tasks(domain_identifier,
                                       root_task=task,
                                       limit=200)
        else:                   # view == 'all' or None
            view = 'all'
            user_id = user.identifier()
            tasks = api.get_all_direct_subtasks(domain_identifier,
                                                root_task=task,
                                                limit=200,
                                                user_identifier=user_id)
        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'user_name': user.name,
            'user_identifier': user.identifier(),
            'tasks': _task_template_values(tasks, user, level=level+1),
            'view_mode': view,
            'show_radio_buttons': show_radio_buttons,
            }
        self.response.out.write(render_template('templates/get-subtasks.html',
                                                template_values))


class TaskEditView(webapp.RequestHandler):
    """
    Handler to show the edit task gui. It shows an editable
    description of the task. Only a user that created the task
    can edit it, or admins.

    A POST request to this handler will put the actual changes
    to database.
    """
    def get(self, domain_identifier, task_identifier):
        task = api.get_task(domain_identifier, task_identifier)
        user = api.get_and_validate_user(domain_identifier)
        if not task or not user:
            self.error(404)
            return

        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        domain = api.get_domain(domain_identifier)
        if not api.can_edit_task(domain, task, user):
            self.error(403)
            return

        # Tasks that are used to create the navigation tasks for
        # moving the task.
        user_id = user.identifier()
        tasks = api.get_all_direct_subtasks(domain_identifier,
                                            root_task=None,
                                            limit=200,
                                            user_identifier=user_id)

        template_values = {
            'domain_name': domain.name,
            'domain_identifier': domain_identifier,
            'user_name': user.name,
            'user_identifier': user.identifier(),
            'messages': get_and_delete_messages(session),
            'task_title' : task.title(),
            'task_description': task.description,
            'task_identifier': task.identifier(),
            'tasks': _task_template_values(tasks, user),
            'show_radio_buttons': True,
            'view_mode': 'all',
            }
        self.response.out.write(render_template('templates/edittask.html',
                                                template_values))


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
                               parent_task_identifier=parent_identifier)
        add_message(self.session, "Task '%s' created" % task.title())
        if parent_identifier:
            self.redirect('/d/%s/task/%s' % (domain, parent_identifier))
        else:
            self.redirect('/d/%s/' % domain)


class EditTask(webapp.RequestHandler):
    """
    Handler for POST requests to edit a task.
    """
    def post(self):
        try:
            domain_identifier = self.request.get('domain')
            task_identifier = self.request.get('task_id')
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
            description = self.request.get('description')
            task = api.change_task_description(domain_identifier,
                                               task_identifier,
                                               description,
                                               user)
        except ValueError:
            self.error(403)
            self.response.out.write("Error while editing task: %s" % error)
            return

        add_message(self.session, "Task '%s' edited" % task.title())
        self.redirect('/d/%s/task/%s' % (domain_identifier, task_identifier))


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
        logging.info("d:%s t:%s p:%s", domain_identifier, task_identifier, new_parent_identifier)
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
        user = api.get_logged_in_user()
        domain = api.create_domain(domain_id, title, user)
        if not domain:
            self.response.out.write("Could not create domain")
            return
        session = Session(writer='cookie',
                          wsgiref_headers=self.response.headers)
        add_message(session, "Created domain '%s'" % domain.key().name())
        self.redirect('/d/%s/' % domain.key().name())


_VALID_DOMAIN_KEY_NAME = api.VALID_DOMAIN_IDENTIFIER

_VALID_TASK_KEY_NAME = '[a-z0-9-]{1,100}'

_DOMAIN_URL = '/d/(%s)/?' % _VALID_DOMAIN_KEY_NAME
_DOMAIN_ALL = '/d/(%s)/all/?' % _VALID_DOMAIN_KEY_NAME
_DOMAIN_OPEN = '/d/(%s)/open/?' % _VALID_DOMAIN_KEY_NAME

_TASK_URL = '%s/task/(%s)/?' % (_DOMAIN_URL, _VALID_TASK_KEY_NAME)
_TASK_EDIT_URL = "%s/edit/?" % (_TASK_URL,)

webapp.template.register_template_library('templatetags.templatefilters')

application = webapp.WSGIApplication([('/create-task', CreateTask),
                                      ('/set-task-completed', CompleteTask),
                                      ('/assign-task', AssignTask),
                                      ('/edit-task', EditTask),
                                      ('/move-task', MoveTask),
                                      ('/create-domain', CreateDomain),
                                      ('/get-subtasks', GetSubTasks),
                                      (_DOMAIN_URL, Overview),
                                      (_DOMAIN_ALL, Overview),
                                      (_DOMAIN_OPEN, Overview),
                                      (_TASK_EDIT_URL, TaskEditView),
                                      (_TASK_URL, TaskDetail),
                                      ('/', Landing)])

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

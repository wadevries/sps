# A Simple Planning System

## Overview

The planning system is designed to facilitate the planning of both
complex large projects, as well as keep track of the simplest atomic
tasks in those projects. To support this functionality, the planner is
a heirarchical system, with all the advanced functionality derived
from a basic datastructure.  Most importantly, the planner must be
simple to use. That is, entering a tasks must be as simple as entering
a single line describing the task and then pressing enter.

The central component of the planner is the Task, which represents
something that must be done. This can be a very large task, such as
'build a product', to a simple task like 'change font size of the
subtitles in the tutorial screen'. Tasks can be divided into subtasks,
each containing a part of the work that is required to complete the
parent task. Subtasks are tasks themselves, and as such can be divided
into subtasks again. A task with no subtasks is called an 'atomic
task', and represent a single bit of work to be done by a single person.

A tasks if finished if all subtasks are completed, or, if the task
does not have any subtasks, when itself is completed.

A tasks can be also be dependent on one or more other tasks, which
signifies that those other tasks must be completed before this task
can be started. It is not possible to create a circular reference in
this way. A task with a dependency that is not complete cannot be
completed itself.

Task have an assignee property, which is the person that is supposed
to complete the task. Atomic tasks have a single person as
assignee. The assignees of a composite task is the union of all
assignees in its subtasks.

Tasks have various other properties, such as a status that can contain
custom information about the task, and an estimated length/duration
for the task. The different statuses of a task are user
configurable. Whether a task is complete or not is not a status, but a
separate field in the task, due to its special meaning in the task
heirarchy.

The second component of the planner are contexts. Every task has a
context. A context relates to the persons or entities that are
involved with the tasks, for example the engineering group of a
company. Contexts are used to group tasks when it is not appropriate
to group the tasks through a single parent task, as a context is not
something that has to be done or can be finished. For example, a
context could be the engineering division in a company, so it is easy
to retrieve all engineering tasks. Contexts are also heirarchical, so
the engineering division could be a subcontext of the company. A
similar system could be created with tags, but tag management is
complicated, and forcing a context on task automatically categorizes
them and allows for straightforward filtering.

Communication between team members about a task is handled through
comments on the task, which form a linear thread of messages. These
messages also act as a log tracking the mutations of the task, such as
assignee changes etc.

## Implementation

The implementation will be done on Google App Engine, a flexible cloud
based web-app development platform. The first user interface will also
be done in HTML, but the goal is to solely use an (as simple as
possible) api to interface with the planner, so that it will be
relatively easy at a later stage to create other clients.

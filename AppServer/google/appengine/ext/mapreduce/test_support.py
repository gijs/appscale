#!/usr/bin/env python
#
# Copyright 2007 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
















"""Utilities to aid in testing mapreduces."""


import base64
import cgi
import collections
import logging
import os
import re

from google.appengine.ext.mapreduce import main
from google.appengine.ext.webapp import mock_webapp
from google.appengine.ext.mapreduce import util


def decode_task_payload(task):
  """Decodes POST task payload.

  Args:
    task: a task to decode its payload.

  Returns:
    parameter_name -> parameter_value dict. If multiple parameter values are
    present, then parameter_value will be a list.
  """
  body = task["body"]
  if not body:
    return {}
  decoded = base64.b64decode(body)
  result = {}
  for (name, value) in cgi.parse_qs(decoded).items():
    if len(value) == 1:
      result[name] = value[0]
    else:
      result[name] = value
  return util.HugeTask.decode_payload(result)


def execute_task(task, retries=0, handlers_map=None):
  """Execute mapper's executor task.

  This will try to determine the correct mapper handler for the task, will set
  up all mock environment necessary for task execution, and execute the task
  itself.

  This function can be used for functional-style testing of functionality
  depending on mapper framework.

  Args:
    task: a taskqueue task.
    retries: the current retry of this task.
    handlers_map: a dict from url regex to handler.

  Returns:
    the handler instance used for this task.
  """
  if not handlers_map:
    handlers_map = main.create_handlers_map()

  url = task["url"]
  handler = None

  for (re_str, handler_class) in handlers_map:
    re_str = "^" + re_str + "($|\\?)"
    if re.match(re_str, url):
      handler = handler_class()
      break

  if not handler:
    raise Exception("Can't determine handler for %s" % task)

  handler.initialize(mock_webapp.MockRequest(),
                     mock_webapp.MockResponse())
  handler.request.set_url(url)

  handler.request.environ["HTTP_HOST"] = "myapp.appspot.com"
  for k, v in task.get("headers", []):
    handler.request.headers[k] = v
    environ_key = "HTTP_" + k.replace("-", "_").upper()
    handler.request.environ[environ_key] = v
  handler.request.headers["X-AppEngine-TaskExecutionCount"] = retries
  handler.request.environ["HTTP_X_APPENGINE_TASKNAME"] = (
      task.get("name", "default_task_name"))
  handler.request.environ["HTTP_X_APPENGINE_QUEUENAME"] = (
      task.get("queue_name", "default"))
  handler.request.environ["PATH_INFO"] = handler.request.path

  saved_os_environ = os.environ
  try:
    os.environ = dict(os.environ)
    os.environ.update(handler.request.environ)
    if task["method"] == "POST":
      for k, v in decode_task_payload(task).items():
        handler.request.set(k, v)
      handler.post()
    elif task["method"] == "GET":
      handler.get()
    else:
      raise Exception("Unsupported method: %s" % task.method)
  finally:
    os.environ = saved_os_environ

  if handler.response.status != 200:
    raise Exception("Handler failure: %s (%s). \nTask: %s\nHandler: %s" %
                    (handler.response.status,
                     handler.response.status_message,
                     task,
                     handler))
  return handler


def execute_all_tasks(taskqueue, queue="default", handlers_map=None):
  """Run and remove all tasks in the taskqueue.

  Args:
    taskqueue: An instance of taskqueue stub.
    queue: Queue name to run all tasks from.
    hanlders_map: see main.create_handlers_map.

  Returns:
    task_run_counts: a dict from handler class to the number of tasks
      it handled.
  """
  tasks = taskqueue.GetTasks(queue)
  taskqueue.FlushQueue(queue)
  task_run_counts = collections.defaultdict(lambda: 0)
  for task in tasks:
    retries = 0
    while True:
      try:
        handler = execute_task(task, retries, handlers_map=handlers_map)
        task_run_counts[handler.__class__] += 1
        break

      except:
        retries += 1

        if retries > 100:
          logging.debug("Task %s failed for too many times. Giving up.",
                        task["name"])
          raise
        logging.debug(
            "Task %s is being retried for the %s time",
            task["name"],
            retries)
  return task_run_counts


def execute_until_empty(taskqueue, queue="default", handlers_map=None):
  """Execute taskqueue tasks until it becomes empty.

  Args:
    taskqueue: An instance of taskqueue stub.
    queue: Queue name to run all tasks from.
    hanlders_map: see main.create_handlers_map.

  Returns:
    task_run_counts: a dict from handler class to the number of tasks
      it handled.
  """
  task_run_counts = collections.defaultdict(lambda: 0)
  while taskqueue.GetTasks(queue):
    new_counts = execute_all_tasks(taskqueue, queue, handlers_map)
    for handler_cls in new_counts:
      task_run_counts[handler_cls] += new_counts[handler_cls]
  return task_run_counts

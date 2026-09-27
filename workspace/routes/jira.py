"""HTTP endpoints for the Jira task constructor."""

from functools import wraps

from flask import Blueprint, jsonify, request

from workspace.common import json_body
from workspace.jira import service, storage
from workspace.jira.client import JiraClient, JiraError
from workspace.jira.composer import compose

bp = Blueprint("jira", __name__, url_prefix="/api/jira")


def endpoint(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return jsonify({"result": function(*args, **kwargs)})
        except JiraError as error:
            return jsonify({"error": str(error), "fields": error.fields, "uncertain": error.uncertain}), error.status
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except OSError:
            return jsonify({"error": "Не удалось прочитать или сохранить варианты в data/jira"}), 500
    return wrapped


def client():
    return JiraClient(request.headers.get("X-Jira-Token", ""))


@bp.get("/options")
@endpoint
def options():
    return storage.load_options()


@bp.post("/options")
@endpoint
def add_option():
    body = json_body()
    return storage.change_option(body.get("category"), body.get("value"))


@bp.delete("/options")
@endpoint
def remove_option():
    body = json_body()
    return storage.change_option(body.get("category"), body.get("value"), remove=True)


@bp.post("/preview")
@endpoint
def preview():
    return compose(json_body())


@bp.get("/metadata")
@endpoint
def metadata():
    result = service.metadata(client(), request.args.get("project", "UCP"), request.args.get("issueType", "3"))
    # Only the fields needed by the form; never return credentials.
    return {key: value for key, value in result.items() if key != "fields"}


@bp.get("/epics")
@endpoint
def epics():
    return service.search_epics(client(), request.args.get("project", "UCP"), request.args.get("q", ""))


@bp.post("/issues")
@endpoint
def create():
    return service.create_issue(client(), json_body())

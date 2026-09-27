"""Application factory for the local developer workspace."""

from flask import Flask

from workspace import config, notes, web
from workspace.routes import creator, jira, manager, mutator, observer


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = max(config.MAX_NOTE_BYTES, config.MAX_UPLOAD_BYTES) + 1024 * 1024
    notes.ensure_directories()
    for blueprint in (web.bp, notes.bp, observer.bp, creator.bp, manager.bp, mutator.bp, jira.bp):
        app.register_blueprint(blueprint)
    return app

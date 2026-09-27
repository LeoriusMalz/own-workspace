"""Entrypoint for the local developer workspace."""

import os

from workspace import create_app

app = create_app()


if __name__ == "__main__":
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port="1681", debug=debug)

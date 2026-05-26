"""Run Flask app with Waitress for local/production use."""

import os

from waitress import serve

from barangay_project.app import create_app


def main() -> None:
    app = create_app()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    serve(app, host=host, port=port)


if __name__ == "__main__":
    main()

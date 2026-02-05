"""WSGI entrypoint for Flask CLI.

This file exists only to make running the app unambiguous.

Usage:
  flask --app wsgi run
"""

from barangay_project.app import create_app

app = create_app()

"""WSGI entrypoint for Flask deployments."""

from barangay_project.app import create_app

app = create_app()

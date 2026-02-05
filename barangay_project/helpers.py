"""
Utility functions for the Barangay Document Management System.

This module defines helper functions that are used across multiple
blueprints, such as logging actions to the transaction log.  By
centralizing these helpers here, we avoid circular imports and keep
route modules focused on view logic.
"""
from __future__ import annotations

import base64
import os
import re
import uuid
from functools import wraps

from flask import abort, current_app, has_request_context, request
from flask_login import current_user
from flask_mail import Message
from werkzeug.utils import secure_filename

from .extensions import db
from .models import TransactionLog


def roles_required(*roles: str):
    """Require the current user to have one of the given roles.

    Usage:
        @login_required
        @roles_required("admin")
        def view(...):
            ...
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            # If the user is not logged in, Flask-Login will handle it via @login_required.
            if not current_user.is_authenticated:
                abort(401)
            if roles and getattr(current_user, "role", None) not in roles:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def get_client_ip() -> str | None:
    """Best-effort client IP for rate limiting and audit logs."""
    if not has_request_context():
        return None
    try:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or None
    except Exception:
        return None
    try:
        return request.remote_addr
    except Exception:
        return None


def log_action(
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    meta: dict | None = None,
) -> None:
    """Record an action in the transaction log.

    This function creates a new `TransactionLog` entry associated
    with the currently authenticated user.  If no user is
    authenticated, the action is not logged.

    Args:
        action: A description of the action performed.
    """
    # Ensure that we only log actions for authenticated users
    if current_user.is_authenticated:
        ip = get_client_ip()
        ua = None
        if has_request_context():
            try:
                ua = (request.user_agent.string or "")[:255]
            except Exception:
                ua = None

        log = TransactionLog(
            user_id=current_user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=ip,
            user_agent=ua,
            meta=meta,
        )
        db.session.add(log)
        db.session.commit()


def _send_otp_email(user, code: str, subject: str, body: str) -> None:
    """Send a one-time code email with a custom subject/body."""
    # Retrieve the mail extension from the current app context
    mail = current_app.extensions.get("mail")
    if mail is None:
        # If mail is not configured, simply log the OTP to the console for debugging
        print(f"OTP for {user.email}: {code}")
        return

    # Compose the email message
    recipients = [user.email]
    msg = Message(subject=subject, recipients=recipients, body=body)
    try:
        mail.send(msg)
    except Exception as exc:
        # If sending fails, log the code to the console for debugging
        print(f"Failed to send OTP email: {exc}")
        print(f"OTP for {user.email}: {code}")


def send_otp_email(user, code: str) -> None:
    """Send a password reset OTP to the given user's email address."""
    subject = "Your Password Reset Code"
    body = (
        f"Hello {user.username},\n\n"
        f"We received a request to reset your password.\n"
        f"Use the following one-time code to reset your password: {code}\n\n"
        f"If you did not request a password reset, please ignore this email."
    )
    _send_otp_email(user, code, subject, body)


def send_login_otp_email(user, code: str) -> None:
    """Send a login verification OTP to the given user's email address."""
    subject = "Your Login Verification Code"
    body = (
        f"Hello {user.username},\n\n"
        f"Use the following one-time code to finish signing in: {code}\n\n"
        f"If you did not try to sign in, please change your password immediately."
    )
    _send_otp_email(user, code, subject, body)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}


def save_uploaded_image(file_storage, subfolder: str) -> str | None:
    """Save an uploaded image under static/uploads/<subfolder> and return relative path.

    Returns:
        Relative path (e.g., 'uploads/residents/<file>.jpg') or None if no file.
    """
    if not file_storage or not getattr(file_storage, "filename", ""):
        return None

    filename = secure_filename(file_storage.filename)
    if "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return None

    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.root_path, "static", "uploads")
    )
    target_dir = os.path.join(upload_root, subfolder)
    os.makedirs(target_dir, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}.{ext}"
    abs_path = os.path.join(target_dir, unique_name)
    file_storage.save(abs_path)

    # Store path relative to /static for easy url_for('static', filename=...)
    return f"uploads/{subfolder}/{unique_name}"


_DATA_URL_RE = re.compile(r"^data:image/(?P<ext>png|jpeg|jpg);base64,(?P<data>.+)$")


def save_captured_image(data_url: str | None, subfolder: str) -> str | None:
    """Save a webcam-captured image from a Data URL (data:image/...;base64,...).

    Returns a relative path under static/ (e.g., 'uploads/residents/<uuid>.jpg')
    or None if the data_url is empty/invalid.
    """
    if not data_url:
        return None

    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        return None

    ext = m.group("ext").lower()
    if ext == "jpeg":
        ext = "jpg"

    try:
        raw = base64.b64decode(m.group("data"), validate=True)
    except Exception:
        return None

    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.root_path, "static", "uploads")
    )
    target_dir = os.path.join(upload_root, subfolder)
    os.makedirs(target_dir, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}.{ext}"
    abs_path = os.path.join(target_dir, unique_name)
    with open(abs_path, "wb") as f:
        f.write(raw)

    return f"uploads/{subfolder}/{unique_name}"

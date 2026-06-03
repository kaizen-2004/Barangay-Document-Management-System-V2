"""
Authentication blueprint for the Barangay Document Management System.

This module defines routes for user login and logout.  It integrates
with Flask-Login to manage user sessions.  Additional routes for
registration and password reset could be added here in the future.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from flask_login import login_user, logout_user, login_required, current_user

from .extensions import db
from .models import User, LoginAttempt
from .forms import (
    LoginForm,
    PasswordChangeForm,
)
from .helpers import log_action, get_client_ip
from .time_utils import utcnow
from datetime import timedelta
import time

auth_bp = Blueprint("auth", __name__)


def _record_login_attempt(username: str | None, ip: str | None, success: bool) -> None:
    attempt = LoginAttempt(
        username=username or None,
        ip_address=ip or None,
        success=success,
    )
    db.session.add(attempt)
    db.session.commit()


def _is_rate_limited(username: str | None, ip: str | None) -> bool:
    max_attempts = int(current_app.config.get("LOGIN_RATE_LIMIT_MAX", 5))
    window_seconds = int(current_app.config.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 600))
    if max_attempts <= 0 or window_seconds <= 0:
        return False

    cutoff = utcnow() - timedelta(seconds=window_seconds)
    base_query = LoginAttempt.query.filter(
        LoginAttempt.success.is_(False),
        LoginAttempt.created_at >= cutoff,
    )
    ip_count = base_query.filter(LoginAttempt.ip_address == ip).count() if ip else 0
    user_count = base_query.filter(LoginAttempt.username == username).count() if username else 0
    return max(ip_count, user_count) >= max_attempts


def _finalize_login(user: User, remember: bool, next_page: str | None, *, action: str):
    login_user(user, remember=remember)
    session.permanent = True
    session["last_activity"] = int(time.time())

    log_action(
        action,
        entity_type="user",
        entity_id=user.id,
        meta={"username": user.username, "role": user.role},
    )

    # Force the default admin to change the password on first login.
    if user.username == "admin" and user.check_password("admin"):
        session["force_password_change"] = True
        flash("Please change the default admin password before continuing.", "warning")
        return redirect(url_for("auth.change_password"))

    session.pop("force_password_change", None)
    return redirect(next_page or url_for("main.index"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Render the login page and process login submissions."""
    # If the user is already authenticated, redirect to the dashboard
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = LoginForm()
    if form.validate_on_submit():
        username = (form.username.data or "").strip()
        ip = get_client_ip()
        if _is_rate_limited(username, ip):
            flash("Too many failed login attempts. Please try again later.", "danger")
            return render_template("login.html", form=form)

        # Look up the user by username
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(form.password.data):
            _record_login_attempt(username, ip, success=False)
            flash("Invalid username or password.", "danger")
            return render_template("login.html", form=form)

        _record_login_attempt(username, ip, success=True)

        flash("Logged in successfully.", "success")
        return _finalize_login(
            user,
            form.remember.data,
            request.args.get("next"),
            action="Logged in",
        )
    return render_template("login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    """Log the current user out and redirect to the login page."""
    log_action(
        "Logged out",
        entity_type="user",
        entity_id=current_user.id,
        meta={"username": current_user.username, "role": current_user.role},
    )
    logout_user()
    session.pop("force_password_change", None)
    session.pop("last_activity", None)
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


# Route for users to change their own password
@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """Allow a logged-in user to change their password."""
    form = PasswordChangeForm()
    if form.validate_on_submit():
        # Verify the current password
        if not current_user.check_password(form.current_password.data):
            flash("Incorrect current password.", "danger")
        else:
            current_user.set_password(form.new_password.data)
            # Persist the new password to the database
            db.session.commit()
            session.pop("force_password_change", None)
            # Log the password change
            log_action("Changed own password")
            flash("Your password has been updated.", "success")
            return redirect(url_for("main.index"))
    return render_template("change_password.html", form=form)


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = PasswordChangeForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("Incorrect current password.", "danger")
        else:
            current_user.set_password(form.new_password.data)
            db.session.commit()
            session.pop("force_password_change", None)
            log_action("Changed password from profile")
            flash("Password updated.", "success")
            return redirect(url_for("auth.profile"))
    return render_template("profile.html", form=form)

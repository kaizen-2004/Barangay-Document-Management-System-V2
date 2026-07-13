"""
Authentication blueprint for the Barangay Document Management System.

This module defines routes for user login, logout, and password reset.
"""
import random
import string

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from flask_login import login_user, logout_user, login_required, current_user

from .extensions import db
from .models import User, LoginAttempt, PasswordResetCode
from .forms import (
    LoginForm,
    PasswordChangeForm,
    ForgotPasswordForm,
    ResetPasswordForm,
)
from .helpers import log_action, get_client_ip
from .email_service import send_email
from .time_utils import utcnow
from datetime import timedelta, timezone
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


def _generate_reset_code() -> str:
    return "".join(random.choices(string.digits, k=6))


def _send_reset_code(user: User) -> bool:
    code = _generate_reset_code()
    expiry_minutes = int(current_app.config.get("PASSWORD_RESET_CODE_EXPIRY_MINUTES", 15))
    expires_at = utcnow() + timedelta(minutes=expiry_minutes)

    reset = PasswordResetCode(
        user_id=user.id,
        code=code,
        expires_at=expires_at,
    )
    db.session.add(reset)
    db.session.commit()

    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2 style="color: #a32020;">Password Reset Code</h2>
        <p>Your 6-digit verification code is:</p>
        <div style="font-size: 32px; font-weight: bold; letter-spacing: 8px;
                    text-align: center; padding: 20px; background: #f8f4f3;
                    border-radius: 8px; color: #1f1616;">{code}</div>
        <p style="color: #7b6f6f; font-size: 14px;">
            This code expires in {expiry_minutes} minutes.<br>
            If you didn't request this, ignore this email.
        </p>
    </div>
    """
    return send_email(user.email, "Password Reset Code", html)


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        username = (form.username.data or "").strip()
        user = User.query.filter_by(username=username).first()

        if user and user.email:
            _send_reset_code(user)
            # Mask email for display: j***@gmail.com
            email = user.email
            masked = email[0] + "***@" + email.split("@")[1] if "@" in email else "***"
            flash(f"Reset code sent to {masked}", "success")
        else:
            # Always show same message to prevent username enumeration
            flash("If an account with that username exists, a reset code has been sent.", "info")

        return redirect(url_for("auth.reset_password"))

    return render_template("forgot_password.html", form=form)


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        code = (form.code.data or "").strip()
        new_password = form.new_password.data

        user = User.query.filter(db.func.lower(User.email) == email).first()
        if not user:
            flash("Invalid email or code.", "danger")
            return render_template("reset_password.html", form=form)

        expiry_minutes = int(current_app.config.get("PASSWORD_RESET_CODE_EXPIRY_MINUTES", 15))
        reset = PasswordResetCode.query.filter(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.code == code,
            PasswordResetCode.used.is_(False),
            PasswordResetCode.expires_at >= utcnow(),
        ).order_by(PasswordResetCode.id.desc()).first()

        if not reset:
            flash("Invalid or expired code. Please request a new one.", "danger")
            return render_template("reset_password.html", form=form)

        # Mark code as used and set new password
        reset.used = True
        user.set_password(new_password)
        db.session.commit()

        log_action("Password reset via email", entity_type="user", entity_id=user.id)
        flash("Your password has been reset. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", form=form)

"""
Authentication blueprint for the Barangay Document Management System.

This module defines routes for user login and logout.  It integrates
with Flask-Login to manage user sessions.  Additional routes for
registration and password reset could be added here in the future.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from flask_login import login_user, logout_user, login_required, current_user

from .extensions import db
from .models import User, PasswordReset, LoginAttempt, LoginMfaCode
from .forms import (
    LoginForm,
    MfaVerifyForm,
    PasswordChangeForm,
    ForgotPasswordForm,
    ResetPasswordForm,
)
from .helpers import log_action, send_otp_email, send_login_otp_email, get_client_ip
from .time_utils import utcnow
from datetime import timedelta
import secrets
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


def _start_mfa(user: User, remember: bool, next_page: str | None) -> None:
    code = secrets.token_hex(3).upper()
    ttl = int(current_app.config.get("MFA_CODE_TTL_SECONDS", 600))
    expires_at = utcnow() + timedelta(seconds=ttl)
    mfa = LoginMfaCode(user_id=user.id, otp_code=code, expires_at=expires_at)
    db.session.add(mfa)
    db.session.commit()
    send_login_otp_email(user, code)
    session["mfa_user_id"] = user.id
    session["mfa_remember"] = bool(remember)
    if next_page:
        session["mfa_next"] = next_page


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

        # Require MFA for admins before completing login
        if user.role == "admin" and current_app.config.get("ADMIN_MFA_REQUIRED", True):
            if not user.email:
                flash("Admin accounts must have a valid email address for MFA.", "danger")
                return render_template("login.html", form=form)
            _start_mfa(user, form.remember.data, request.args.get("next"))
            flash("Verification code sent. Please check your email.", "info")
            return redirect(url_for("auth.mfa_verify"))

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
    session.pop("mfa_user_id", None)
    session.pop("mfa_remember", None)
    session.pop("mfa_next", None)
    session.pop("force_password_change", None)
    session.pop("last_activity", None)
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/mfa", methods=["GET", "POST"])
def mfa_verify():
    """Verify admin login via email OTP."""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    user_id = session.get("mfa_user_id")
    if not user_id:
        flash("Please log in again.", "warning")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, user_id)
    if not user:
        session.pop("mfa_user_id", None)
        session.pop("mfa_remember", None)
        session.pop("mfa_next", None)
        flash("Please log in again.", "warning")
        return redirect(url_for("auth.login"))

    form = MfaVerifyForm()
    if form.validate_on_submit():
        code = (form.otp_code.data or "").strip().upper()
        pr = LoginMfaCode.query.filter_by(
            user_id=user.id,
            otp_code=code,
            used=False,
        ).order_by(LoginMfaCode.expires_at.desc()).first()
        if pr and pr.expires_at > utcnow():
            pr.used = True
            db.session.commit()
            remember = bool(session.pop("mfa_remember", False))
            next_page = session.pop("mfa_next", None)
            session.pop("mfa_user_id", None)
            flash("Logged in successfully.", "success")
            return _finalize_login(
                user,
                remember,
                next_page,
                action="Logged in (MFA)",
            )
        else:
            flash("Invalid or expired verification code.", "danger")
    return render_template("mfa_verify.html", form=form)


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


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Initiate a password reset by sending an OTP code to the user's bound email.

    Users provide their **username** to request a password reset.  If a
    matching user is found, an OTP code is generated, stored with an
    expiration timestamp, and emailed to the address saved in the user's
    `email` field.  A generic success message is flashed regardless of
    whether the user exists to avoid revealing account information.
    """
    # If user is already logged in, redirect to main page
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        # Find user by username (treated as email)
        user = User.query.filter_by(username=form.username.data).first()
        if user:
            # Generate a secure random 6-digit OTP code
            code = secrets.token_hex(3).upper()  # 6 hex characters (~3 bytes)
            expires_at = utcnow() + timedelta(minutes=10)
            pr = PasswordReset(user_id=user.id, otp_code=code, expires_at=expires_at)
            db.session.add(pr)
            db.session.commit()
            # Send the OTP via email (or log to console if mail is not configured)
            send_otp_email(user, code)
            # Log the password reset request
            log_action(f"Requested password reset for '{user.username}'")
        # Always flash a generic message to avoid account enumeration
        flash("If an account exists for the provided username, an OTP has been sent.", "info")
        return redirect(url_for("auth.reset_password"))
    return render_template("forgot_password.html", form=form)


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Complete a password reset using an OTP code.

    Users enter their **username**, the OTP code they received, and their
    new password.  If the OTP is valid and has not expired or been used,
    the user's password is updated and they are redirected to the login
    page with a success message.
    """
    # If user is logged in, redirect to main page
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if not user:
            flash("Invalid username or OTP code.", "danger")
        else:
            # Look up the most recent unused password reset request
            pr = PasswordReset.query.filter_by(
                user_id=user.id,
                otp_code=form.otp_code.data,
                used=False,
            ).order_by(PasswordReset.expires_at.desc()).first()
            if pr and pr.expires_at > utcnow():
                # Update the user's password and mark the reset token as used
                user.set_password(form.new_password.data)
                pr.used = True
                db.session.commit()
                # Log the password reset
                log_action(f"Reset password for '{user.username}'")
                flash("Your password has been reset. You may now log in.", "success")
                return redirect(url_for("auth.login"))
            else:
                flash("Invalid or expired OTP code.", "danger")
    return render_template("reset_password.html", form=form)

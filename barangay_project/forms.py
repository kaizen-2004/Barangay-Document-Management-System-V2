"""
WTForms classes defining the input forms for the Barangay Document Management System.

Separate form classes for adding a resident and issuing a document help keep
the view functions in `routes.py` concise.  Validation logic can be extended
as needed.
"""
import re

from flask import current_app
from flask_wtf import FlaskForm
from wtforms import StringField, DateField, SelectField, TextAreaField, SubmitField, HiddenField
from wtforms import PasswordField, BooleanField
from wtforms.validators import DataRequired, Optional, EqualTo, Regexp, Length, ValidationError


def _password_policy_errors(password: str) -> list[str]:
    min_len = int(current_app.config.get("PASSWORD_MIN_LENGTH", 10))
    require_upper = current_app.config.get("PASSWORD_REQUIRE_UPPER", True)
    require_lower = current_app.config.get("PASSWORD_REQUIRE_LOWER", True)
    require_digit = current_app.config.get("PASSWORD_REQUIRE_DIGIT", True)
    require_symbol = current_app.config.get("PASSWORD_REQUIRE_SYMBOL", True)
    disallow_spaces = current_app.config.get("PASSWORD_DISALLOW_SPACES", True)

    errors = []
    if len(password) < min_len:
        errors.append(f"at least {min_len} characters")
    if require_upper and not re.search(r"[A-Z]", password):
        errors.append("an uppercase letter")
    if require_lower and not re.search(r"[a-z]", password):
        errors.append("a lowercase letter")
    if require_digit and not re.search(r"\d", password):
        errors.append("a number")
    if require_symbol and not re.search(r"[^\w\s]", password):
        errors.append("a symbol")
    if disallow_spaces and re.search(r"\s", password):
        errors.append("no spaces")
    return errors


def password_strength_required(form, field) -> None:
    password = field.data or ""
    errors = _password_policy_errors(password)
    if errors:
        raise ValidationError(
            "Password must contain " + ", ".join(errors) + "."
        )


class ResidentForm(FlaskForm):
    # Webcam-captured image as a data URL (data:image/jpeg;base64,...) coming
    # from the in-app camera capture UI.
    photo_data = HiddenField()
    barangay_id = StringField("Barangay ID No.", validators=[Optional(), Length(max=50)])
    first_name = StringField("First Name", validators=[DataRequired()])
    middle_name = StringField("Middle Name")
    last_name = StringField("Last Name", validators=[DataRequired()])
    gender = SelectField("Gender", choices=[("Male", "Male"), ("Female", "Female"), ("Other", "Other")], validators=[DataRequired()])
    birth_date = DateField("Birth Date", validators=[DataRequired()])
    marital_status = SelectField(
        "Marital Status",
        choices=[
            ("Single", "Single"),
            ("Married", "Married"),
            ("Separated", "Separated"),
            ("Widowed", "Widowed"),
            ("Annulled", "Annulled"),
            ("Other", "Other"),
        ],
        validators=[DataRequired()],
    )
    address = StringField("Address", validators=[DataRequired()])
    submit = SubmitField("Save")


class DocumentForm(FlaskForm):
    resident_id = SelectField("Resident", coerce=int, validators=[DataRequired()])
    document_type_id = SelectField("Document Type", coerce=int, validators=[DataRequired()])
    details = TextAreaField("Details")
    # Optional: capture/update the resident's photo during document issuance.
    resident_photo_data = HiddenField()
    issue_date = DateField("Issue Date", validators=[Optional()])
    submit = SubmitField("Save Draft")


class DocumentTypeForm(FlaskForm):
    requires_photo = BooleanField('Requires Resident Photo')
    """Admin form to manage document types."""

    name = StringField("Name", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[Optional()])
    template_path = SelectField(
        "PDF Template",
        choices=[
            ("", "Auto (by name)"),
            ("barangay_id", "Barangay ID"),
            ("barangay_clearance", "Barangay Clearance"),
            ("business_clearance", "Business Clearance"),
            ("residency", "Residency"),
            ("generic", "Generic"),
        ],
        validators=[Optional()],
    )
    submit = SubmitField("Save")


class LoginForm(FlaskForm):
    """Form for users to log in to the system.

    Authentication is based on a unique username rather than an email.
    The username may differ from the user's email address, which is
    stored separately for password reset notifications.
    """
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember Me")
    submit = SubmitField("Log In")


class MfaVerifyForm(FlaskForm):
    """Form for verifying the email OTP during admin MFA."""

    otp_code = StringField("Verification Code", validators=[DataRequired()])
    submit = SubmitField("Verify")


class UserForm(FlaskForm):
    """Form for creating or editing user accounts.

    Administrators can use this form to add new users.  It includes
    fields for username, password and role.  The role field allows
    selection between admin and clerk roles.  Additional roles can be
    added by modifying the choices list.
    """
    # Unique username used for login.  This may differ from the user's
    # email address.  The username is not validated as an email.
    username = StringField("Username", validators=[DataRequired()])
    # Email address used for password reset notifications.
    # Use a simple regex to avoid requiring the external `email_validator` package.
    email = StringField(
        "Email",
        validators=[
            DataRequired(),
            Length(max=255),
            Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Enter a valid email address."),
        ],
    )
    password = PasswordField("Password", validators=[DataRequired(), password_strength_required])
    role = SelectField(
        "Role",
        choices=[("admin", "Admin"), ("clerk", "Clerk")],
        validators=[DataRequired()],
    )
    submit = SubmitField("Save")


class EditUserForm(FlaskForm):
    """Form for editing existing user accounts.

    The password field is optional; if left blank, the user's
    password will not be changed.  Administrators can use this form
    to update usernames, roles and optionally reset passwords.
    """
    # Username used for login.  Separate from the user's email address.
    username = StringField("Username", validators=[DataRequired()])
    # Email address associated with the user.  Used for password reset notifications.
    email = StringField(
        "Email",
        validators=[
            DataRequired(),
            Length(max=255),
            Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Enter a valid email address."),
        ],
    )
    password = PasswordField("New Password", validators=[Optional(), password_strength_required])
    role = SelectField(
        "Role",
        choices=[("admin", "Admin"), ("clerk", "Clerk")],
        validators=[DataRequired()],
    )
    submit = SubmitField("Update")


class PasswordChangeForm(FlaskForm):
    """Form for users to change their password.

    Requires the current password and a new password entered twice for
    confirmation.  The `EqualTo` validator ensures the two new
    password fields match.
    """
    current_password = PasswordField("Current Password", validators=[DataRequired()])
    new_password = PasswordField(
        "New Password",
        validators=[DataRequired(), password_strength_required],
    )
    confirm_new_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match")],
    )
    submit = SubmitField("Change Password")


class ForgotPasswordForm(FlaskForm):
    """Form for initiating a password reset via OTP.

    Users provide their username (treated as email) to request a
    password reset.  If a matching user exists, the system generates
    an OTP and sends it to the user's email address.
    """
    username = StringField(
        "Username",
        validators=[DataRequired()],
    )
    submit = SubmitField("Send OTP")


class ResetPasswordForm(FlaskForm):
    """Form for completing a password reset using an OTP.

    Users must enter their username, the OTP code they received, and
    their desired new password twice for confirmation.  The
    `EqualTo` validator ensures the password fields match.
    """
    username = StringField(
        "Username",
        validators=[DataRequired()],
    )
    otp_code = StringField("OTP Code", validators=[DataRequired()])
    new_password = PasswordField("New Password", validators=[DataRequired(), password_strength_required])
    confirm_new_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match")],
    )
    submit = SubmitField("Reset Password")


class DeleteForm(FlaskForm):
    """Tiny form used only to attach CSRF to POST delete actions."""

    submit = SubmitField("Delete")

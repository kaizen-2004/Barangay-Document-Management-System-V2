"""
WTForms classes defining the input forms for the Barangay Document Management System.

Separate form classes for adding a resident and issuing a document help keep
the view functions in `routes.py` concise.  Validation logic can be extended
as needed.
"""
import re

from flask import current_app
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, DateField, SelectField, TextAreaField, SubmitField, HiddenField, IntegerField
from wtforms import PasswordField, BooleanField
from wtforms.validators import DataRequired, Optional, EqualTo, Length, ValidationError


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
    contact_number = StringField("Contact Number", validators=[Optional(), Length(max=50)])
    occupation = StringField("Occupation", validators=[Optional(), Length(max=120)])
    years_on_barangay = StringField("Years in Barangay", validators=[Optional(), Length(max=10)])
    emergency_contact_name = StringField("Emergency Contact Person", validators=[Optional(), Length(max=150)])
    emergency_contact_relationship = StringField("Relationship", validators=[Optional(), Length(max=80)])
    emergency_contact_number = StringField("Emergency Contact Number", validators=[Optional(), Length(max=50)])
    emergency_contact_address = StringField("Emergency Contact Address", validators=[Optional(), Length(max=255)])
    emergency_contact_same_address = BooleanField("Same address as resident")
    street_id = SelectField("Street", coerce=int, validators=[DataRequired()])
    address = StringField("House No. / Address Details", validators=[DataRequired()])
    submit = SubmitField("Save")


class BarangayStreetForm(FlaskForm):
    name = StringField("Street / Road Name", validators=[DataRequired(), Length(max=120)])
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
    template_file = FileField("DOCX Template", validators=[Optional(), FileAllowed(["docx"], "DOCX templates only.")])
    template_active = BooleanField("Template Active")
    placeholder_config = TextAreaField("Placeholder Config (JSON)", validators=[Optional()])
    field_config = TextAreaField("Fill-out Fields (JSON)", validators=[Optional()])
    validity_months = IntegerField("Validity (months)", validators=[Optional()], render_kw={"min": 1, "placeholder": "e.g. 6 or 12"})
    submit = SubmitField("Save")


class LoginForm(FlaskForm):
    """Form for users to log in to the system."""
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember Me")
    submit = SubmitField("Log In")


class UserForm(FlaskForm):
    """Form for creating or editing user accounts.

    Administrators can use this form to add new users.  It includes
    fields for username, password and role.  The role field allows
    selection between admin and clerk roles.  Additional roles can be
    added by modifying the choices list.
    """
    username = StringField("Username", validators=[DataRequired()])
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
    username = StringField("Username", validators=[DataRequired()])
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


class OfficialForm(FlaskForm):
    full_name = StringField("Full Name", validators=[DataRequired(), Length(max=150)])
    title = StringField("Title", validators=[DataRequired(), Length(max=100)])
    signature_file = FileField(
        "Signature Image",
        validators=[Optional(), FileAllowed(["png", "jpg", "jpeg"], "PNG/JPG only.")],
    )
    is_active = BooleanField("Set as active official")
    submit = SubmitField("Save")


class DeleteForm(FlaskForm):
    """Tiny form used only to attach CSRF to POST delete actions."""

    submit = SubmitField("Delete")

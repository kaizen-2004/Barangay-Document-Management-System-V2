"""
SQLAlchemy models defining the database schema for the Barangay Document
Management System.

Each model corresponds to a table in the PostgreSQL database.  Relationships
between models are declared via foreign keys and backrefs.  Additional
optional fields can be added to meet specific barangay requirements.
"""
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db
from .time_utils import utcnow


class Resident(db.Model):
    """
    Represents a resident in the barangay.  A resident may have multiple
    documents issued to them.  Additional demographic fields can be added
    such as civil status, occupation, zone, etc.
    """

    __tablename__ = "residents"
    __table_args__ = (
        db.Index("ix_residents_last_name", "last_name"),
        db.Index("ix_residents_barangay_id", "barangay_id"),
    )
    id = db.Column(db.Integer, primary_key=True)
    # Human-friendly identifier for the resident (optional but useful for
    # barangay ID issuance and searching).
    # Example format used by the app when auto-generated: BRGY-2026-00001
    barangay_id = db.Column(db.String(50), unique=True, nullable=True)
    first_name = db.Column(db.String(100), nullable=False)
    middle_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    birth_date = db.Column(db.Date, nullable=False)
    marital_status = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(255), nullable=False)
    photo_path = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    is_archived = db.Column(db.Boolean, default=False, nullable=False, server_default="false")
    archived_at = db.Column(db.DateTime, nullable=True)
    archived_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Relationship to Document: One resident can have many documents.
    documents = db.relationship(
        "Document",
        back_populates="resident",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Resident {self.last_name}, {self.first_name}>"


class DocumentType(db.Model):
    """
    Defines a type of document that the barangay can issue.  Common examples
    include Barangay Clearance, Certificate of Residency, Certificate of
    Indigency, and Business Clearance.
    """

    __tablename__ = "document_types"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    template_path = db.Column(db.String(255), nullable=True)
    requires_photo = db.Column(db.Boolean, nullable=False, default=False, server_default='false')

    documents = db.relationship(
        "Document",
        back_populates="document_type",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<DocumentType {self.name}>"


class Document(db.Model):
    """
    Records each document issued.  Links a resident and a document type
    together with additional details and the issue date.  Optionally
    contains a file_path pointing to the generated PDF or digital copy.
    """

    __tablename__ = "documents"
    __table_args__ = (
        db.Index("ix_documents_issue_date", "issue_date"),
        db.Index("ix_documents_resident_id", "resident_id"),
        db.Index("ix_documents_document_type_id", "document_type_id"),
    )
    id = db.Column(db.Integer, primary_key=True)
    resident_id = db.Column(db.Integer, db.ForeignKey("residents.id"), nullable=False)
    document_type_id = db.Column(db.Integer, db.ForeignKey("document_types.id"), nullable=False)
    status = db.Column(db.String(20), default="draft", nullable=False)
    details = db.Column(db.Text, nullable=True)
    issue_date = db.Column(db.DateTime, default=utcnow, nullable=False)
    file_path = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    issued_at = db.Column(db.DateTime, nullable=True)
    is_archived = db.Column(db.Boolean, default=False, nullable=False, server_default="false")
    archived_at = db.Column(db.DateTime, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    issued_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    archived_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    resident = db.relationship("Resident", back_populates="documents")
    document_type = db.relationship("DocumentType", back_populates="documents")

    def __repr__(self):
        return f"<Document {self.id} - {self.document_type.name} for {self.resident.last_name}>"


class User(UserMixin, db.Model):
    """
    Represents a user of the system (e.g., admin, clerk).  This table is
    used for authentication and role-based access control.  Integrates
    with Flask-Login via the UserMixin base class, which provides
    default implementations of the methods Flask-Login requires.
    """

    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    # Unique username used for login.  Separate from the user's email
    # address, which is stored in the `email` column.
    username = db.Column(db.String(150), nullable=False, unique=True)
    # Email address associated with the user.  Used for password reset
    # notifications and other communications.
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False, default="clerk")
    created_at = db.Column(db.DateTime, default=utcnow)

    def set_password(self, password: str) -> None:
        """Hash and store the user's password.

        Args:
            password: The plaintext password to hash and store.
        """
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Check a plaintext password against the stored hash.

        Args:
            password: The candidate password to verify.

        Returns:
            True if the password matches the stored hash, False otherwise.
        """
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class TransactionLog(db.Model):
    """
    Optional audit trail.  Records actions performed by users such as
    creating or deleting records.  Useful for accountability and
    troubleshooting.
    """

    __tablename__ = "transaction_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = db.Column(db.String(255), nullable=False)
    # Optional structured context for richer audit trails.
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    meta = db.Column(db.JSON, nullable=True)
    timestamp = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User")

    def __repr__(self):
        return f"<TransactionLog {self.id} - {self.action}>"


class PasswordReset(db.Model):
    """
    Stores one-time password (OTP) codes for password reset operations.

    Each record corresponds to a single password reset request.  The
    OTP code is valid until `expires_at` and can be marked as used
    once the password has been successfully reset.  Entries older
    than the expiration time should be cleaned up periodically.
    """

    __tablename__ = "password_resets"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    otp_code = db.Column(db.String(20), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User")

    def __repr__(self):
        return f"<PasswordReset {self.id} for user {self.user_id}>"


class LoginAttempt(db.Model):
    """Tracks login attempts for rate limiting and audit."""

    __tablename__ = "login_attempts"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    success = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def __repr__(self):
        return f"<LoginAttempt {self.id} {'success' if self.success else 'fail'}>"


class LoginMfaCode(db.Model):
    """Stores short-lived OTP codes for admin MFA during login."""

    __tablename__ = "login_mfa_codes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    otp_code = db.Column(db.String(20), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User")

    def __repr__(self):
        return f"<LoginMfaCode {self.id} for user {self.user_id}>"

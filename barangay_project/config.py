"""
Configuration settings for the Barangay Document Management System.

This module defines different configuration classes for various environments
(development, testing, production).  The default configuration uses
environment variables to construct the SQLAlchemy database URI; if a
`DATABASE_URL` is not provided, a fallback is used.

The project is designed for PostgreSQL but will gracefully fall back to
SQLite if necessary (e.g., for quick local testing).  To switch between
databases, set the `DATABASE_URL` environment variable before running
`flask run`.
"""
import os
from datetime import timedelta


class Config:
    """Base configuration with default settings."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "a-very-secret-key")
    # Determine database URL: if DATABASE_URL is provided, use it; otherwise, default to PostgreSQL on localhost.
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/barangay_db",
    )
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Convenience for local development.
    # If true (default), the app will run `db.create_all()` on startup.
    # For real deployments, set AUTO_CREATE_DB=false and use Alembic:
    #   flask db upgrade
    AUTO_CREATE_DB = os.environ.get("AUTO_CREATE_DB", "true").lower() in {"1", "true", "yes", "on"}

    # Uploads (images, generated files)
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', os.path.join(os.path.dirname(__file__), 'static', 'uploads'))
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 5 * 1024 * 1024))  # 5MB

    # CSRF: keep tokens valid (avoids "token expired" during long admin sessions)
    WTF_CSRF_TIME_LIMIT = None

    # Pagination defaults
    DEFAULT_PAGE_SIZE = int(os.environ.get("DEFAULT_PAGE_SIZE", 20))

    # Ops / logging / backups
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    LOG_JSON = os.environ.get("LOG_JSON", "True") == "True"
    AUTO_MIGRATE = os.environ.get("AUTO_MIGRATE", "False") == "True"
    BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(os.getcwd(), "backups"))
    BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", 7))
    ERROR_REPORT_EMAIL = os.environ.get("ERROR_REPORT_EMAIL", "")

    # Automatic cleanup of expired documents (issue date + validity window)
    AUTO_PURGE_EXPIRED = os.environ.get("AUTO_PURGE_EXPIRED", "True") == "True"
    PURGE_VALIDITY_MONTHS = int(os.environ.get("PURGE_VALIDITY_MONTHS", 6))
    PURGE_GRACE_DAYS = int(os.environ.get("PURGE_GRACE_DAYS", 30))
    PURGE_CHECK_INTERVAL_MINUTES = int(os.environ.get("PURGE_CHECK_INTERVAL_MINUTES", 1440))

    # Password policy
    PASSWORD_MIN_LENGTH = int(os.environ.get("PASSWORD_MIN_LENGTH", 10))
    PASSWORD_REQUIRE_UPPER = os.environ.get("PASSWORD_REQUIRE_UPPER", "True") == "True"
    PASSWORD_REQUIRE_LOWER = os.environ.get("PASSWORD_REQUIRE_LOWER", "True") == "True"
    PASSWORD_REQUIRE_DIGIT = os.environ.get("PASSWORD_REQUIRE_DIGIT", "True") == "True"
    PASSWORD_REQUIRE_SYMBOL = os.environ.get("PASSWORD_REQUIRE_SYMBOL", "True") == "True"
    PASSWORD_DISALLOW_SPACES = os.environ.get("PASSWORD_DISALLOW_SPACES", "True") == "True"

    # Login rate limiting (per IP and per username)
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 600))
    LOGIN_RATE_LIMIT_MAX = int(os.environ.get("LOGIN_RATE_LIMIT_MAX", 5))

    # Admin MFA (email OTP)
    ADMIN_MFA_REQUIRED = os.environ.get("ADMIN_MFA_REQUIRED", "True") == "True"
    MFA_CODE_TTL_SECONDS = int(os.environ.get("MFA_CODE_TTL_SECONDS", 600))

    # Session timeouts
    SESSION_IDLE_TIMEOUT_SECONDS = int(os.environ.get("SESSION_IDLE_TIMEOUT_SECONDS", 1800))
    SESSION_ABSOLUTE_TIMEOUT_SECONDS = int(os.environ.get("SESSION_ABSOLUTE_TIMEOUT_SECONDS", 8 * 60 * 60))
    PERMANENT_SESSION_LIFETIME = timedelta(seconds=SESSION_ABSOLUTE_TIMEOUT_SECONDS)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "False") == "True"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    # Security headers
    SECURITY_HEADERS_ENABLED = os.environ.get("SECURITY_HEADERS_ENABLED", "True") == "True"
    HSTS_SECONDS = int(os.environ.get("HSTS_SECONDS", 31536000))
    CSP = os.environ.get(
        "CSP",
        (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: blob:; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "media-src 'self' blob:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        ),
    )

    # Flask-Mail settings.  These defaults can be overridden by
    # environment variables.  To enable password reset emails, set
    # MAIL_SERVER, MAIL_USERNAME and MAIL_PASSWORD in your environment.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", None)
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587)) if os.environ.get("MAIL_PORT") else None
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True") == "True"
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "False") == "True"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", None)
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", None)
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", None)


class DevelopmentConfig(Config):
    """Configuration for development environment."""

    DEBUG = True


class ProductionConfig(Config):
    """Configuration for production environment."""

    DEBUG = False
    # In production, you might fetch a secure database URL and secret key from the environment


class TestingConfig(Config):
    """Configuration for testing (uses an in-memory SQLite DB)."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

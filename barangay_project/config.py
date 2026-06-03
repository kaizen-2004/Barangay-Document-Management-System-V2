"""
Configuration settings for the Barangay Document Management System.

This module defines different configuration classes for various environments
(development, testing, production).  The default configuration uses
environment variables to construct the SQLAlchemy database URI; if a
`DATABASE_URL` is not provided, a fallback is used.

The default database is SQLite (zero-setup, single-file).  To use
MySQL or PostgreSQL instead, set the `DATABASE_URL` environment variable
before starting the application.
"""
import os
from datetime import timedelta


class Config:
    """Base configuration with default settings."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "a-very-secret-key")
    # Default to SQLite for zero-setup deployment.  Override via
    # DATABASE_URL env var to use MySQL, PostgreSQL, or any SQLAlchemy backend.
    # For SQLite relative paths, we resolve to an absolute path so that
    # Flask-SQLAlchemy (which resolves against the instance folder) works
    # consistently regardless of how the app is started.
    _SQLITE_DEFAULT = os.path.abspath(os.path.join(os.getcwd(), "data", "barangay.db"))
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{_SQLITE_DEFAULT}",
    )
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": int(os.environ.get("DB_POOL_SIZE", 10)),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", 20)),
    }

    # Convenience for local development.
    # If true (default), the app will run `db.create_all()` on startup.
    # For real deployments, set AUTO_CREATE_DB=false and use Alembic:
    #   flask db upgrade
    AUTO_CREATE_DB = os.environ.get("AUTO_CREATE_DB", "true").lower() in {"1", "true", "yes", "on"}

    # Uploads (images, generated files)
    UPLOAD_FOLDER = os.environ.get(
        'UPLOAD_FOLDER',
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend', 'static', 'uploads')),
    )
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 5 * 1024 * 1024))  # 5MB
    ENABLE_CAMERA_CAPTURE = os.environ.get("ENABLE_CAMERA_CAPTURE", "True") == "True"
    ENABLE_BACKGROUND_REMOVAL = os.environ.get("ENABLE_BACKGROUND_REMOVAL", "True") == "True"
    ENABLE_ID_PHOTO_PIPELINE = os.environ.get("ENABLE_ID_PHOTO_PIPELINE", "True") == "True"
    ID_PHOTO_NORMALIZE = os.environ.get("ID_PHOTO_NORMALIZE", "True") == "True"
    ID_PHOTO_REFINE_ALPHA = os.environ.get("ID_PHOTO_REFINE_ALPHA", "True") == "True"
    ID_PHOTO_PRESET = os.environ.get("ID_PHOTO_PRESET", "id_photo")
    ID_PHOTO_MODEL = os.environ.get("ID_PHOTO_MODEL", "birefnet-general")
    ID_PHOTO_SKIP_NORMALIZE = os.environ.get("ID_PHOTO_SKIP_NORMALIZE", "False") == "True"
    PROCESSED_PHOTO_WIDTH = int(os.environ.get("PROCESSED_PHOTO_WIDTH", 600))
    PROCESSED_PHOTO_HEIGHT = int(os.environ.get("PROCESSED_PHOTO_HEIGHT", 600))

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
    MYSQLDUMP_BIN = os.environ.get("MYSQLDUMP_BIN", "mysqldump")
    MYSQL_BIN = os.environ.get("MYSQL_BIN", "mysql")
    LIBREOFFICE_BIN = os.environ.get("LIBREOFFICE_BIN", "soffice")
    LIBREOFFICE_TIMEOUT_SECONDS = int(os.environ.get("LIBREOFFICE_TIMEOUT_SECONDS", 90))
    ERROR_REPORT_EMAIL = os.environ.get("ERROR_REPORT_EMAIL", "")

    DOCUMENT_STORAGE_ROOT = os.environ.get(
        "DOCUMENT_STORAGE_ROOT",
        os.path.abspath(os.path.join(os.getcwd(), "barangay_data")),
    )
    DOCX_TEMPLATE_UPLOAD_DIR = os.environ.get(
        "DOCX_TEMPLATE_UPLOAD_DIR",
        os.path.join(DOCUMENT_STORAGE_ROOT, "templates"),
    )
    DOCX_OUTPUT_DIR = os.environ.get(
        "DOCX_OUTPUT_DIR",
        os.path.join(DOCUMENT_STORAGE_ROOT, "generated"),
    )

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

    # Branding — customize the system name, barangay name, and footer
    SYSTEM_NAME = os.environ.get("SYSTEM_NAME", "Barangay Management System")
    BARANGAY_NAME = os.environ.get("BARANGAY_NAME", "Krus Na Ligas")
    SYSTEM_DESCRIPTION = os.environ.get(
        "SYSTEM_DESCRIPTION",
        "Barangay Document Management and Archiving System",
    )
    APP_VERSION = os.environ.get("APP_VERSION", "0.1.0")
    APP_AUTHOR = os.environ.get("APP_AUTHOR", "Steve Villa")

    # Login rate limiting (per IP and per username)
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 600))
    LOGIN_RATE_LIMIT_MAX = int(os.environ.get("LOGIN_RATE_LIMIT_MAX", 5))

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

class DevelopmentConfig(Config):
    """Configuration for development environment."""

    DEBUG = True


class ProductionConfig(Config):
    """Configuration for production environment."""

    DEBUG = False
    # In production, you might fetch a secure database URL and secret key from the environment


class TestingConfig(Config):
    """Configuration for testing."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "sqlite:///./data/barangay_test.db",
    )

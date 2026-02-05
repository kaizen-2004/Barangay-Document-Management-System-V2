"""
Application entry point for the Barangay Document Management System.

This module creates the Flask application, loads configuration, initializes
extensions, and registers blueprints.  Running this script via `flask run`
starts the development server.
"""
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from calendar import monthrange
from datetime import date as dt_date, datetime, timedelta, timezone

import click

from flask import Flask, flash, g, jsonify, redirect, request, session, url_for
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFError
from flask_login import current_user, logout_user
from flask_mail import Message
from werkzeug.exceptions import HTTPException

from .config import DevelopmentConfig
from .extensions import csrf, db, login_manager, mail
from sqlalchemy import inspect, text

# Optional: load environment variables from a .env file if present.
# This makes local setup much smoother and avoids "role USER does not exist"
# errors when DATABASE_URL is only defined in .env.
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None
from .routes import main_bp
from .auth import auth_bp
from .admin import admin_bp


def create_app(config_class=DevelopmentConfig):
    """
    Application factory.  Creates and configures the Flask app instance.

    Args:
        config_class: The configuration class to use (e.g., DevelopmentConfig or ProductionConfig).
    Returns:
        A configured Flask app instance.
    """
    # Load .env from the current working directory and/or the package directory.
    if load_dotenv is not None:
        load_dotenv(os.path.join(os.getcwd(), ".env"), override=False)
        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)

    app = Flask(__name__)
    app.config.from_object(config_class)

    # Logging configuration
    level_name = str(app.config.get("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)
    app.logger.setLevel(log_level)

    # Initialize extensions
    db.init_app(app)
    Migrate(app, db)
    # Initialize mail extension for sending password reset emails
    mail.init_app(app)

    # Enable CSRF protection globally. This allows templates to use
    # `csrf_token()` and enforces CSRF validation on POST/PUT/PATCH/DELETE.
    csrf.init_app(app)

    # Configure login manager for authentication
    login_manager.init_app(app)
    # Redirect unauthenticated users to the login page
    login_manager.login_view = "auth.login"
    login_manager.session_protection = "strong"

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e: CSRFError):
        """Gracefully handle CSRF failures instead of returning a blank 400."""
        from flask import flash, redirect, request, url_for

        flash("Security token missing/expired. Please retry the action.", "danger")
        return redirect(request.referrer or url_for("main.index"))

    @login_manager.user_loader
    def load_user(user_id: str):
        """Given a user ID, return the corresponding User object.

        Flask-Login uses this callback to reload the user object from
        the user ID stored in the session.  If the ID is not found,
        None is returned.
        """
        # Import here to avoid circular imports.
        from .models import User

        if not user_id:
            return None
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    @app.context_processor
    def inject_pagination_helpers():
        def pagination_url(page: int):
            args = request.args.to_dict(flat=True)
            args["page"] = page
            return url_for(request.endpoint, **args)

        return {"pagination_url": pagination_url}

    @app.before_request
    def assign_request_id():
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        g.request_start = time.time()
        # Echo back for clients
        request.environ["request_id"] = g.request_id

    @app.teardown_request
    def log_unhandled_exception(exc):
        if exc and not isinstance(exc, HTTPException):
            payload = {
                "event": "error",
                "request_id": getattr(g, "request_id", None),
                "path": request.path,
                "method": request.method,
                "error": str(exc),
            }
            if app.config.get("LOG_JSON", True):
                app.logger.exception(json.dumps(payload))
            else:
                app.logger.exception("Unhandled exception: %s", exc)

            report_to = str(app.config.get("ERROR_REPORT_EMAIL", "")).strip()
            if report_to:
                try:
                    user_id = getattr(current_user, "id", None) if current_user.is_authenticated else None
                    subject = f"[Barangay] Error {request.method} {request.path}"
                    body = (
                        "An unhandled exception occurred.\n\n"
                        f"Time (UTC): {datetime.now(timezone.utc).isoformat()}\n"
                        f"Request ID: {getattr(g, 'request_id', None)}\n"
                        f"User ID: {user_id}\n"
                        f"Method: {request.method}\n"
                        f"Path: {request.path}\n"
                        f"IP: {request.remote_addr}\n"
                        f"User-Agent: {request.user_agent.string if request.user_agent else ''}\n"
                        f"Error: {exc}\n"
                    )
                    msg = Message(subject=subject, recipients=[report_to], body=body)
                    mail.send(msg)
                except Exception:
                    app.logger.exception("Failed to send error report email.")

    @app.after_request
    def log_request(response):
        duration_ms = None
        if hasattr(g, "request_start"):
            duration_ms = int((time.time() - g.request_start) * 1000)
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")

        payload = {
            "event": "request",
            "request_id": getattr(g, "request_id", None),
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "user_id": getattr(current_user, "id", None) if current_user.is_authenticated else None,
        }
        if app.config.get("LOG_JSON", True):
            app.logger.info(json.dumps(payload))
        else:
            app.logger.info(
                "%s %s %s %sms user=%s",
                request.method,
                request.path,
                response.status_code,
                duration_ms,
                payload["user_id"],
            )
        return response

    @app.before_request
    def enforce_session_security():
        """Apply idle timeout and forced password change checks."""
        if not current_user.is_authenticated:
            return

        endpoint = request.endpoint or ""
        if endpoint.startswith("static"):
            return

        idle_timeout = int(app.config.get("SESSION_IDLE_TIMEOUT_SECONDS", 0) or 0)
        if idle_timeout > 0:
            now_ts = int(time.time())
            last = session.get("last_activity")
            if last and now_ts - int(last) > idle_timeout:
                logout_user()
                session.pop("mfa_user_id", None)
                session.pop("mfa_remember", None)
                session.pop("mfa_next", None)
                session.pop("force_password_change", None)
                session.pop("last_activity", None)
                flash("Your session expired due to inactivity. Please log in again.", "warning")
                return redirect(url_for("auth.login"))
            session["last_activity"] = now_ts

        if session.get("force_password_change"):
            allowed = {"auth.change_password", "auth.logout"}
            if endpoint not in allowed:
                return redirect(url_for("auth.change_password"))

    @app.after_request
    def apply_security_headers(response):
        """Set security headers (CSP/HSTS/etc.)."""
        if app.config.get("SECURITY_HEADERS_ENABLED", True):
            csp = app.config.get("CSP")
            if csp:
                response.headers["Content-Security-Policy"] = csp
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
            if request.is_secure:
                hsts = int(app.config.get("HSTS_SECONDS", 0) or 0)
                if hsts > 0:
                    response.headers["Strict-Transport-Security"] = f"max-age={hsts}; includeSubDomains"
        return response

    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    @app.get("/healthz")
    def healthz():
        """Basic health check with optional DB connectivity."""
        db_ok = True
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        status = "ok" if db_ok else "degraded"
        code = 200 if db_ok else 503
        return jsonify({"status": status, "db": db_ok, "time": datetime.now(timezone.utc).isoformat()}), code

    # Optional: run Alembic migrations automatically on startup
    if app.config.get("AUTO_MIGRATE", False):
        try:
            from flask_migrate import upgrade as alembic_upgrade

            with app.app_context():
                alembic_upgrade()
            app.logger.info("Auto migration completed.")
        except Exception as exc:  # pragma: no cover - depends on runtime env
            app.logger.exception("Auto migration failed: %s", exc)

    # -----------------------------------------------------------------
    # Database initialization
    # -----------------------------------------------------------------
    # IMPORTANT:
    # - Prefer `flask db upgrade` (Alembic) for real projects.
    # - For convenience in local/dev setups, we optionally auto-create
    #   tables if they don't exist.
    #
    # This keeps the project easy to run after extracting the zip.
    with app.app_context():
        # Ensure all models are registered on metadata before create_all.
        # This is required because SQLAlchemy only creates tables for
        # models that have been imported.
        from . import models

        # Validate DB connectivity early so failures are clear.
        try:
            db.engine.connect().close()
        except Exception as exc:
            app.logger.error(
                "Database connection failed. Check DATABASE_URL / .env. Error: %s",
                exc,
            )
            # Re-raise so the developer sees a clear error immediately.
            raise

        auto_create = str(app.config.get("AUTO_CREATE_DB", "true")).lower() in {"1", "true", "yes", "on"}
        if auto_create:
            db.create_all()

        insp = inspect(db.engine)

        # --- Safe, additive schema fixes for existing DBs (PostgreSQL) ---
        # create_all() does NOT add missing columns, so older DBs may break
        # login/roles after code updates. These ALTERs are safe to run repeatedly.
        def _colnames(table: str) -> set[str]:
            try:
                return {c["name"] for c in insp.get_columns(table)}
            except Exception:
                return set()

        def _exec(sql: str) -> None:
            db.session.execute(text(sql))
            db.session.commit()

        if db.engine.dialect.name == "postgresql":
            # -----------------------------------------------------------------
            # Ensure core tables exist (idempotent)
            # -----------------------------------------------------------------
            # A common local setup is an older DB that only has `residents`
            # and `documents`. Newer versions of the app require `users` for
            # login and `document_types` + `documents.document_type_id` for
            # document issuance/search.

            def _exec_try(sql: str) -> None:
                """Execute SQL and rollback on failure (keeps startup resilient)."""
                try:
                    db.session.execute(text(sql))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            # --- residents: add missing columns used by the current models ---
            if insp.has_table("residents"):
                cols = _colnames("residents")
                if "middle_name" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS middle_name VARCHAR(100);")
                    insp = inspect(db.engine)
                if "marital_status" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS marital_status VARCHAR(50);")
                    insp = inspect(db.engine)
                if "created_by_id" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS created_by_id INTEGER;")
                if "updated_by_id" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS updated_by_id INTEGER;")
                if "updated_at" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE;")
                if "is_archived" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;")
                if "archived_at" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITHOUT TIME ZONE;")
                if "archived_by_id" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS archived_by_id INTEGER;")
                insp = inspect(db.engine)

                # Indexes for faster search/sort
                _exec_try("CREATE INDEX IF NOT EXISTS ix_residents_last_name ON residents (last_name);")
                _exec_try("CREATE INDEX IF NOT EXISTS ix_residents_barangay_id ON residents (barangay_id);")

            # --- document_types: ensure the table exists (older DBs may not have it) ---
            if not insp.has_table("document_types"):
                _exec_try(
                    """
                    CREATE TABLE IF NOT EXISTS document_types (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(100) NOT NULL UNIQUE,
                        description VARCHAR(255),
                        template_path VARCHAR(255),
                        requires_photo BOOLEAN NOT NULL DEFAULT FALSE
                    );
                    """
                )
                insp = inspect(db.engine)
            else:
                dt_cols = _colnames("document_types")
                if "description" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN IF NOT EXISTS description VARCHAR(255);")
                if "template_path" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN IF NOT EXISTS template_path VARCHAR(255);")
                if "requires_photo" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN IF NOT EXISTS requires_photo BOOLEAN NOT NULL DEFAULT FALSE;")
                insp = inspect(db.engine)

            # --- users: ensure the table exists (required for login) ---
            if not insp.has_table("users"):
                _exec_try(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(150) NOT NULL UNIQUE,
                        email VARCHAR(255) NOT NULL UNIQUE,
                        password_hash VARCHAR(255) NOT NULL,
                        role VARCHAR(50) NOT NULL DEFAULT 'clerk',
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
                    );
                    """
                )
                insp = inspect(db.engine)

            # --- transaction_logs: ensure the table exists (audit trail) ---
            if not insp.has_table("transaction_logs"):
                _exec_try(
                    """
                    CREATE TABLE IF NOT EXISTS transaction_logs (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(id),
                        action VARCHAR(255) NOT NULL,
                        entity_type VARCHAR(50),
                        entity_id INTEGER,
                        ip_address VARCHAR(64),
                        user_agent VARCHAR(255),
                        meta JSONB,
                        timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
                    );
                    """
                )
                insp = inspect(db.engine)

            # --- transaction_logs: add missing columns (safe / additive) ---
            if insp.has_table("transaction_logs"):
                lcols = _colnames("transaction_logs")
                if "entity_type" not in lcols:
                    _exec_try("ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS entity_type VARCHAR(50);")
                if "entity_id" not in lcols:
                    _exec_try("ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS entity_id INTEGER;")
                if "ip_address" not in lcols:
                    _exec_try("ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS ip_address VARCHAR(64);")
                if "user_agent" not in lcols:
                    _exec_try("ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS user_agent VARCHAR(255);")
                if "meta" not in lcols:
                    _exec_try("ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS meta JSONB;")
                if "user_id" in lcols:
                    _exec_try("ALTER TABLE transaction_logs ALTER COLUMN user_id DROP NOT NULL;")
                    _exec_try(
                        """
                        DO $$
                        BEGIN
                            IF EXISTS (
                                SELECT 1 FROM pg_constraint WHERE conname = 'transaction_logs_user_id_fkey'
                            ) THEN
                                ALTER TABLE transaction_logs DROP CONSTRAINT transaction_logs_user_id_fkey;
                            END IF;
                            ALTER TABLE transaction_logs
                            ADD CONSTRAINT transaction_logs_user_id_fkey
                            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
                        END $$;
                        """
                    )
                insp = inspect(db.engine)

            # --- login_attempts: ensure the table exists (rate limiting) ---
            if not insp.has_table("login_attempts"):
                _exec_try(
                    """
                    CREATE TABLE IF NOT EXISTS login_attempts (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(150),
                        ip_address VARCHAR(64),
                        success BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
                    );
                    """
                )
                insp = inspect(db.engine)

            # --- login_mfa_codes: ensure the table exists (admin MFA) ---
            if not insp.has_table("login_mfa_codes"):
                _exec_try(
                    """
                    CREATE TABLE IF NOT EXISTS login_mfa_codes (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        otp_code VARCHAR(20) NOT NULL,
                        expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                        used BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
                    );
                    """
                )
                insp = inspect(db.engine)

            # --- users: add missing columns used by the current models ---
            if insp.has_table("users"):
                ucols = _colnames("users")
                if "email" not in ucols:
                    _exec_try("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255);")
                if "role" not in ucols:
                    _exec_try("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) NOT NULL DEFAULT 'clerk';")
                if "password_hash" not in ucols:
                    _exec_try("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);")
                insp = inspect(db.engine)

            # --- documents: migrate old doc_type string -> document_type_id FK ---
            if insp.has_table("documents"):
                dcols = _colnames("documents")

                if "document_type_id" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS document_type_id INTEGER;")
                    insp = inspect(db.engine)
                    dcols = _colnames("documents")

                if "status" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'draft';")
                _exec_try("UPDATE documents SET status='issued' WHERE status IS NULL;")
                if "created_at" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW();")
                if "updated_at" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE;")
                if "approved_at" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP WITHOUT TIME ZONE;")
                if "issued_at" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS issued_at TIMESTAMP WITHOUT TIME ZONE;")
                if "is_archived" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;")
                if "archived_at" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITHOUT TIME ZONE;")
                if "created_by_id" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_by_id INTEGER;")
                if "updated_by_id" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_by_id INTEGER;")
                if "approved_by_id" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS approved_by_id INTEGER;")
                if "issued_by_id" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS issued_by_id INTEGER;")
                if "archived_by_id" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN IF NOT EXISTS archived_by_id INTEGER;")
                _exec_try("UPDATE documents SET created_at = issue_date WHERE created_at IS NULL;")
                _exec_try("UPDATE documents SET issued_at = issue_date WHERE issued_at IS NULL AND status='issued';")
                insp = inspect(db.engine)

                # If an older schema uses `doc_type` (string), backfill document_types + FK.
                if "doc_type" in dcols:
                    _exec_try(
                        """
                        INSERT INTO document_types(name)
                        SELECT DISTINCT doc_type
                        FROM documents
                        WHERE doc_type IS NOT NULL AND doc_type <> ''
                        ON CONFLICT (name) DO NOTHING;
                        """
                    )
                    _exec_try(
                        """
                        UPDATE documents d
                        SET document_type_id = dt.id
                        FROM document_types dt
                        WHERE d.document_type_id IS NULL
                          AND d.doc_type = dt.name;
                        """
                    )

                
                    # Keep legacy `doc_type` column compatible:
                    # Some older DBs have documents.doc_type as NOT NULL. Newer code inserts only
                    # `document_type_id`, so we set a default and backfill NULLs to avoid crashes.
                    _exec_try("ALTER TABLE documents ALTER COLUMN doc_type SET DEFAULT 'Unknown';")
                    _exec_try("UPDATE documents SET doc_type='Unknown' WHERE doc_type IS NULL;")
# Ensure there's always a fallback type so NOT NULL is safe.
                _exec_try("INSERT INTO document_types(name) VALUES ('Unknown') ON CONFLICT (name) DO NOTHING;")
                _exec_try(
                    """
                    UPDATE documents
                    SET document_type_id = (SELECT id FROM document_types WHERE name='Unknown')
                    WHERE document_type_id IS NULL;
                    """
                )

                # Enforce NOT NULL and add FK constraint (best-effort).
                _exec_try("ALTER TABLE documents ALTER COLUMN document_type_id SET NOT NULL;")
                _exec_try(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conname = 'documents_document_type_id_fkey'
                        ) THEN
                            ALTER TABLE documents
                            ADD CONSTRAINT documents_document_type_id_fkey
                            FOREIGN KEY (document_type_id)
                            REFERENCES document_types (id);
                        END IF;
                    END $$;
                    """
                )

                # Indexes for faster search/sort
                _exec_try("CREATE INDEX IF NOT EXISTS ix_documents_issue_date ON documents (issue_date);")
                _exec_try("CREATE INDEX IF NOT EXISTS ix_documents_resident_id ON documents (resident_id);")
                _exec_try("CREATE INDEX IF NOT EXISTS ix_documents_document_type_id ON documents (document_type_id);")


        # Seed common document types (safe to run repeatedly)
        DEFAULT_DOCUMENT_TYPES = [
            ("Barangay ID", "Identification card issued by the barangay.", True, "barangay_id"),
            ("Barangay Clearance", "General clearance certificate.", False, "barangay_clearance"),
            ("Business Clearance", "Clearance for business permit/renewal.", False, "business_clearance"),
            ("Certificate of Residency", "Certificate of residency.", False, "residency"),
            ("Certificate of Indigency", "Certificate of indigency.", False, "indigency"),
            ("Certificate of Good Moral", "Certificate of good moral character.", False, "good_moral"),
            ("Other Certificate", "Other barangay-issued certificate.", False, "other"),
        ]

        if insp.has_table("document_types"):
            DocumentType = models.DocumentType
            for name, desc, req_photo, template_path in DEFAULT_DOCUMENT_TYPES:
                existing = DocumentType.query.filter_by(name=name).first()
                if not existing:
                    db.session.add(
                        DocumentType(
                            name=name,
                            description=desc,
                            requires_photo=req_photo,
                            template_path=template_path,
                        )
                    )
                else:
                    # Keep existing customizations if present.
                    if not existing.description:
                        existing.description = desc
                    existing.requires_photo = req_photo
                    existing.template_path = template_path

            db.session.commit()
        # Seed a default admin user if no users exist.  The default
        # credentials are username `admin` with password `admin` and
        # email `admin@example.com`.  Administrators can change the
        # password after logging in.  Additional users can be created
        # through the database or via the admin interface.  The email
        # address is used for password reset notifications.
        # Use a raw SQL query to count existing users.  Using the ORM here
        # could fail during migrations if new columns (e.g., email) have
        # not yet been added to the table.  Raw SQL avoids referencing
        # model attributes that may not exist on the physical table.
        if insp.has_table("users"):
            User = models.User
            try:
                user_count = db.session.execute(text("SELECT COUNT(*) FROM users")).scalar()
            except Exception:
                user_count = None
            if user_count == 0:
                admin = User(username="admin", email="admin@example.com", role="admin")
                admin.set_password("admin")
                db.session.add(admin)
                db.session.commit()

    @app.cli.command("init-db")
    def init_db_command():
        """Create all tables and apply safe schema-healing.

        Useful for brand-new databases or after dropping tables.
        """
        with app.app_context():
            db.create_all()
        print("Database initialized.")

    @app.cli.command("backup-db")
    def backup_db_command():
        """Create a timestamped database backup (PostgreSQL or SQLite)."""
        backup_dir = app.config.get("BACKUP_DIR", os.path.join(os.getcwd(), "backups"))
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        url = app.config.get("SQLALCHEMY_DATABASE_URI") or ""

        if url.startswith("sqlite:///"):
            db_path = url.replace("sqlite:///", "", 1)
            if db_path == ":memory:":
                print("Cannot back up an in-memory SQLite database.")
                return
            dest = os.path.join(backup_dir, f"backup_{ts}.sqlite")
            shutil.copy2(db_path, dest)
            print(f"SQLite backup created: {dest}")
        else:
            dest = os.path.join(backup_dir, f"backup_{ts}.dump")
            cmd = ["pg_dump", "-Fc", url, "-f", dest]
            subprocess.run(cmd, check=True)
            print(f"PostgreSQL backup created: {dest}")

        # Retention cleanup
        retention_days = int(app.config.get("BACKUP_RETENTION_DAYS", 7))
        if retention_days > 0:
            cutoff = time.time() - (retention_days * 86400)
            for name in os.listdir(backup_dir):
                path = os.path.join(backup_dir, name)
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)

    @app.cli.command("restore-db")
    @click.option("--path", "backup_path", required=True, type=click.Path(exists=True, dir_okay=False))
    @click.option("--yes", is_flag=True, help="Confirm restore (overwrites existing data).")
    def restore_db_command(backup_path: str, yes: bool):
        """Restore database from a backup file."""
        if not yes:
            print("Refusing to restore without --yes (this will overwrite existing data).")
            return

        url = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
        if url.startswith("sqlite:///"):
            db_path = url.replace("sqlite:///", "", 1)
            if db_path == ":memory:":
                print("Cannot restore an in-memory SQLite database.")
                return
            shutil.copy2(backup_path, db_path)
            print(f"SQLite restored from: {backup_path}")
        else:
            cmd = ["pg_restore", "--clean", "--if-exists", "-d", url, backup_path]
            subprocess.run(cmd, check=True)
            print(f"PostgreSQL restored from: {backup_path}")

    def _add_months(value: dt_date, months: int) -> dt_date:
        month = value.month - 1 + months
        year = value.year + month // 12
        month = month % 12 + 1
        day = min(value.day, monthrange(year, month)[1])
        return dt_date(year, month, day)

    def _process_expired_documents(
        *,
        months: int | None = None,
        grace_days: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, int]:
        from .models import Document, TransactionLog

        months = int(months if months is not None else app.config.get("PURGE_VALIDITY_MONTHS", 6))
        grace_days = int(grace_days if grace_days is not None else app.config.get("PURGE_GRACE_DAYS", 30))
        if months <= 0:
            return {"archived": 0, "deleted": 0, "months": months, "grace_days": grace_days}

        now = datetime.now(timezone.utc)
        today = now.date()

        to_archive = []
        for doc in Document.query.filter(
            Document.status == "issued",
            Document.is_archived.is_(False),
        ).all():
            issue_dt = doc.issue_date.date() if hasattr(doc.issue_date, "date") else doc.issue_date
            if not issue_dt:
                continue
            expiry_dt = _add_months(issue_dt, months)
            if expiry_dt < today:
                to_archive.append(doc)

        cutoff_date = today - timedelta(days=grace_days)
        to_delete = []
        for doc in Document.query.filter(
            Document.status == "issued",
            Document.is_archived.is_(True),
        ).all():
            issue_dt = doc.issue_date.date() if hasattr(doc.issue_date, "date") else doc.issue_date
            if not issue_dt:
                continue
            expiry_dt = _add_months(issue_dt, months)
            if expiry_dt < cutoff_date:
                to_delete.append(doc)

        if dry_run:
            return {"archived": len(to_archive), "deleted": len(to_delete), "months": months, "grace_days": grace_days}

        if to_archive:
            for doc in to_archive:
                doc.is_archived = True
                doc.archived_at = now
                doc.archived_by_id = None
                doc.updated_at = now

        if to_delete:
            static_root = os.path.join(app.root_path, "static")
            for doc in to_delete:
                if doc.file_path:
                    abs_path = os.path.join(static_root, doc.file_path)
                    if os.path.exists(abs_path):
                        try:
                            os.remove(abs_path)
                        except Exception:
                            pass
                db.session.delete(doc)

        if to_archive:
            db.session.add(
                TransactionLog(
                    user_id=None,
                    action="Auto-archived expired documents",
                    entity_type="document",
                    entity_id=None,
                    meta={"count": len(to_archive), "months": months},
                )
            )
        if to_delete:
            db.session.add(
                TransactionLog(
                    user_id=None,
                    action="Auto-deleted expired documents",
                    entity_type="document",
                    entity_id=None,
                    meta={"count": len(to_delete), "grace_days": grace_days},
                )
            )

        if to_archive or to_delete:
            db.session.commit()

        return {"archived": len(to_archive), "deleted": len(to_delete), "months": months, "grace_days": grace_days}

    default_months = int(app.config.get("PURGE_VALIDITY_MONTHS", 6))
    default_grace = int(app.config.get("PURGE_GRACE_DAYS", 30))

    @app.cli.command("purge-expired-documents")
    @click.option("--months", default=default_months, show_default=True, type=int, help="Validity window in months.")
    @click.option("--grace-days", default=default_grace, show_default=True, type=int, help="Days to keep archived before deletion.")
    @click.option("--dry-run", is_flag=True, help="Show counts without making changes.")
    @click.option("--yes", is_flag=True, help="Confirm archiving/deletion of expired documents.")
    def purge_expired_documents(months: int, grace_days: int, dry_run: bool, yes: bool):
        """Archive expired documents, then delete auto-archived ones after a grace period."""
        if not dry_run and not yes:
            print("Refusing to run without --dry-run or --yes.")
            return

        result = _process_expired_documents(months=months, grace_days=grace_days, dry_run=dry_run)
        print(
            "Expired documents: archived={archived}, deleted={deleted} (months={months}, grace_days={grace_days})".format(
                **result
            )
        )
        if dry_run:
            return
        print("Purge complete.")

    def _start_auto_purge_worker() -> None:
        if not app.config.get("AUTO_PURGE_EXPIRED", True):
            return
        if app.testing:
            return
        if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
            return
        if app.extensions.get("auto_purge_started"):
            return

        interval_minutes = int(app.config.get("PURGE_CHECK_INTERVAL_MINUTES", 1440))
        interval_seconds = max(60, interval_minutes * 60)
        stop_event = threading.Event()

        def _worker() -> None:
            app.logger.info("Auto purge worker started (interval=%sm).", interval_minutes)
            while not stop_event.is_set():
                with app.app_context():
                    try:
                        result = _process_expired_documents()
                        if result["archived"] or result["deleted"]:
                            app.logger.info(
                                "Auto purge completed: archived=%s deleted=%s",
                                result["archived"],
                                result["deleted"],
                            )
                    except Exception:
                        app.logger.exception("Auto purge failed.")
                stop_event.wait(interval_seconds)

        thread = threading.Thread(target=_worker, name="auto-purge-expired", daemon=True)
        thread.start()
        app.extensions["auto_purge_started"] = True
        app.extensions["auto_purge_stop"] = stop_event

    @app.before_request
    def _start_auto_purge_on_first_request() -> None:
        _start_auto_purge_worker()

    return app


if __name__ == "__main__":
    # Create an app using the default development configuration
    app = create_app()
    app.run()

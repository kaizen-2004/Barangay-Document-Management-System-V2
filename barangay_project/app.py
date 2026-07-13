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
import sqlite3
import subprocess
import threading
import time
import uuid
from calendar import monthrange
from datetime import date as dt_date, datetime, timedelta, timezone

import click

from flask import Flask, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFError
from flask_login import current_user, logout_user
from werkzeug.exceptions import HTTPException

from .config import DevelopmentConfig
from .extensions import csrf, db, login_manager
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import make_url

# Optional: load environment variables from a .env file if present.
# This makes local setup much smoother and avoids "role USER does not exist"
# errors when DATABASE_URL is only defined in .env.
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    current_app.logger.exception("Failed to load .env file")
    load_dotenv = None
from .routes import main_bp
from .auth import auth_bp
from .admin import admin_bp
from .time_utils import format_local_datetime


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

    frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
    templates_dir = os.environ.get("BARANGAY_TEMPLATES_DIR") or os.path.join(frontend_dir, "templates")
    static_dir = os.environ.get("BARANGAY_STATIC_DIR") or os.path.join(frontend_dir, "static")

    app = Flask(
        __name__,
        template_folder=templates_dir,
        static_folder=static_dir,
        static_url_path="/static",
    )
    app.config.from_object(config_class)

    # Logging configuration
    level_name = str(app.config.get("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)
    app.logger.setLevel(log_level)

    # Initialize extensions
    db.init_app(app)
    Migrate(app, db)

    # Enable WAL mode + foreign keys for SQLite (safe no-op for other backends)
    with app.app_context():
        @event.listens_for(db.engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            if isinstance(dbapi_connection, sqlite3.Connection):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

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
            endpoint = request.endpoint
            if not endpoint:
                return "#"
            args = dict(request.view_args or {})
            args.update(request.args.to_dict(flat=True))
            args["page"] = page
            return url_for(endpoint, **args)

        return {"pagination_url": pagination_url}

    @app.context_processor
    def inject_branding():
        return {
            "system_name": current_app.config.get("SYSTEM_NAME", "Barangay DMS"),
            "barangay_name": current_app.config.get("BARANGAY_NAME", "RENZPONSABLENG BARANGAY"),
            "system_description": current_app.config.get(
                "SYSTEM_DESCRIPTION", "Barangay Document Management and Archiving System"
            ),
            "app_version": current_app.config.get("APP_VERSION", "0.1.0"),
            "app_author": current_app.config.get("APP_AUTHOR", "Steve Villa"),
        }

    @app.context_processor
    def inject_pending_count():
        from .models import Document

        try:
            pending_count = Document.query.filter(
                Document.is_archived.is_(False),
                Document.status.in_(("draft", "pending")),
            ).count()
        except Exception:
            pending_count = 0
        return {"sidebar_pending_count": pending_count}

    app.jinja_env.filters["local_datetime"] = format_local_datetime

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
        """Set security headers (CSP/HSTS/etc.) and caching."""
        # Cache static assets for 1 hour
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        elif request.endpoint and request.endpoint.startswith("static"):
            response.headers["Cache-Control"] = "public, max-age=3600"

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
            current_app.logger.exception("Health check DB query failed")
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

        # Ensure SQLite data directory exists before engine connect
        db_url = make_url(str(app.config.get("SQLALCHEMY_DATABASE_URI") or ""))
        if "sqlite" in (db_url.drivername or ""):
            db_path = db_url.database
            if db_path:
                abs_path = db_path if os.path.isabs(db_path) else os.path.join(os.getcwd(), db_path)
                os.makedirs(os.path.dirname(os.path.abspath(abs_path)), exist_ok=True)

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
                current_app.logger.exception("Failed to load table names")
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
                    current_app.logger.exception("Schema heal (MySQL path) failed")
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
                if "contact_number" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS contact_number VARCHAR(50);")
                if "occupation" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS occupation VARCHAR(120);")
                if "years_on_barangay" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS years_on_barangay INTEGER;")
                if "emergency_contact_name" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS emergency_contact_name VARCHAR(150);")
                if "emergency_contact_relationship" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS emergency_contact_relationship VARCHAR(80);")
                if "emergency_contact_number" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS emergency_contact_number VARCHAR(50);")
                if "emergency_contact_address" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS emergency_contact_address VARCHAR(255);")
                if "street_id" not in cols:
                    _exec_try("ALTER TABLE residents ADD COLUMN IF NOT EXISTS street_id INTEGER;")
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
                        field_config TEXT,
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
                if "template_filename" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN template_filename VARCHAR(255);")
                if "template_version" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN template_version INTEGER NOT NULL DEFAULT 1;")
                if "template_active" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN template_active BOOLEAN NOT NULL DEFAULT FALSE;")
                if "template_uploaded_at" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN template_uploaded_at DATETIME;")
                if "template_uploaded_by_id" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN template_uploaded_by_id INTEGER;")
                if "placeholder_config" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN placeholder_config TEXT;")
                if "field_config" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN field_config TEXT;")
                if "validity_text" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN validity_text VARCHAR(120);")
                if "validity_months" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN validity_months INTEGER;")
                if "requires_photo" not in dt_cols:
                    _exec_try("ALTER TABLE document_types ADD COLUMN IF NOT EXISTS requires_photo BOOLEAN NOT NULL DEFAULT FALSE;")
                insp = inspect(db.engine)

            if not insp.has_table("officials"):
                _exec_try(
                    """
                    CREATE TABLE IF NOT EXISTS officials (
                        id INTEGER PRIMARY KEY AUTO_INCREMENT,
                        full_name VARCHAR(150) NOT NULL,
                        title VARCHAR(100) NOT NULL DEFAULT 'Barangay Captain',
                        signature_path VARCHAR(255),
                        is_active BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME
                    );
                    """
                )
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

            # --- password_reset_codes: ensure the table exists ---
            if not insp.has_table("password_reset_codes"):
                _exec_try(
                    """
                    CREATE TABLE IF NOT EXISTS password_reset_codes (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        code VARCHAR(6) NOT NULL,
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
                if "generated_docx_path" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN generated_docx_path VARCHAR(255);")
                if "generation_status" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN generation_status VARCHAR(50) NOT NULL DEFAULT 'pending';")
                if "generation_error" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN generation_error TEXT;")
                if "field_values" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN field_values TEXT;")
                if "generated_at" not in dcols:
                    _exec_try("ALTER TABLE documents ADD COLUMN generated_at DATETIME;")
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

        # Additive schema fixes for existing databases.  `create_all()` does
        # to existing tables, so keep this generic path before model-based seeding.
        if db.engine.dialect.name != "postgresql":
            def _exec_try_any(sql: str) -> None:
                try:
                    db.session.execute(text(sql))
                    db.session.commit()
                except Exception:
                    current_app.logger.exception("Schema heal (non-MySQL path) failed")
                    db.session.rollback()

            insp = inspect(db.engine)
            if insp.has_table("users"):
                ucols = _colnames("users")
                if "email" not in ucols:
                    _exec_try_any("ALTER TABLE users ADD COLUMN email VARCHAR(255);")
            if not insp.has_table("password_reset_codes"):
                _exec_try_any(
                    "CREATE TABLE IF NOT EXISTS password_reset_codes ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "user_id INTEGER NOT NULL REFERENCES users(id), "
                    "code VARCHAR(6) NOT NULL, "
                    "expires_at DATETIME NOT NULL, "
                    "used BOOLEAN NOT NULL DEFAULT 0, "
                    "created_at DATETIME NOT NULL)"
                )
            if insp.has_table("document_types"):
                dt_cols = _colnames("document_types")
                if "field_config" not in dt_cols:
                    _exec_try_any("ALTER TABLE document_types ADD COLUMN field_config TEXT;")
                insp = inspect(db.engine)
            if insp.has_table("documents"):
                dcols = _colnames("documents")
                if "field_values" not in dcols:
                    _exec_try_any("ALTER TABLE documents ADD COLUMN field_values TEXT;")
                insp = inspect(db.engine)
            if not insp.has_table("barangay_streets"):
                _exec_try_any(
                    "CREATE TABLE barangay_streets (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL UNIQUE, created_at DATETIME);"
                )
                insp = inspect(db.engine)
            if insp.has_table("residents"):
                rcols = _colnames("residents")
                if "street_id" not in rcols:
                    _exec_try_any("ALTER TABLE residents ADD COLUMN street_id INTEGER;")
                if "contact_number" not in rcols:
                    _exec_try_any("ALTER TABLE residents ADD COLUMN contact_number VARCHAR(50);")
                if "occupation" not in rcols:
                    _exec_try_any("ALTER TABLE residents ADD COLUMN occupation VARCHAR(120);")
                if "years_on_barangay" not in rcols:
                    _exec_try_any("ALTER TABLE residents ADD COLUMN years_on_barangay INTEGER;")
                if "emergency_contact_name" not in rcols:
                    _exec_try_any("ALTER TABLE residents ADD COLUMN emergency_contact_name VARCHAR(150);")
                if "emergency_contact_relationship" not in rcols:
                    _exec_try_any("ALTER TABLE residents ADD COLUMN emergency_contact_relationship VARCHAR(80);")
                if "emergency_contact_number" not in rcols:
                    _exec_try_any("ALTER TABLE residents ADD COLUMN emergency_contact_number VARCHAR(50);")
                if "emergency_contact_address" not in rcols:
                    _exec_try_any("ALTER TABLE residents ADD COLUMN emergency_contact_address VARCHAR(255);")
                insp = inspect(db.engine)

        # Seed common document types (safe to run repeatedly)
        DEFAULT_DOCUMENT_TYPES = [
            ("Barangay ID", "Identification card issued by the barangay.", True),
            ("Barangay Clearance", "General clearance certificate.", False),
            ("Business Clearance", "Clearance for business permit/renewal.", False),
            ("Certificate of Residency", "Certificate of residency.", False),
            ("Certificate of Indigency", "Certificate of indigency.", False),
            ("Certificate of Good Moral", "Certificate of good moral character.", False),
            ("Other Certificate", "Other barangay-issued certificate.", False),
        ]

        if insp.has_table("document_types"):
            DocumentType = models.DocumentType
            for name, desc, req_photo in DEFAULT_DOCUMENT_TYPES:
                existing = DocumentType.query.filter_by(name=name).first()
                if not existing:
                    db.session.add(
                        DocumentType(
                            name=name,
                            description=desc,
                            requires_photo=req_photo,
                        )
                    )
                else:
                    # Keep existing customizations if present.
                    if not existing.description:
                        existing.description = desc
                    existing.requires_photo = req_photo

            db.session.commit()

            # Auto-activate document types that have a template file
            inactive_with_template = DocumentType.query.filter(
                DocumentType.template_path.isnot(None),
                DocumentType.template_active.is_(False),
            ).all()
            for dt in inactive_with_template:
                dt.template_active = True
            if inactive_with_template:
                db.session.commit()

        DEFAULT_STREETS = [
            "Alonzo Street",
            "Angeles Street",
            "B. Baluyot Street",
            "E. Ramos Street",
            "Eugenio Street",
            "I. Francisco Alley",
            "J. Panganiban Street",
            "Kabalitang Street",
            "Lieutenant J. Francisco Street",
            "M. Dela Cruz Street",
            "Marilag Street",
            "P. Fernando Street",
            "P. Francisco Street",
            "Plaza Hernandez Street",
            "Plaza Sta. Ines",
            "S. Flores Street",
            "S. Salvador Street",
            "S. Santos Street",
            "T. Fulgencio Extension",
            "T. Fulgencio Street",
            "Tiburcio Street",
            "Tiburcio Street Extension",
            "V. Francisco Street",
            "V. Gonzales Street",
            "V. Manansala Street",
        ]

        if inspect(db.engine).has_table("barangay_streets"):
            BarangayStreet = models.BarangayStreet
            for street_name in DEFAULT_STREETS:
                if not BarangayStreet.query.filter_by(name=street_name).first():
                    db.session.add(BarangayStreet(name=street_name))
            db.session.commit()
        # Seed a default admin user if no users exist.
        # Use a raw SQL query to count existing users to keep this resilient
        # against legacy schemas.
        if insp.has_table("users"):
            User = models.User
            try:
                user_count = db.session.execute(text("SELECT COUNT(*) FROM users")).scalar()
            except Exception:
                current_app.logger.exception("Failed to check user count for seed")
                user_count = None
            if user_count == 0:
                admin = User(username="admin", role="admin")
                admin.set_password("admin")
                db.session.add(admin)
                db.session.commit()

        # Seed default placeholders if table exists and is empty
        if insp.has_table("placeholders"):
            Placeholder = models.Placeholder
            if Placeholder.query.count() == 0:
                default_placeholders = [
                    ("resident_name", "Resident Information"),
                    ("first_name", "Resident Information"),
                    ("middle_name", "Resident Information"),
                    ("last_name", "Resident Information"),
                    ("address", "Resident Information"),
                    ("birth_date", "Resident Information"),
                    ("marital_status", "Resident Information"),
                    ("emergency_contact_name", "Emergency Contact"),
                    ("emergency_contact_number", "Emergency Contact"),
                    ("emergency_contact_relationship", "Emergency Contact"),
                    ("emergency_contact_address", "Emergency Contact"),
                    ("document_id", "Document Details"),
                    ("document_type", "Document Details"),
                    ("purpose", "Document Details"),
                    ("issue_date", "Document Details"),
                    ("author", "Document Details"),
                    ("validity", "Validity & Expiry"),
                    ("expiration_date", "Validity & Expiry"),
                    ("year_on_barangay", "Validity & Expiry"),
                    ("resident_photo", "Media & Signatures"),
                    ("qr_code", "Media & Signatures"),
                    ("captain_name", "Media & Signatures"),
                ]
                for name, group in default_placeholders:
                    db.session.add(Placeholder(name=name, group=group))
                db.session.commit()

    @app.cli.command("init-db")
    def init_db_command():
        """Create all tables and apply safe schema-healing.

        Useful for brand-new databases or after dropping tables.
        """
        with app.app_context():
            db.create_all()
        click.echo("Database initialized.")

    @app.cli.command("backup-db")
    def backup_db_command():
        """Create a timestamped database backup."""
        backup_dir = app.config.get("BACKUP_DIR", os.path.join(os.getcwd(), "backups"))
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        url = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
        parsed = make_url(url)

        if "sqlite" in (parsed.drivername or ""):
            db_path = parsed.database
            if not db_path or not os.path.isfile(db_path):
                click.echo("SQLite database file not found.")
                return
            dest = os.path.join(backup_dir, f"backup_{ts}.db")
            shutil.copy2(db_path, dest)
            click.echo(f"Database backup created: {dest}")
        else:
            if not parsed.drivername.startswith("mysql"):
                click.echo("Only MySQL/MariaDB is supported in this deployment mode.")
                return
            if not parsed.database:
                click.echo("DATABASE_URL must include a database name.")
                return
            args = [
                f"--host={parsed.host or '127.0.0.1'}",
                f"--port={parsed.port or 3306}",
                f"--user={parsed.username or 'root'}",
            ]
            if parsed.password:
                args.append(f"--password={parsed.password}")
            dest = os.path.join(backup_dir, f"backup_{ts}.sql")
            mysqldump_bin = app.config.get("MYSQLDUMP_BIN", "mysqldump")
            cmd = [mysqldump_bin, *args, parsed.database, f"--result-file={dest}", "--single-transaction", "--quick"]
            subprocess.run(cmd, check=True)
            click.echo(f"MySQL backup created: {dest}")

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
            click.echo("Refusing to restore without --yes (this will overwrite existing data).")
            return

        url = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
        parsed = make_url(url)

        if "sqlite" in (parsed.drivername or ""):
            db_path = parsed.database
            if not db_path:
                click.echo("SQLite database path not found in DATABASE_URL.")
                return
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            shutil.copy2(backup_path, db_path)
            click.echo(f"Database restored from: {backup_path}")
        else:
            if not parsed.drivername.startswith("mysql"):
                click.echo("Only MySQL/MariaDB is supported in this deployment mode.")
                return
            if not parsed.database:
                click.echo("DATABASE_URL must include a database name.")
                return
            args = [
                f"--host={parsed.host or '127.0.0.1'}",
                f"--port={parsed.port or 3306}",
                f"--user={parsed.username or 'root'}",
            ]
            if parsed.password:
                args.append(f"--password={parsed.password}")
            mysql_bin = app.config.get("MYSQL_BIN", "mysql")
            cmd = [mysql_bin, *args, parsed.database]
            with open(backup_path, "rb") as infile:
                subprocess.run(cmd, check=True, stdin=infile)
            click.echo(f"MySQL restored from: {backup_path}")

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
            from .docx_pipeline import resolve_stored_path_to_abs

            for doc in to_delete:
                if doc.file_path:
                    abs_path = resolve_stored_path_to_abs(doc.file_path)
                    if abs_path and abs_path.exists():
                        try:
                            abs_path.unlink()
                        except Exception:
                            current_app.logger.exception("Failed to delete file in expired doc cleanup")
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
            click.echo("Refusing to run without --dry-run or --yes.")
            return

        result = _process_expired_documents(months=months, grace_days=grace_days, dry_run=dry_run)
        click.echo(
            "Expired documents: archived={archived}, deleted={deleted} (months={months}, grace_days={grace_days})".format(
                **result
            )
        )
        if dry_run:
            return
        click.echo("Purge complete.")

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

    # ------------------------------------------------------------------
    # Custom error pages (404, 403, 500)
    # ------------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    return app


if __name__ == "__main__":
    # Create an app using the default development configuration
    app = create_app()
    app.run()

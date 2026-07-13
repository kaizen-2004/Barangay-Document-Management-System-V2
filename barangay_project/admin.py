"""
Administrative blueprint for managing users in the Barangay Document
Management System.

This module defines routes that allow administrators to list existing
users and add new users to the system.  Access to these routes is
restricted to authenticated users with the 'admin' role.
"""
import os
import shutil
import subprocess
import io
import json
from werkzeug.utils import secure_filename
from datetime import datetime, timezone

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_file
from flask_login import login_required, current_user

from sqlalchemy import or_, text
from sqlalchemy.engine import make_url

from .extensions import db
from .helpers import log_action, roles_required, save_uploaded_image
from .models import BarangayStreet, Placeholder, Resident, TransactionLog, User, Document, DocumentType, Official, PasswordResetCode
from .forms import BarangayStreetForm, EditUserForm, UserForm, DeleteForm, DocumentTypeForm, OfficialForm
from .docx_pipeline import (
    store_template_upload,
    validate_template_with_required,
    REQUIRED_PLACEHOLDERS,
    remove_template_file,
    resolve_stored_path_to_abs,
    template_storage_dir,
    document_output_dir,
    preview_document_type_template,
    DocumentGenerationError,
    parse_docx_placeholders,
)
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _validate_field_config(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    parsed = json.loads(raw)
    fields = parsed.get("fields") if isinstance(parsed, dict) else None
    if not isinstance(fields, list):
        raise ValueError("field config must be an object with a 'fields' list")
    seen: set[str] = set()
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("each field must be an object")
        name = str(field.get("name", "")).strip()
        label = str(field.get("label", "")).strip()
        field_type = str(field.get("type", "text")).strip() or "text"
        if not name or not name.replace("_", "").isalnum():
            raise ValueError("field names may only contain letters, numbers, and underscores")
        if name in seen:
            raise ValueError(f"duplicate field name: {name}")
        if field_type not in {"text", "textarea", "date", "number"}:
            raise ValueError(f"unsupported field type for {name}: {field_type}")
        if not label:
            field["label"] = name.replace("_", " ").title()
        field["name"] = name
        field["type"] = field_type
        field["required"] = bool(field.get("required"))
        seen.add(name)
    return json.dumps({"fields": fields}, indent=2)


def _field_config_names(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except Exception:
        current_app.logger.exception("Failed to fetch admin stats")
        return set()
    fields = parsed.get("fields") if isinstance(parsed, dict) else []
    if not isinstance(fields, list):
        return set()
    return {str(field.get("name", "")).strip() for field in fields if isinstance(field, dict) and str(field.get("name", "")).strip()}


def _backup_db(backup_dir: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    url = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    parsed = make_url(url)

    if "sqlite" in (parsed.drivername or ""):
        db_path = parsed.database
        if not db_path or not os.path.isfile(db_path):
            raise RuntimeError("SQLite database file not found.")
        db.session.commit()
        with db.engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        dest = os.path.join(backup_dir, f"backup_{ts}.db")
        import shutil
        shutil.copy2(db_path, dest)
        return dest

    if not parsed.drivername.startswith("mysql"):
        raise RuntimeError("Only MySQL/MariaDB is supported in this deployment mode.")
    if not parsed.database:
        raise RuntimeError("DATABASE_URL must include a database name.")

    args = [
        f"--host={parsed.host or '127.0.0.1'}",
        f"--port={parsed.port or 3306}",
        f"--user={parsed.username or 'root'}",
    ]
    if parsed.password:
        args.append(f"--password={parsed.password}")
    dest = os.path.join(backup_dir, f"backup_{ts}.sql")
    mysqldump_bin = current_app.config.get("MYSQLDUMP_BIN", "mysqldump")
    cmd = [mysqldump_bin, *args, parsed.database, f"--result-file={dest}", "--single-transaction", "--quick"]
    subprocess.run(cmd, check=True)
    return dest


def _restore_db(backup_path: str) -> None:
    url = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    db.session.remove()
    db.engine.dispose()
    parsed = make_url(url)

    if "sqlite" in (parsed.drivername or ""):
        db_path = parsed.database
        if not db_path:
            raise RuntimeError("SQLite database path not found.")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        import shutil
        shutil.copy2(backup_path, db_path)
        return

    if not parsed.drivername.startswith("mysql"):
        raise RuntimeError("Only MySQL/MariaDB is supported in this deployment mode.")
    if not parsed.database:
        raise RuntimeError("DATABASE_URL must include a database name.")
    if not backup_path.endswith(".sql"):
        raise RuntimeError("Selected backup does not look like a MySQL SQL dump.")

    args = [
        f"--host={parsed.host or '127.0.0.1'}",
        f"--port={parsed.port or 3306}",
        f"--user={parsed.username or 'root'}",
    ]
    if parsed.password:
        args.append(f"--password={parsed.password}")
    mysql_bin = current_app.config.get("MYSQL_BIN", "mysql")
    cmd = [mysql_bin, *args, parsed.database]
    try:
        with open(backup_path, "rb") as infile:
            subprocess.run(cmd, check=True, stdin=infile, capture_output=True)
    except subprocess.CalledProcessError as exc:
        detail = ((exc.stderr or b"") + (exc.stdout or b"")).decode(errors="ignore").strip() or str(exc)
        raise RuntimeError(detail) from exc


def _list_backups(backup_dir: str) -> list[dict]:
    if not os.path.isdir(backup_dir):
        return []
    items = []
    for name in os.listdir(backup_dir):
        path = os.path.join(backup_dir, name)
        if not os.path.isfile(path):
            continue
        items.append(
            {
                "name": name,
                "path": path,
                "size": os.path.getsize(path),
                "mtime": datetime.fromtimestamp(os.path.getmtime(path)),
            }
        )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _parse_date_param(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def _get_db_size_bytes() -> int | None:
    try:
        if db.engine.dialect.name == "mysql":
            return db.session.execute(text("SELECT SUM(data_length + index_length) FROM information_schema.tables WHERE table_schema = DATABASE()")).scalar()
        db_url = make_url(str(db.engine.url))
        db_path = getattr(db_url, "database", None)
        if db_path and os.path.isfile(db_path):
            return os.path.getsize(db_path)
        return None
    except Exception:
        current_app.logger.exception("Failed to get database size")
        return None


@admin_bp.route("/audit")
@login_required
@roles_required("admin")
def audit_logs():
    """View recent audit log entries."""
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))
    query = TransactionLog.query.outerjoin(User)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(TransactionLog.action.ilike(like), User.username.ilike(like)))
    query = query.order_by(TransactionLog.timestamp.desc())
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    logs = pagination.items
    return render_template("audit_logs.html", logs=logs, q=q, pagination=pagination)


@admin_bp.route("/users")
@login_required
@roles_required("admin")
def list_users():
    """Display a list of all user accounts for administrators."""
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))

    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(User.username.ilike(like), User.role.ilike(like))
        )
    query = query.order_by(User.username.asc())
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    users = pagination.items
    delete_form = DeleteForm()
    return render_template("users.html", users=users, delete_form=delete_form, q=q, pagination=pagination)


@admin_bp.route("/users/add", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def add_user():
    """Render and process the form for adding a new user."""
    form = UserForm()
    if form.validate_on_submit():
        existing = User.query.filter(User.username.ilike(form.username.data.strip())).first()
        if existing:
            form.username.errors.append("Username already exists.")
            return render_template("user_form.html", form=form)
        user = User(
            username=form.username.data,
            email=form.email.data.strip().lower() if form.email.data else None,
            role=form.role.data,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        log_action(
            "Created user",
            entity_type="user",
            entity_id=user.id,
            meta={"username": user.username, "role": user.role},
        )
        flash("User created successfully.", "success")
        return redirect(url_for("admin.list_users"))
    return render_template("user_form.html", form=form)


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def edit_user(user_id: int):
    """Render and process the form for editing an existing user.

    Administrators can change a user's username, role and optionally
    reset the user's password by providing a new password.  If the
    password field is left blank, the existing password remains
    unchanged.
    """
    user = db.get_or_404(User, user_id)
    form = EditUserForm(obj=user)
    if form.validate_on_submit():
        # Prevent administrators from demoting themselves to a non-admin role
        if user.id == current_user.id and form.role.data != "admin":
            flash("You cannot change your own role from admin.", "danger")
            return redirect(url_for("admin.edit_user", user_id=user.id))

        conflict = User.query.filter(
            User.username.ilike(form.username.data.strip()),
            User.id != user.id,
        ).first()
        if conflict:
            form.username.errors.append("Username already exists.")
            return render_template("user_edit_form.html", form=form, user=user)

        user.username = form.username.data
        user.email = form.email.data.strip().lower() if form.email.data else None
        user.role = form.role.data
        # Only set a new password if one was provided
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        # Log the update
        log_action(
            "Updated user",
            entity_type="user",
            entity_id=user.id,
            meta={"username": user.username, "role": user.role},
        )
        flash("User updated successfully.", "success")
        return redirect(url_for("admin.list_users"))
    return render_template("user_edit_form.html", form=form, user=user)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_user(user_id: int):
    """Delete a user account.

    Administrators cannot delete themselves to prevent accidental
    lockout.  After deletion, a log entry is created.
    """
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("admin.list_users"))
    username = user.username

    # Preserve audit logs by detaching user references before deletion.
    TransactionLog.query.filter_by(user_id=user.id).update({"user_id": None}, synchronize_session=False)
    PasswordResetCode.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    db.session.delete(user)
    db.session.commit()
    # Log the deletion
    log_action(
        "Deleted user",
        entity_type="user",
        meta={"username": username},
    )
    flash("User deleted successfully.", "success")
    return redirect(url_for("admin.list_users"))


# ------------------------------
# Placeholder management
# ------------------------------


@admin_bp.route("/placeholders")
@login_required
@roles_required("admin")
def list_placeholders():
    placeholders = Placeholder.query.order_by(Placeholder.group, Placeholder.name).all()
    return render_template("placeholders.html", placeholders=placeholders)


@admin_bp.route("/placeholders/add", methods=["POST"])
@login_required
@roles_required("admin")
def add_placeholder():
    name = (request.form.get("name") or "").strip().lower().replace(" ", "_")
    group = (request.form.get("group") or "").strip() or None
    if not name:
        flash("Placeholder name is required.", "danger")
        return redirect(url_for("admin.list_placeholders"))
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        flash("Placeholder name must start with a letter and contain only letters, numbers, and underscores.", "danger")
        return redirect(url_for("admin.list_placeholders"))
    existing = Placeholder.query.filter_by(name=name).first()
    if existing:
        flash(f"Placeholder '{name}' already exists.", "danger")
        return redirect(url_for("admin.list_placeholders"))
    db.session.add(Placeholder(name=name, group=group))
    db.session.commit()
    log_action("Added placeholder", entity_type="placeholder", meta={"name": name, "group": group})
    flash(f"Placeholder '{name}' added.", "success")
    return redirect(url_for("admin.list_placeholders"))


@admin_bp.route("/placeholders/<int:ph_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_placeholder(ph_id: int):
    ph = db.get_or_404(Placeholder, ph_id)
    db.session.delete(ph)
    db.session.commit()
    log_action("Deleted placeholder", entity_type="placeholder", meta={"name": ph.name})
    flash(f"Placeholder '{ph.name}' deleted.", "success")
    return redirect(url_for("admin.list_placeholders"))


# ------------------------------
# Document type management
# ------------------------------


@admin_bp.route("/document-types")
@login_required
@roles_required("admin")
def list_document_types():
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))
    query = DocumentType.query.order_by(DocumentType.name.asc())
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    document_types = pagination.items
    delete_form = DeleteForm()
    return render_template(
        "document_types.html",
        document_types=document_types,
        delete_form=delete_form,
        pagination=pagination,
        template_storage_dir=str(template_storage_dir()),
        output_storage_dir=str(document_output_dir()),
    )


@admin_bp.route("/streets")
@login_required
@roles_required("admin")
def list_streets():
    streets = BarangayStreet.query.order_by(BarangayStreet.name.asc()).all()
    return render_template("streets.html", streets=streets)


@admin_bp.route("/streets/add", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def add_street():
    form = BarangayStreetForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if BarangayStreet.query.filter_by(name=name).first():
            form.name.errors.append("Street already exists.")
        else:
            street = BarangayStreet(name=name)
            db.session.add(street)
            db.session.commit()
            log_action("Created street", entity_type="street", entity_id=street.id, meta={"name": street.name})
            flash("Street added.", "success")
            return redirect(url_for("admin.list_streets"))
    return render_template("street_form.html", form=form, title="Add Street", submit_label="Create")


@admin_bp.route("/streets/<int:street_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def edit_street(street_id: int):
    street = db.get_or_404(BarangayStreet, street_id)
    form = BarangayStreetForm(obj=street)
    if form.validate_on_submit():
        name = form.name.data.strip()
        existing = BarangayStreet.query.filter(BarangayStreet.name == name, BarangayStreet.id != street.id).first()
        if existing:
            form.name.errors.append("Street already exists.")
        else:
            street.name = name
            db.session.commit()
            log_action("Updated street", entity_type="street", entity_id=street.id, meta={"name": street.name})
            flash("Street updated.", "success")
            return redirect(url_for("admin.list_streets"))
    return render_template("street_form.html", form=form, title="Edit Street", submit_label="Update")


@admin_bp.route("/streets/<int:street_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_street(street_id: int):
    street = db.get_or_404(BarangayStreet, street_id)
    if Resident.query.filter_by(street_id=street.id).first():
        flash("Cannot delete a street that is assigned to residents.", "danger")
        return redirect(url_for("admin.list_streets"))
    street_name = street.name
    db.session.delete(street)
    db.session.commit()
    log_action("Deleted street", entity_type="street", entity_id=street_id, meta={"name": street_name})
    flash("Street deleted.", "success")
    return redirect(url_for("admin.list_streets"))


def _parse_validity_months(value) -> int | None:
    try:
        v = int(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


@admin_bp.route("/document-types/detect-placeholders", methods=["POST"])
@login_required
@roles_required("admin")
def detect_template_placeholders():
    upload = request.files.get("template_file")
    if not upload or not upload.filename:
        return {"success": False, "error": "No template file provided."}, 400
    if not upload.filename.lower().endswith(".docx"):
        return {"success": False, "error": "Only .docx files are supported."}, 400
    try:
        tmp_path = template_storage_dir() / f"_detect_{upload.filename}"
        upload.save(str(tmp_path))
        detected = sorted(parse_docx_placeholders(str(tmp_path)))
        tmp_path.unlink(missing_ok=True)
        return {"success": True, "placeholders": detected}
    except Exception as exc:
        current_app.logger.exception("Placeholder detection failed")
        return {"success": False, "error": str(exc)}, 500


@admin_bp.route("/document-types/add", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def add_document_type():
    form = DocumentTypeForm()
    ph_query = lambda: Placeholder.query.order_by(Placeholder.group, Placeholder.name).all()
    if form.validate_on_submit():
        placeholder_config_raw = (form.placeholder_config.data or "").strip()
        try:
            field_config_raw = _validate_field_config(form.field_config.data or "")
        except Exception as exc:
            current_app.logger.exception("Failed to delete user")
            flash(f"Invalid fill-out fields JSON: {exc}", "danger")
            return render_template("document_type_form.html", form=form, title="Add Document Type", submit_label="Create", placeholders=ph_query())
        if placeholder_config_raw:
            try:
                parsed = json.loads(placeholder_config_raw)
                if not isinstance(parsed, dict) or not isinstance(parsed.get("required", []), list):
                    raise ValueError("placeholder config must be an object with a 'required' list")
                required_placeholders = {str(x).strip() for x in parsed.get("required", []) if str(x).strip()}
            except Exception as exc:
                current_app.logger.exception("Failed to save street")
                flash(f"Invalid placeholder config JSON: {exc}", "danger")
                return render_template("document_type_form.html", form=form, title="Add Document Type", submit_label="Create", placeholders=ph_query())
        else:
            required_placeholders = set()

        template_path = None
        template_filename = None
        template_version = 1
        upload = request.files.get("template_file")
        if upload and upload.filename:
            try:
                template_path, template_filename = store_template_upload(upload, form.name.data.strip())
                abs_template = resolve_stored_path_to_abs(template_path)
                if not required_placeholders:
                    detected = parse_docx_placeholders(str(abs_template))
                    if detected:
                        required_placeholders = detected
                        placeholder_config_raw = json.dumps({"required": sorted(detected)})
                        flash("Placeholders auto-detected from template: " + ", ".join(sorted(detected)), "info")
                validation = validate_template_with_required(str(abs_template), required_placeholders, extra_allowed=_field_config_names(field_config_raw))
                if validation["missing"]:
                    flash(
                        "Template is missing required placeholders: " + ", ".join(validation["missing"]),
                        "danger",
                    )
                    return render_template("document_type_form.html", form=form, title="Add Document Type", submit_label="Create", placeholders=ph_query())
                if validation["unknown"]:
                    flash(
                        "Template has unknown placeholders: " + ", ".join(validation["unknown"]),
                        "warning",
                    )
            except Exception as exc:
                current_app.logger.exception("Failed to delete street")
                flash(f"Template upload failed: {exc}", "danger")
                return render_template("document_type_form.html", form=form, title="Add Document Type", submit_label="Create", placeholders=ph_query())

        dt = DocumentType(
            name=form.name.data.strip(),
            description=form.description.data.strip() if form.description.data else None,
            template_path=template_path,
            template_filename=template_filename,
            template_version=template_version,
            template_active=bool(template_path),
            template_uploaded_by_id=current_user.id if template_path else None,
            template_uploaded_at=datetime.now(timezone.utc) if template_path else None,
            placeholder_config=placeholder_config_raw or None,
            field_config=field_config_raw,
            validity_months=_parse_validity_months(form.validity_months.data),
            requires_photo=bool(form.requires_photo.data),
        )
        db.session.add(dt)
        db.session.commit()
        log_action("Created document type", entity_type="document_type", entity_id=dt.id, meta={"name": dt.name})
        flash("Document type added.", "success")
        return redirect(url_for("admin.list_document_types"))
    return render_template("document_type_form.html", form=form, title="Add Document Type", submit_label="Create", placeholders=Placeholder.query.order_by(Placeholder.group, Placeholder.name).all())


@admin_bp.route("/document-types/<int:type_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def edit_document_type(type_id: int):
    dt = db.get_or_404(DocumentType, type_id)
    form = DocumentTypeForm(obj=dt)
    ph_query = lambda: Placeholder.query.order_by(Placeholder.group, Placeholder.name).all()
    if request.method == "GET":
        form.field_config.data = dt.field_config or ""
    if form.validate_on_submit():
        placeholder_config_raw = (form.placeholder_config.data or "").strip()
        try:
            field_config_raw = _validate_field_config(form.field_config.data or "")
        except Exception as exc:
            current_app.logger.exception("Failed to save backup config")
            flash(f"Invalid fill-out fields JSON: {exc}", "danger")
            return render_template("document_type_form.html", form=form, title="Edit Document Type", submit_label="Update", document_type=dt, placeholders=ph_query())
        if placeholder_config_raw:
            try:
                parsed = json.loads(placeholder_config_raw)
                if not isinstance(parsed, dict) or not isinstance(parsed.get("required", []), list):
                    raise ValueError("placeholder config must be an object with a 'required' list")
                required_placeholders = {str(x).strip() for x in parsed.get("required", []) if str(x).strip()}
            except Exception as exc:
                current_app.logger.exception("Failed to save document type")
                flash(f"Invalid placeholder config JSON: {exc}", "danger")
                return render_template("document_type_form.html", form=form, title="Edit Document Type", submit_label="Update", document_type=dt, placeholders=ph_query())
        else:
            required_placeholders = set()

        dt.name = form.name.data.strip()
        dt.description = form.description.data.strip() if form.description.data else None
        dt.validity_months = _parse_validity_months(form.validity_months.data)
        dt.validity_text = None  # migrate old value away
        upload = request.files.get("template_file")
        if upload and upload.filename:
            old_template_path = dt.template_path
            try:
                template_path, template_filename = store_template_upload(upload, dt.name)
                abs_template = resolve_stored_path_to_abs(template_path)
                if not required_placeholders:
                    detected = parse_docx_placeholders(str(abs_template))
                    if detected:
                        required_placeholders = detected
                        placeholder_config_raw = json.dumps({"required": sorted(detected)})
                        flash("Placeholders auto-detected from template: " + ", ".join(sorted(detected)), "info")
                validation = validate_template_with_required(str(abs_template), required_placeholders, extra_allowed=_field_config_names(field_config_raw))
                if validation["missing"]:
                    flash(
                        "Template is missing required placeholders: " + ", ".join(validation["missing"]),
                        "danger",
                    )
                    return render_template("document_type_form.html", form=form, title="Edit Document Type", submit_label="Update", document_type=dt, placeholders=ph_query())
                if validation["unknown"]:
                    flash(
                        "Template has unknown placeholders: " + ", ".join(validation["unknown"]),
                        "warning",
                    )
                dt.template_path = template_path
                dt.template_filename = template_filename
                dt.template_uploaded_by_id = current_user.id
                dt.template_uploaded_at = datetime.now(timezone.utc)
                dt.template_version = int(dt.template_version or 0) + 1
                if old_template_path and old_template_path != template_path:
                    remove_template_file(old_template_path)
            except Exception as exc:
                current_app.logger.exception("Failed to delete document type")
                flash(f"Template upload failed: {exc}", "danger")
                return render_template("document_type_form.html", form=form, title="Edit Document Type", submit_label="Update", document_type=dt, placeholders=ph_query())

        # Auto-activate when a new template file is uploaded; otherwise respect the checkbox
        if upload and upload.filename:
            dt.template_active = True
        elif not dt.template_path:
            dt.template_active = False
        else:
            dt.template_active = bool(form.template_active.data)
        dt.placeholder_config = placeholder_config_raw or None
        dt.field_config = field_config_raw
        dt.requires_photo = bool(form.requires_photo.data)
        db.session.commit()
        log_action("Updated document type", entity_type="document_type", entity_id=dt.id, meta={"name": dt.name})
        flash("Document type updated.", "success")
        return redirect(url_for("admin.list_document_types"))
    return render_template("document_type_form.html", form=form, title="Edit Document Type", submit_label="Update", document_type=dt, placeholders=ph_query())


@admin_bp.route("/document-types/<int:type_id>/preview")
@login_required
@roles_required("admin")
def preview_document_type(type_id: int):
    """Render the document-type template with dummy data and return the DOCX."""
    dt = db.get_or_404(DocumentType, type_id)
    if not dt.template_path or not dt.template_active:
        flash("This document type has no active template to preview.", "warning")
        return redirect(url_for("admin.edit_document_type", type_id=dt.id))
    try:
        docx_bytes, filename = preview_document_type_template(dt)
        return send_file(
            io.BytesIO(docx_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except DocumentGenerationError as exc:
        flash(f"Preview failed: {exc}", "danger")
        return redirect(url_for("admin.edit_document_type", type_id=dt.id))


@admin_bp.route("/document-types/<int:type_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_document_type(type_id: int):
    dt = db.get_or_404(DocumentType, type_id)
    in_use = Document.query.filter(Document.document_type_id == dt.id).first()
    if in_use:
        flash("Cannot delete a document type that is already in use.", "danger")
        return redirect(url_for("admin.list_document_types"))
    db.session.delete(dt)
    db.session.commit()
    log_action("Deleted document type", entity_type="document_type", entity_id=type_id, meta={"name": dt.name})
    flash("Document type deleted.", "success")
    return redirect(url_for("admin.list_document_types"))


@admin_bp.route("/document-types/cleanup-templates", methods=["POST"])
@login_required
@roles_required("admin")
def cleanup_document_type_templates():
    root = template_storage_dir()
    referenced: set[str] = set()
    for dt in DocumentType.query.all():
        if dt.template_path:
            referenced.add(str(dt.template_path).strip().lstrip("/"))

    deleted = 0
    for path in root.rglob("*.docx"):
        rel = str(path.relative_to(root).as_posix())
        if rel not in referenced:
            try:
                path.unlink()
                deleted += 1
            except Exception:
                current_app.logger.exception("Failed to cleanup template file")
                continue

    log_action("Cleaned up orphaned document templates", entity_type="document_type", meta={"deleted": deleted})
    flash(f"Template cleanup finished. Deleted {deleted} orphaned file(s).", "success")
    return redirect(url_for("admin.list_document_types"))


@admin_bp.route("/officials")
@login_required
@roles_required("admin")
def list_officials():
    officials = Official.query.order_by(Official.is_active.desc(), Official.full_name.asc()).all()
    return render_template("officials.html", officials=officials)


@admin_bp.route("/officials/add", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def add_official():
    form = OfficialForm()
    if form.validate_on_submit():
        official = Official(
            full_name=form.full_name.data.strip(),
            title=form.title.data.strip(),
            is_active=bool(form.is_active.data),
        )
        signature_file = request.files.get("signature_file")
        if signature_file and signature_file.filename:
            signature_rel = save_uploaded_image(signature_file, "official_signatures")
            official.signature_path = signature_rel

        if official.is_active:
            Official.query.update({"is_active": False})

        db.session.add(official)
        db.session.commit()
        log_action("Created official", entity_type="official", entity_id=official.id, meta={"name": official.full_name})
        flash("Official added.", "success")
        return redirect(url_for("admin.list_officials"))
    return render_template("official_form.html", form=form, title="Add Official", submit_label="Create")


@admin_bp.route("/officials/<int:official_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def edit_official(official_id: int):
    official = db.get_or_404(Official, official_id)
    form = OfficialForm(obj=official)
    if form.validate_on_submit():
        official.full_name = form.full_name.data.strip()
        official.title = form.title.data.strip()
        signature_file = request.files.get("signature_file")
        if signature_file and signature_file.filename:
            signature_rel = save_uploaded_image(signature_file, "official_signatures")
            official.signature_path = signature_rel

        if form.is_active.data:
            Official.query.update({"is_active": False})
            official.is_active = True
        else:
            official.is_active = False

        official.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        log_action("Updated official", entity_type="official", entity_id=official.id, meta={"name": official.full_name})
        flash("Official updated.", "success")
        return redirect(url_for("admin.list_officials"))

    return render_template("official_form.html", form=form, title="Edit Official", submit_label="Update", official=official)


# ------------------------------
# Backups (Admin)
# ------------------------------


@admin_bp.route("/backups")
@login_required
@roles_required("admin")
def backups():
    backup_dir = current_app.config.get("BACKUP_DIR", os.path.join(os.getcwd(), "backups"))
    backups_list = _list_backups(backup_dir)
    db_size_bytes = _get_db_size_bytes()
    db_size_label = _format_bytes(db_size_bytes)

    # System health
    db_url = current_app.config["SQLALCHEMY_DATABASE_URI"]
    try:
        disk = shutil.disk_usage(backup_dir)
        disk_total = _format_bytes(disk.total)
        disk_free = _format_bytes(disk.free)
        disk_pct = disk.used / disk.total * 100
    except Exception:
        disk_total = disk_free = "—"
        disk_pct = 0
    backup_count = len(backups_list)
    latest_backup = max(backups_list, key=lambda b: b["mtime"]) if backups_list else None

    date_from = _parse_date_param((request.args.get("from") or "").strip())
    date_to = _parse_date_param((request.args.get("to") or "").strip())
    if date_from or date_to:
        filtered = []
        for b in backups_list:
            b_date = b["mtime"].date()
            if date_from and b_date < date_from:
                continue
            if date_to and b_date > date_to:
                continue
            filtered.append(b)
        backups_list = filtered

    return render_template(
        "admin_backups.html",
        backups=backups_list,
        backup_dir=backup_dir,
        date_from=date_from,
        date_to=date_to,
        db_size_bytes=db_size_bytes,
        db_size_label=db_size_label,
        disk_total=disk_total,
        disk_free=disk_free,
        disk_pct=disk_pct,
        backup_count=backup_count,
        latest_backup=latest_backup,
    )


@admin_bp.route("/backups/create", methods=["POST"])
@login_required
@roles_required("admin")
def create_backup():
    backup_dir = current_app.config.get("BACKUP_DIR", os.path.join(os.getcwd(), "backups"))
    try:
        dest = _backup_db(backup_dir)
        log_action("Created database backup", entity_type="backup", meta={"path": dest})
        flash("Backup created successfully.", "success")
    except Exception as exc:
        current_app.logger.exception("Backup failed: %s", exc)
        flash(f"Backup failed: {exc}", "danger")
    return redirect(url_for("admin.backups"))


@admin_bp.route("/backups/download/<path:filename>")
@login_required
@roles_required("admin")
def download_backup(filename: str):
    backup_dir = current_app.config.get("BACKUP_DIR", os.path.join(os.getcwd(), "backups"))
    safe_path = os.path.abspath(os.path.join(backup_dir, filename))
    if not safe_path.startswith(os.path.abspath(backup_dir) + os.sep) or not os.path.isfile(safe_path):
        flash("Backup not found.", "warning")
        return redirect(url_for("admin.backups"))
    return send_file(safe_path, as_attachment=True, download_name=os.path.basename(safe_path))


@admin_bp.route("/backups/restore", methods=["POST"])
@login_required
@roles_required("admin")
def restore_backup():
    backup_dir = current_app.config.get("BACKUP_DIR", os.path.join(os.getcwd(), "backups"))
    filename = (request.form.get("filename") or "").strip()
    upload = request.files.get("backup_file")

    restore_path = None
    temp_uploaded = False
    if upload and upload.filename:
        # Save uploaded file into backup dir for restore
        os.makedirs(backup_dir, exist_ok=True)
        safe_name = secure_filename(upload.filename)
        if not safe_name:
            flash("Invalid upload filename.", "warning")
            return redirect(url_for("admin.backups"))
        restore_path = os.path.abspath(os.path.join(backup_dir, safe_name))
        try:
            upload.save(restore_path)
        except Exception as exc:
            current_app.logger.exception("Failed to save uploaded backup: %s", exc)
            flash(f"Upload failed: {exc}", "danger")
            return redirect(url_for("admin.backups"))
        temp_uploaded = True
    elif filename:
        restore_path = os.path.abspath(os.path.join(backup_dir, filename))

    if not restore_path or not restore_path.startswith(os.path.abspath(backup_dir) + os.sep) or not os.path.isfile(restore_path):
        flash("Invalid backup selected.", "warning")
        return redirect(url_for("admin.backups"))

    try:
        _restore_db(restore_path)
        db.create_all()
        try:
            log_action("Restored database backup", entity_type="backup", meta={"path": restore_path})
        except Exception as log_exc:
            current_app.logger.warning("Could not log restore action (user/table missing in backup?): %s", log_exc)
        flash("Database restored successfully.", "success")
    except Exception as exc:
        current_app.logger.exception("Restore failed: %s", exc)
        flash(f"Restore failed: {exc}", "danger")
    finally:
        if temp_uploaded:
            current_app.logger.info("Uploaded backup saved at %s", restore_path)
        db.session.remove()
        db.engine.dispose()

    return redirect(url_for("admin.backups"))


@admin_bp.route("/backups/delete/<path:filename>", methods=["POST"])
@login_required
@roles_required("admin")
def delete_backup(filename: str):
    backup_dir = current_app.config.get("BACKUP_DIR", os.path.join(os.getcwd(), "backups"))
    safe_path = os.path.abspath(os.path.join(backup_dir, filename))
    if not safe_path.startswith(os.path.abspath(backup_dir) + os.sep) or not os.path.isfile(safe_path):
        flash("Backup not found.", "warning")
        return redirect(url_for("admin.backups"))
    try:
        os.remove(safe_path)
        log_action("Deleted database backup", entity_type="backup", meta={"path": safe_path})
        flash("Backup deleted.", "success")
    except Exception as exc:
        current_app.logger.exception("Failed to delete backup: %s", exc)
        flash(f"Failed to delete backup: {exc}", "danger")
    return redirect(url_for("admin.backups"))


# ------------------------------
# Settings
# ------------------------------


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def settings():
    from .settings import load_settings, save_settings

    if request.method == "POST":
        data = load_settings()
        data["public_url"] = (request.form.get("public_url") or "").strip().rstrip("/")
        data["barangay_name"] = (request.form.get("barangay_name") or "").strip() or data["barangay_name"]
        data["system_name"] = (request.form.get("system_name") or "").strip() or data["system_name"]
        save_settings(data)
        log_action("Updated system settings", entity_type="settings", meta={"keys": list(data.keys())})
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))

    data = load_settings()
    return render_template("admin_settings.html", settings=data)

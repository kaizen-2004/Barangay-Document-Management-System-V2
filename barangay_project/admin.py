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
from datetime import datetime, timezone

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_file
from flask_login import login_required, current_user

from sqlalchemy import or_, text

from .extensions import db
from .helpers import log_action, roles_required
from .models import PasswordReset, TransactionLog, User, LoginMfaCode, Document, DocumentType
from .forms import EditUserForm, UserForm, DeleteForm, DocumentTypeForm
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _backup_db(backup_dir: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    url = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""

    if url.startswith("sqlite:///"):
        db_path = url.replace("sqlite:///", "", 1)
        if db_path == ":memory:":
            raise RuntimeError("Cannot back up an in-memory SQLite database.")
        dest = os.path.join(backup_dir, f"backup_{ts}.sqlite")
        shutil.copy2(db_path, dest)
        return dest

    dest = os.path.join(backup_dir, f"backup_{ts}.dump")
    cmd = ["pg_dump", "-Fc", url, "-f", dest]
    subprocess.run(cmd, check=True)
    return dest


def _restore_db(backup_path: str) -> None:
    url = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if url.startswith("sqlite:///"):
        db_path = url.replace("sqlite:///", "", 1)
        if db_path == ":memory:":
            raise RuntimeError("Cannot restore an in-memory SQLite database.")
        shutil.copy2(backup_path, db_path)
        return

    cmd = ["pg_restore", "--clean", "--if-exists", "-d", url, backup_path]
    subprocess.run(cmd, check=True)


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
    url = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if url.startswith("sqlite:///"):
        db_path = url.replace("sqlite:///", "", 1)
        if db_path == ":memory:":
            return None
        try:
            return os.path.getsize(db_path)
        except OSError:
            return None
    try:
        return db.session.execute(text("SELECT pg_database_size(current_database())")).scalar()
    except Exception:
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
            or_(User.username.ilike(like), User.email.ilike(like), User.role.ilike(like))
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
        # Create a new user with the supplied details
        user = User(
            username=form.username.data,
            email=form.email.data,
            role=form.role.data,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        # Log the creation of a new user
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

        user.username = form.username.data
        user.email = form.email.data
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
    PasswordReset.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    LoginMfaCode.query.filter_by(user_id=user.id).delete(synchronize_session=False)

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
    )


@admin_bp.route("/document-types/add", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def add_document_type():
    form = DocumentTypeForm()
    if form.validate_on_submit():
        dt = DocumentType(
            name=form.name.data.strip(),
            description=form.description.data.strip() if form.description.data else None,
            template_path=form.template_path.data or None,
            requires_photo=bool(form.requires_photo.data),
        )
        db.session.add(dt)
        db.session.commit()
        log_action("Created document type", entity_type="document_type", entity_id=dt.id, meta={"name": dt.name})
        flash("Document type added.", "success")
        return redirect(url_for("admin.list_document_types"))
    return render_template("document_type_form.html", form=form, title="Add Document Type", submit_label="Create")


@admin_bp.route("/document-types/<int:type_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def edit_document_type(type_id: int):
    dt = db.get_or_404(DocumentType, type_id)
    form = DocumentTypeForm(obj=dt)
    if form.validate_on_submit():
        dt.name = form.name.data.strip()
        dt.description = form.description.data.strip() if form.description.data else None
        dt.template_path = form.template_path.data or None
        dt.requires_photo = bool(form.requires_photo.data)
        db.session.commit()
        log_action("Updated document type", entity_type="document_type", entity_id=dt.id, meta={"name": dt.name})
        flash("Document type updated.", "success")
        return redirect(url_for("admin.list_document_types"))
    return render_template("document_type_form.html", form=form, title="Edit Document Type", submit_label="Update")


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
        restore_path = os.path.abspath(os.path.join(backup_dir, upload.filename))
        upload.save(restore_path)
        temp_uploaded = True
    elif filename:
        restore_path = os.path.abspath(os.path.join(backup_dir, filename))

    if not restore_path or not restore_path.startswith(os.path.abspath(backup_dir) + os.sep) or not os.path.isfile(restore_path):
        flash("Invalid backup selected.", "warning")
        return redirect(url_for("admin.backups"))

    try:
        _restore_db(restore_path)
        log_action("Restored database backup", entity_type="backup", meta={"path": restore_path})
        flash("Database restored successfully.", "success")
    except Exception as exc:
        current_app.logger.exception("Restore failed: %s", exc)
        flash(f"Restore failed: {exc}", "danger")
    finally:
        # Keep uploaded backups in the backup directory for traceability.
        if temp_uploaded:
            current_app.logger.info("Uploaded backup saved at %s", restore_path)

    return redirect(url_for("admin.backups"))

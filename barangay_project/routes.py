"""Main (non-admin) routes.

This blueprint covers the day-to-day operations:

- Residents: list, add, edit, delete
- Documents: list, issue, edit, delete

For admin-only features (users, document types, audit logs), see `admin.py`.
"""

from __future__ import annotations

import os
import csv
import io
import json
import re
import time
from calendar import monthrange
from datetime import date as dt_date, datetime, timedelta, time as dt_time

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, Response, send_file, url_for
from flask_login import login_required, current_user

from sqlalchemy import and_, func, or_

from .forms import DocumentForm, ResidentForm
from .helpers import delete_capture_photo_paths, log_action, reprocess_captured_photo, roles_required, save_camera_capture_for_processing, save_or_keep_resident_photo, save_or_keep_resident_signature
from .docx_pipeline import render_document_files, DocumentGenerationError, resolve_stored_path_to_abs
from .extensions import db
from .formatting import format_ph_mobile, normalize_phone_for_storage
from .models import BarangayStreet, Document, DocumentType, Resident, TransactionLog, User
from .time_utils import utcnow


DOCUMENT_STATUSES = ("draft", "pending", "approved", "issued")
DRAFT_LIKE_STATUSES = ("draft", "pending", "approved")
KNL_ID_PATTERN = re.compile(r"^KNL-\\d{4}-\\d{5}$", re.IGNORECASE)
RESIDENT_SEARCH_COLUMNS = (
    Resident.first_name,
    Resident.last_name,
    Resident.middle_name,
    Resident.barangay_id,
    Resident.address,
    Resident.contact_number,
    Resident.occupation,
)
DOCUMENT_SEARCH_COLUMNS = (
    Resident.first_name,
    Resident.last_name,
    Resident.barangay_id,
    DocumentType.name,
    Document.details,
    Document.field_values,
)


def _multi_search_filter(q, columns):
    words = [w.strip() for w in q.split() if w.strip()]
    if not words:
        return db.true()
    return and_(*[or_(*[c.ilike(f"%{word}%") for c in columns]) for word in words])


def _document_type_field_configs() -> dict[int, dict]:
    configs: dict[int, dict] = {}
    for dt in DocumentType.query.all():
        try:
            parsed = json.loads(dt.field_config or "{}")
        except Exception:
            current_app.logger.exception("Failed to parse field config JSON")
            parsed = {}
        fields = parsed.get("fields") if isinstance(parsed, dict) else []
        configs[dt.id] = {"fields": fields if isinstance(fields, list) else []}
    return configs


def _document_field_values(document: Document | None = None) -> dict[str, str]:
    if not document or not document.field_values:
        return {}
    try:
        parsed = json.loads(document.field_values)
    except Exception:
        current_app.logger.exception("Failed to parse candidate placeholders JSON")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v or "") for k, v in parsed.items()}


def _posted_document_field_values() -> dict[str, str]:
    return {str(k): str(request.form.get(k) or "") for k in request.form}


def _resident_profile_field_values(resident: Resident) -> dict[str, str]:
    resident_name = " ".join(part for part in [resident.first_name, resident.middle_name, resident.last_name] if part).upper()
    return {
        "resident_name": resident_name,
        "first_name": (resident.first_name or "").upper(),
        "middle_name": (resident.middle_name or "").upper(),
        "last_name": (resident.last_name or "").upper(),
        "gender": (resident.gender or "").upper(),
        "birth_date": resident.birth_date.isoformat() if resident.birth_date else "",
        "marital_status": (resident.marital_status or "").upper(),
        "contact_number": format_ph_mobile(resident.contact_number),
        "occupation": (resident.occupation or "").upper(),
        "years_on_barangay": str(resident.years_on_barangay) if resident.years_on_barangay is not None else "",
        "emergency_contact_name": (resident.emergency_contact_name or "").upper(),
        "emergency_contact_relationship": (resident.emergency_contact_relationship or "").upper(),
        "emergency_contact_number": format_ph_mobile(resident.emergency_contact_number),
        "barangay_id": resident.barangay_id or "",
        "resident_id": resident.barangay_id or "",
        "street": (resident.street.name if resident.street else "").upper(),
        "address": (resident.full_address or "").upper(),
    }


def _resident_document_field_values() -> dict[int, dict[str, str]]:
    residents = Resident.query.filter(Resident.is_archived.is_(False)).all()
    return {resident.id: _resident_profile_field_values(resident) for resident in residents}


def _resident_signature_values() -> dict[int, dict[str, str]]:
    residents = Resident.query.filter(Resident.is_archived.is_(False)).all()
    return {
        resident.id: {
            "path": resident.signature_path or "",
            "url": url_for("static", filename=resident.signature_path) if resident.signature_path else "",
        }
        for resident in residents
    }


def _resident_photo_values() -> dict[int, dict[str, str]]:
    residents = Resident.query.filter(Resident.is_archived.is_(False)).all()
    return {
        resident.id: {
            "path": resident.photo_path or "",
            "url": url_for("static", filename=resident.photo_path) if resident.photo_path else "",
        }
        for resident in residents
    }


def _collect_document_field_values(doc_type: DocumentType, resident: Resident) -> tuple[dict[str, str], list[str]]:
    try:
        config = json.loads(doc_type.field_config or "{}")
    except Exception:
        current_app.logger.exception("Failed to parse placeholder config JSON")
        config = {}
    fields = config.get("fields") if isinstance(config, dict) else []
    resident_values = _resident_profile_field_values(resident)
    values: dict[str, str] = {}
    errors: list[str] = []
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name", "")).strip()
        if not name:
            continue
        label = str(field.get("label") or name.replace("_", " ").title())
        value = (request.form.get(f"field_{name}") or "").strip() or resident_values.get(name, "")
        if field.get("required") and not value:
            errors.append(f"{label} is required.")
        values[name] = value
    return values, errors


def _populate_resident_street_choices(form: ResidentForm) -> None:
    streets = BarangayStreet.query.order_by(BarangayStreet.name.asc()).all()
    form.street_id.choices = [(street.id, street.name) for street in streets]


def _build_user_map(user_ids: set[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    users = User.query.filter(User.id.in_(user_ids)).all()
    return {u.id: u.username for u in users}


def _parse_date(value: str | None) -> dt_date | None:
    """Parse YYYY-MM-DD to date, returning None if empty/invalid."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        current_app.logger.exception("Failed to resolve template for document type")
        return None


main_bp = Blueprint("main", __name__)


def _clamp_removal_level(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 2
    if v < 1:
        return 1
    if v > 3:
        return 3
    return v


@main_bp.route("/api/photos/capture-process", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def capture_process_photo():
    if not current_app.config.get("ENABLE_CAMERA_CAPTURE", True):
        return jsonify({"success": False, "error": "Camera capture is disabled."}), 403

    payload = request.get_json(silent=True) or {}
    image_data = payload.get("image_data")
    level = _clamp_removal_level(payload.get("removal_level", 2))

    existing_original = payload.get("original_path")
    existing_processed = payload.get("processed_path")

    if existing_original and existing_processed:
        # Re-process: overwrite existing temp files in place (no duplicates)
        result = reprocess_captured_photo(image_data, existing_original, existing_processed, level=level)
        if not result:
            return jsonify({"success": False, "error": "Invalid re-process request."}), 400
        resident_photo_saved = False
        resident_photo_path = result["processed_path"]
        resident_photo_url = url_for("static", filename=resident_photo_path)
        processed_image_url = url_for("static", filename=result["processed_path"])

        return jsonify({
            "success": True,
            "original_image_path": result["original_path"],
            "processed_image_path": result["processed_path"],
            "original_image_url": url_for("static", filename=result["original_path"]) + f"?v={time.time_ns()}",
            "processed_image_url": processed_image_url + f"?v={time.time_ns()}",
            "resident_photo_path": resident_photo_path,
            "resident_photo_url": resident_photo_url + f"?v={time.time_ns()}",
            "resident_photo_saved": resident_photo_saved,
            "warning": result.get("warning"),
        })

    result = save_camera_capture_for_processing(image_data, level=level)
    if not result:
        return jsonify({"success": False, "error": "Invalid captured image."}), 400

    resident_photo_saved = False
    resident_photo_path = result["processed_path"]
    resident_photo_url = url_for("static", filename=resident_photo_path)
    resident_id = payload.get("resident_id")
    if resident_id:
        try:
            resident = db.session.get(Resident, int(resident_id))
        except Exception:
            current_app.logger.exception("Failed to fetch resident for photo capture")
            resident = None
        if resident and not resident.is_archived:
            resident.photo_path = result["processed_path"]
            resident.updated_at = utcnow()
            resident.updated_by_id = current_user.id
            db.session.commit()
            resident_photo_saved = True
            resident_photo_path = resident.photo_path or result["processed_path"]
            resident_photo_url = url_for("static", filename=resident_photo_path)

    response = {
        "success": True,
        "original_image_path": result["original_path"],
        "processed_image_path": result["processed_path"],
        "original_image_url": url_for("static", filename=result["original_path"]) + f"?v={time.time_ns()}",
        "processed_image_url": url_for("static", filename=result["processed_path"]) + f"?v={time.time_ns()}",
        "resident_photo_path": resident_photo_path,
        "resident_photo_url": resident_photo_url + f"?v={time.time_ns()}",
        "resident_photo_saved": resident_photo_saved,
        "warning": result.get("warning"),
    }
    return jsonify(response)


@main_bp.route("/api/photos/capture-cleanup", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def capture_cleanup_photo():
    payload = request.get_json(silent=True) or {}
    paths = payload.get("paths") or []
    if not isinstance(paths, list):
        return jsonify({"success": False, "error": "Invalid cleanup request."}), 400
    deleted = delete_capture_photo_paths([str(path) for path in paths])
    return jsonify({"success": True, "deleted": deleted})


@main_bp.route("/api/signatures/process", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def process_signature():
    if not current_app.config.get("ENABLE_CAMERA_CAPTURE", True):
        return jsonify({"success": False, "error": "Camera capture is disabled."}), 403

    payload = request.get_json(silent=True) or {}
    image_data = payload.get("image_data")
    if not image_data:
        return jsonify({"success": False, "error": "No image data provided."}), 400

    rel_path = save_or_keep_resident_signature(image_data)
    if not rel_path:
        return jsonify({"success": False, "error": "Invalid signature image data."}), 400

    return jsonify({
        "success": True,
        "signature_path": rel_path,
        "signature_url": url_for("static", filename=rel_path) + f"?v={time.time_ns()}",
    })


@main_bp.route("/api/residents/<int:resident_id>/field-values")
@login_required
@roles_required("admin", "clerk")
def api_resident_field_values(resident_id: int):
    resident = db.session.get(Resident, resident_id)
    if not resident or resident.is_archived:
        return jsonify({"success": False, "error": "Resident not found."}), 404
    return jsonify({"success": True, "values": _resident_profile_field_values(resident)})


@main_bp.route("/")
@login_required
def index():
    """Dashboard with quick stats + charts."""
    resident_count = Resident.query.filter(Resident.is_archived.is_(False)).count()
    archived_resident_count = Resident.query.filter(Resident.is_archived.is_(True)).count()
    document_count = Document.query.filter(
        Document.is_archived.is_(False),
        Document.status == "issued",
    ).count()
    draft_document_count = Document.query.filter(
        Document.is_archived.is_(False),
        Document.status.in_(DRAFT_LIKE_STATUSES),
    ).count()
    archived_document_count = Document.query.filter(Document.is_archived.is_(True)).count()

    today = dt_date.today()
    month_start = today.replace(day=1)
    next_month_start = dt_date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
    documents_this_month = Document.query.filter(
        Document.is_archived.is_(False),
        Document.status == "issued",
        Document.issue_date >= month_start,
        Document.issue_date < next_month_start,
    ).count()

    # Residents by gender
    gender_rows = (
        db.session.query(Resident.gender, func.count(Resident.id))
        .filter(Resident.is_archived.is_(False))
        .group_by(Resident.gender)
        .all()
    )
    gender_labels = [g or "Unspecified" for g, _ in gender_rows]
    gender_values = [int(c) for _, c in gender_rows]

    street_rows = (
        db.session.query(BarangayStreet.name, func.count(Resident.id))
        .join(Resident, Resident.street_id == BarangayStreet.id)
        .filter(Resident.is_archived.is_(False))
        .group_by(BarangayStreet.name)
        .order_by(func.count(Resident.id).desc(), BarangayStreet.name.asc())
        .limit(10)
        .all()
    )
    street_labels = [name for name, _ in street_rows]
    street_values = [int(c) for _, c in street_rows]

    # Documents by type
    type_rows = (
        db.session.query(DocumentType.name, func.count(Document.id))
        .join(Document, Document.document_type_id == DocumentType.id)
        .filter(Document.is_archived.is_(False), Document.status == "issued")
        .group_by(DocumentType.name)
        .order_by(func.count(Document.id).desc())
        .limit(8)
        .all()
    )
    doc_type_labels = [n for n, _ in type_rows]
    doc_type_values = [int(c) for _, c in type_rows]

    # Documents issued per month (last 6 months)
    month_expr = func.strftime("%Y-%m", Document.issue_date)
    month_rows = (
        db.session.query(month_expr, func.count(Document.id))
        .filter(Document.is_archived.is_(False), Document.status == "issued")
        .group_by(month_expr)
        .order_by(month_expr)
        .all()
    )
    month_labels = [m for m, _ in month_rows][-6:]
    month_values = [int(c) for _, c in month_rows][-6:]

    # Residents added per month (last 6 months)
    resident_month_expr = func.strftime("%Y-%m", Resident.created_at)
    resident_month_rows = (
        db.session.query(resident_month_expr, func.count(Resident.id))
        .filter(Resident.is_archived.is_(False))
        .group_by(resident_month_expr)
        .order_by(resident_month_expr)
        .all()
    )
    resident_month_labels = [m for m, _ in resident_month_rows][-6:]
    resident_month_values = [int(c) for _, c in resident_month_rows][-6:]

    # Document status breakdown
    status_labels = ["Issued", "Draft", "Archived"]
    status_values = [document_count, draft_document_count, archived_document_count]

    # Expiring documents (within 30 days)
    def _add_months(d, months):
        month = d.month - 1 + months
        year = d.year + month // 12
        month = month % 12 + 1
        day = min(d.day, monthrange(year, month)[1])
        return dt_date(year, month, day)

    expiring_cutoff = today + timedelta(days=30)
    expiring_docs = []
    all_issued = Document.query.filter(
        Document.is_archived.is_(False),
        Document.status == "issued",
    ).all()
    for doc in all_issued:
        vm = doc.document_type.validity_months if doc.document_type else None
        if vm and doc.issue_date:
            issue = doc.issue_date
            if hasattr(issue, "date"):
                issue = issue.date()
            expiry = _add_months(issue, vm)
            if today <= expiry <= expiring_cutoff:
                expiring_docs.append({
                    "id": doc.id,
                    "resident_id": doc.resident_id,
                    "resident_name": f"{doc.resident.last_name}, {doc.resident.first_name}",
                    "doc_type": doc.document_type.name,
                    "expiry_date": expiry,
                })
    expiring_docs.sort(key=lambda x: x["expiry_date"])
    expiring_count = len(expiring_docs)

    # Upcoming birthdays (within 30 days)
    birthday_cutoff = today + timedelta(days=30)
    upcoming_birthdays = []
    for r in Resident.query.filter(Resident.is_archived.is_(False)).all():
        if not r.birth_date:
            continue
        try:
            bday = dt_date(today.year, r.birth_date.month, r.birth_date.day)
        except ValueError:
            continue
        if bday < today:
            try:
                bday = dt_date(today.year + 1, r.birth_date.month, r.birth_date.day)
            except ValueError:
                continue
        if bday <= birthday_cutoff:
            upcoming_birthdays.append({
                "id": r.id,
                "name": f"{r.last_name}, {r.first_name}",
                "age": bday.year - r.birth_date.year,
                "bday_date": bday,
            })
    upcoming_birthdays.sort(key=lambda x: x["bday_date"])
    upcoming_birthdays = upcoming_birthdays[:10]

    # Recent activity (audit trail)
    from .models import TransactionLog

    try:
        recent_logs = TransactionLog.query.order_by(TransactionLog.timestamp.desc()).limit(8).all()
    except Exception:
        current_app.logger.exception("Failed to fetch recent logs")
        # If the audit table isn't available yet, keep the dashboard usable.
        recent_logs = []

    return render_template(
        "index.html",
        resident_count=resident_count,
        archived_resident_count=archived_resident_count,
        document_count=document_count,
        draft_document_count=draft_document_count,
        archived_document_count=archived_document_count,
        documents_this_month=documents_this_month,
        gender_labels=gender_labels,
        gender_values=gender_values,
        street_labels=street_labels,
        street_values=street_values,
        doc_type_labels=doc_type_labels,
        doc_type_values=doc_type_values,
        month_labels=month_labels,
        month_values=month_values,
        resident_month_labels=resident_month_labels,
        resident_month_values=resident_month_values,
        status_labels=status_labels,
        status_values=status_values,
        expiring_docs=expiring_docs,
        expiring_count=expiring_count,
        upcoming_birthdays=upcoming_birthdays,
        recent_logs=recent_logs,
    )


@main_bp.route("/search")
@login_required
@roles_required("admin", "clerk")
def global_search():
    q = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "all").strip()
    if scope not in {"residents", "documents", "all"}:
        scope = "all"
    status = (request.args.get("status") or "").strip()
    type_id = (request.args.get("type") or "").strip()
    include_archived = (request.args.get("archived") or "").strip() == "1"
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))

    results = []
    pagination = None
    residents_results = []
    documents_results = []
    residents_count = 0
    documents_count = 0
    document_types = DocumentType.query.order_by(DocumentType.name.asc()).all()

    if q:
        if scope in {"documents", "all"}:
            query = Document.query.join(Resident).join(DocumentType)
            if not include_archived:
                query = query.filter(Document.is_archived.is_(False))
            query = query.filter(_multi_search_filter(q, DOCUMENT_SEARCH_COLUMNS))
            if type_id.isdigit():
                query = query.filter(Document.document_type_id == int(type_id))
            if status in DOCUMENT_STATUSES:
                if status == "draft":
                    query = query.filter(Document.status.in_(DRAFT_LIKE_STATUSES))
                else:
                    query = query.filter(Document.status == status)
            query = query.order_by(Document.issue_date.desc())
            if scope == "documents":
                pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
                results = pagination.items
            else:
                documents_count = query.count()
                documents_results = query.limit(per_page).all()

        if scope in {"residents", "all"}:
            query = Resident.query
            if not include_archived:
                query = query.filter(Resident.is_archived.is_(False))
            query = query.filter(_multi_search_filter(q, RESIDENT_SEARCH_COLUMNS))
            query = query.order_by(Resident.last_name.asc(), Resident.first_name.asc())
            if scope == "residents":
                pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
                results = pagination.items
            else:
                residents_count = query.count()
                residents_results = query.limit(per_page).all()

    return render_template(
        "search.html",
        q=q,
        scope=scope,
        status=status,
        type_id=type_id,
        include_archived=include_archived,
        document_types=document_types,
        results=results,
        pagination=pagination,
        residents_results=residents_results,
        documents_results=documents_results,
        residents_count=residents_count,
        documents_count=documents_count,
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@main_bp.route("/reports")
@login_required
@roles_required("admin", "clerk")
def reports():
    """Reporting dashboard with export to CSV/XLSX/PDF."""
    # Accept both the new query param names (date_from/date_to) and legacy (from/to)
    date_from = _parse_date((request.args.get("date_from") or request.args.get("from") or "").strip())
    date_to = _parse_date((request.args.get("date_to") or request.args.get("to") or "").strip())

    # Advanced filters: document type and status
    filter_type_id = (request.args.get("type") or "").strip()
    filter_status = (request.args.get("status") or "").strip()

    # Default window: last 30 days
    if not date_to:
        date_to = dt_date.today()
    if not date_from:
        date_from = date_to.replace(day=1)

    query = Document.query.join(DocumentType).join(Resident)
    query = query.filter(
        Document.issue_date >= datetime.combine(date_from, dt_time.min),
        Document.issue_date <= datetime.combine(date_to, dt_time.max),
        Document.is_archived.is_(False),
        Document.status == "issued",
    )

    # Apply optional filters
    if filter_type_id and filter_type_id.isdigit():
        query = query.filter(Document.document_type_id == int(filter_type_id))
    if filter_status:
        if filter_status == "draft":
            query = query.filter(Document.status.in_(DRAFT_LIKE_STATUSES))
        elif filter_status == "issued":
            query = query.filter(Document.status == "issued")

    total_docs = query.count()
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))
    pagination = db.paginate(query.order_by(Document.issue_date.desc()), page=page, per_page=per_page, error_out=False)
    docs = pagination.items

    by_type_rows = (
        db.session.query(DocumentType.name, func.count(Document.id))
        .join(Document)
        .filter(
            Document.issue_date >= date_from,
            Document.issue_date <= date_to,
            Document.is_archived.is_(False),
            Document.status == "issued",
        )
        .group_by(DocumentType.name)
        .order_by(func.count(Document.id).desc())
        .all()
    )

    # Template expects a mapping
    by_type = {name: int(count) for name, count in by_type_rows}

    document_types = DocumentType.query.order_by(DocumentType.name).all()

    return render_template(
        "reports.html",
        # Keep both names to avoid template mismatch regressions
        docs=docs,
        documents=docs,
        date_from=date_from,
        date_to=date_to,
        total_docs=total_docs,
        total_documents=total_docs,
        by_type=by_type,
        filter_type_id=filter_type_id,
        filter_status=filter_status,
        document_types=document_types,
        DRAFT_LIKE_STATUSES=DRAFT_LIKE_STATUSES,
        pagination=pagination,
    )


@main_bp.route("/reports/export/<string:fmt>")
@login_required
@roles_required("admin", "clerk")
def export_reports(fmt: str):
    """Export report rows to CSV/XLSX/PDF."""
    fmt = (fmt or "").lower()
    date_from = _parse_date((request.args.get("date_from") or request.args.get("from") or "").strip())
    date_to = _parse_date((request.args.get("date_to") or request.args.get("to") or "").strip())
    if not date_to:
        date_to = dt_date.today()
    if not date_from:
        date_from = date_to.replace(day=1)

    query = Document.query.join(DocumentType).join(Resident)
    query = query.filter(
        Document.issue_date >= datetime.combine(date_from, dt_time.min),
        Document.issue_date <= datetime.combine(date_to, dt_time.max),
        Document.is_archived.is_(False),
        Document.status == "issued",
    )
    docs = query.order_by(Document.issue_date.asc()).all()

    rows = []
    for d in docs:
        rows.append(
            {
                "Issue Date": d.issue_date.isoformat() if d.issue_date else "",
                "Type": d.document_type.name if d.document_type else "",
                "Resident": f"{d.resident.last_name}, {d.resident.first_name}".upper() if d.resident else "",
                "Details": d.details or "",
            }
        )

    filename_base = f"report_{date_from.isoformat()}_{date_to.isoformat()}"

    if fmt == "csv":
        output = io.StringIO()
        fieldnames = list(rows[0].keys()) if rows else ["Issue Date", "Type", "Resident", "Details"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        data = io.BytesIO(output.getvalue().encode("utf-8"))
        log_action("Exported reports (CSV)", entity_type="report", meta={"from": date_from.isoformat(), "to": date_to.isoformat(), "rows": len(rows)})
        return send_file(data, mimetype="text/csv", as_attachment=True, download_name=f"{filename_base}.csv")

    if fmt == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Documents"
        headers = list(rows[0].keys()) if rows else ["Issue Date", "Type", "Resident", "Details"]
        ws.append(headers)
        for r in rows:
            ws.append([r.get(h, "") for h in headers])
        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        log_action("Exported reports (XLSX)", entity_type="report", meta={"from": date_from.isoformat(), "to": date_to.isoformat(), "rows": len(rows)})
        return send_file(bio, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name=f"{filename_base}.xlsx")

    if fmt == "pdf":
        from collections import Counter
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER
        from reportlab.graphics.shapes import Drawing
        from reportlab.graphics.charts.barcharts import VerticalBarChart

        MAROON = colors.HexColor("#a32020")
        DARK_MAROON = colors.HexColor("#7a1818")
        LIGHT_GREY = colors.Color(0.92, 0.92, 0.92)
        ALT_ROW = colors.Color(0.96, 0.93, 0.93)

        W = letter[0] - inch
        type_counts = Counter(d.document_type.name for d in docs if d.document_type)
        sorted_types = sorted(type_counts.items(), key=lambda x: -x[1])
        labels = [t[0] for t in sorted_types]
        values = [t[1] for t in sorted_types]
        total = len(docs)

        bio = io.BytesIO()
        doc = SimpleDocTemplate(bio, pagesize=letter, leftMargin=0.5*inch, rightMargin=0.5*inch, topMargin=0.3*inch, bottomMargin=0.3*inch)

        story = []
        story.append(Spacer(1, 2))

        # Colored header bar
        hdr = ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=14, textColor=colors.white, alignment=TA_CENTER, leading=17)
        sbd = ParagraphStyle("Sbd", fontName="Helvetica", fontSize=7.5, textColor=colors.white, alignment=TA_CENTER, leading=9)
        hdr_data = [
            [Paragraph("Issued Documents Summary", hdr)],
            [Paragraph(f"{date_from.isoformat()}  —  {date_to.isoformat()}", sbd)],
        ]
        hdr_tbl = Table(hdr_data, colWidths=[W])
        hdr_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), MAROON),
            ("TOPPADDING", (0, 0), (0, 0), 6),
            ("BOTTOMPADDING", (0, 0), (0, 0), 1),
            ("TOPPADDING", (0, 1), (0, 1), 1),
            ("BOTTOMPADDING", (0, 1), (0, 1), 6),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        story.append(hdr_tbl)
        story.append(Spacer(1, 4))

        # Metric row
        mb = ParagraphStyle("mb", fontName="Helvetica-Bold", fontSize=7, textColor=colors.Color(0.4, 0.4, 0.4), alignment=TA_CENTER)
        mv = ParagraphStyle("mv", fontName="Helvetica-Bold", fontSize=11, textColor=MAROON, alignment=TA_CENTER)
        mc = ParagraphStyle("mc", fontName="Helvetica", fontSize=7.5, alignment=TA_CENTER)
        metric_data = [
            [Paragraph("Total Documents", mb), Paragraph("Date Range", mb), Paragraph("Document Types", mb)],
            [Paragraph(str(total), mv), Paragraph(f"{date_from.isoformat()} — {date_to.isoformat()}", mc), Paragraph(str(len(sorted_types)), mv)],
        ]
        mtbl = Table(metric_data, colWidths=[W/3, W/3, W/3])
        mtbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GREY),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.Color(0.8, 0.8, 0.8)),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.Color(0.85, 0.85, 0.85)),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(mtbl)
        story.append(Spacer(1, 4))

        if labels:
            # Bar chart
            ch = min(160, max(100, len(labels) * 20 + 40))
            drawing = Drawing(W, ch)
            bc = VerticalBarChart()
            bc.x = 45
            bc.y = 25
            bc.height = ch - 45
            bc.width = W - 60
            bc.data = [values]
            bc.categoryAxis.categoryNames = labels
            bc.categoryAxis.labels.fontSize = 6.5
            bc.categoryAxis.labels.angle = 8
            bc.categoryAxis.labels.fillColor = colors.Color(0.3, 0.3, 0.3)
            bc.valueAxis.valueMin = 0
            bc.valueAxis.valueMax = max(values) * 1.2 if values else 1
            bc.valueAxis.labels.fontSize = 6.5
            bc.valueAxis.labels.fillColor = colors.Color(0.3, 0.3, 0.3)
            bc.bars[0].fillColor = MAROON
            bc.bars[0].strokeColor = DARK_MAROON
            bc.barWidth = max(8, min(24, W / len(labels) * 0.4))
            bc.groupSpacing = 18
            drawing.add(bc)
            story.append(drawing)
            story.append(Spacer(1, 4))

            # Summary table
            ls = ParagraphStyle("ls", fontName="Helvetica-Bold", fontSize=7, textColor=colors.white, alignment=TA_CENTER)
            bs = ParagraphStyle("bs", fontName="Helvetica", fontSize=7.5, leading=9)
            bb = ParagraphStyle("bb", fontName="Helvetica-Bold", fontSize=7.5, leading=9)
            cs = ParagraphStyle("cs", fontName="Helvetica", fontSize=7.5, leading=9, alignment=TA_CENTER)

            pdata = [[Paragraph("Document Type", ls), Paragraph("Count", ls), Paragraph("Share", ls)]]
            for i, (name, count) in enumerate(sorted_types):
                pct = f"{count / total * 100:.1f}%" if total else "—"
                pdata.append([Paragraph(name, bs), Paragraph(str(count), bb), Paragraph(pct, cs)])

            ptbl = Table(pdata, colWidths=[W * 0.5, W * 0.25, W * 0.25], repeatRows=1)
            ptbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), DARK_MAROON),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.Color(0.8, 0.8, 0.8)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            for i in range(1, len(pdata)):
                if i % 2 == 1:
                    ptbl.setStyle(TableStyle([("BACKGROUND", (0, i), (-1, i), ALT_ROW)]))
            story.append(ptbl)
        else:
            story.append(Paragraph("No documents found for the selected period.", ParagraphStyle("no", fontName="Helvetica", fontSize=9, alignment=TA_CENTER)))

        doc.build(story)
        bio.seek(0)
        log_action("Exported reports (PDF summary)", entity_type="report", meta={"from": date_from.isoformat(), "to": date_to.isoformat(), "rows": len(rows)})
        return send_file(bio, mimetype="application/pdf", as_attachment=True, download_name=f"{filename_base}.pdf")

    flash("Unsupported export format.", "danger")
    return redirect(
        url_for(
            "main.reports",
            **{"from": date_from.isoformat(), "to": date_to.isoformat()},
        )
    )


# ---------------------------------------------------------------------------
# Residents
# ---------------------------------------------------------------------------


@main_bp.route("/residents")
@login_required
@roles_required("admin", "clerk")
def list_residents():
    q = (request.args.get("q") or "").strip()
    gender = (request.args.get("gender") or "").strip()
    street_id = (request.args.get("street") or "").strip()
    sort = (request.args.get("sort") or "name_asc").strip()
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))

    query = Resident.query
    query = query.filter(Resident.is_archived.is_(False))
    if q:
        query = query.filter(_multi_search_filter(q, RESIDENT_SEARCH_COLUMNS))
    if gender:
        query = query.filter(Resident.gender == gender)
    if street_id and street_id.isdigit():
        query = query.filter(Resident.street_id == int(street_id))

    # Sorting options
    if sort == "added_desc":
        query = query.order_by(Resident.id.desc())
    elif sort == "added_asc":
        query = query.order_by(Resident.id.asc())
    elif sort == "barangay_id_asc":
        query = query.order_by(Resident.barangay_id.asc().nulls_last(), Resident.last_name.asc(), Resident.first_name.asc())
    elif sort == "barangay_id_desc":
        query = query.order_by(Resident.barangay_id.desc().nulls_last(), Resident.last_name.asc(), Resident.first_name.asc())
    elif sort == "name_desc":
        query = query.order_by(Resident.last_name.desc(), Resident.first_name.desc())
    else:  # name_asc
        query = query.order_by(Resident.last_name.asc(), Resident.first_name.asc())

    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    residents = pagination.items
    user_ids = {
        uid
        for resident in residents
        for uid in (resident.updated_by_id, resident.created_by_id)
        if uid
    }
    user_map = _build_user_map(user_ids)
    streets = BarangayStreet.query.order_by(BarangayStreet.name).all()
    document_types = DocumentType.query.order_by(DocumentType.name).all()
    return render_template(
        "residents.html",
        residents=residents,
        q=q,
        gender=gender,
        street_id=street_id,
        sort=sort,
        archived_view=False,
        pagination=pagination,
        user_map=user_map,
        streets=streets,
        document_types=document_types,
    )


@main_bp.route("/residents/archived")
@login_required
@roles_required("admin", "clerk")
def list_archived_residents():
    q = (request.args.get("q") or "").strip()
    gender = (request.args.get("gender") or "").strip()
    street_id = (request.args.get("street") or "").strip()
    sort = (request.args.get("sort") or "name_asc").strip()
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))

    query = Resident.query.filter(Resident.is_archived.is_(True))
    if q:
        query = query.filter(_multi_search_filter(q, RESIDENT_SEARCH_COLUMNS))
    if gender:
        query = query.filter(Resident.gender == gender)
    if street_id and street_id.isdigit():
        query = query.filter(Resident.street_id == int(street_id))

    # Sorting options
    if sort == "added_desc":
        query = query.order_by(Resident.id.desc())
    elif sort == "added_asc":
        query = query.order_by(Resident.id.asc())
    elif sort == "barangay_id_asc":
        query = query.order_by(Resident.barangay_id.asc().nulls_last(), Resident.last_name.asc(), Resident.first_name.asc())
    elif sort == "barangay_id_desc":
        query = query.order_by(Resident.barangay_id.desc().nulls_last(), Resident.last_name.asc(), Resident.first_name.asc())
    elif sort == "name_desc":
        query = query.order_by(Resident.last_name.desc(), Resident.first_name.desc())
    else:  # name_asc
        query = query.order_by(Resident.last_name.asc(), Resident.first_name.asc())

    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    residents = pagination.items
    user_ids = {
        uid
        for resident in residents
        for uid in (resident.updated_by_id, resident.created_by_id)
        if uid
    }
    user_map = _build_user_map(user_ids)
    streets = BarangayStreet.query.order_by(BarangayStreet.name).all()
    return render_template(
        "residents.html",
        residents=residents,
        q=q,
        gender=gender,
        street_id=street_id,
        sort=sort,
        archived_view=True,
        pagination=pagination,
        user_map=user_map,
        streets=streets,
    )


def _resolve_emergency_contact_address(form) -> str | None:
    if form.emergency_contact_same_address.data:
        return "SAME ADDRESS"
    return (form.emergency_contact_address.data or "").strip() or None


@main_bp.route("/residents/add", methods=["GET", "POST"])
@login_required
@roles_required("admin", "clerk")
def add_resident():
    form = ResidentForm()
    _populate_resident_street_choices(form)
    if form.validate_on_submit():
        if form.birth_date.data and form.birth_date.data > dt_date.today():
            form.birth_date.errors.append("Birth date cannot be in the future.")
            return render_template("resident_form.html", form=form, title="Add Resident")

        first = (form.first_name.data or "").strip()
        last = (form.last_name.data or "").strip()
        existing = Resident.query.filter(
            func.lower(Resident.first_name) == first.lower(),
            func.lower(Resident.last_name) == last.lower(),
            Resident.birth_date == form.birth_date.data,
        ).first()
        if existing:
            msg = "Resident already exists."
            if existing.is_archived:
                msg = "Resident already exists but is archived. Restore the record instead."
            form.first_name.errors.append(msg)
            return render_template("resident_form.html", form=form, title="Add Resident")

        barangay_id = form.barangay_id.data.strip() if form.barangay_id.data else None
        if barangay_id:
            normalized = barangay_id.strip().upper()
            if not KNL_ID_PATTERN.match(normalized):
                form.barangay_id.errors.append("Barangay ID must follow format KNL-YYYY-##### (e.g., KNL-2026-00001).")
                return render_template("resident_form.html", form=form, title="Add Resident")
            existing = Resident.query.filter(func.upper(Resident.barangay_id) == normalized).first()
            if existing:
                form.barangay_id.errors.append("Barangay ID is already in use.")
                return render_template("resident_form.html", form=form, title="Add Resident")
            barangay_id = normalized

        resident = Resident(
            barangay_id=barangay_id,
            first_name=form.first_name.data,
            middle_name=form.middle_name.data,
            last_name=form.last_name.data,
            gender=form.gender.data,
            birth_date=form.birth_date.data,
            marital_status=form.marital_status.data,
            contact_number=normalize_phone_for_storage(form.contact_number.data),
            occupation=(form.occupation.data or "").strip() or None,
            years_on_barangay=int(form.years_on_barangay.data) if (form.years_on_barangay.data or "").strip().isdigit() else None,
            emergency_contact_name=(form.emergency_contact_name.data or "").strip() or None,
            emergency_contact_relationship=(form.emergency_contact_relationship.data or "").strip() or None,
            emergency_contact_number=normalize_phone_for_storage(form.emergency_contact_number.data),
            emergency_contact_address=_resolve_emergency_contact_address(form),
            street_id=form.street_id.data,
            address=form.address.data,
            created_by_id=current_user.id,
        )
        # In-app webcam capture (no external upload).
        photo_rel_path = None
        if form.photo_data.data:
            photo_rel_path = save_or_keep_resident_photo(form.photo_data.data)
        if photo_rel_path:
            resident.photo_path = photo_rel_path

        signature_rel_path = save_or_keep_resident_signature(form.signature_data.data)
        if signature_rel_path:
            resident.signature_path = signature_rel_path

        db.session.add(resident)
        # Ensure we have an ID for consistent auto-generated Barangay IDs
        db.session.flush()
        if not resident.barangay_id:
            resident.barangay_id = f"KNL-{dt_date.today().year}-{resident.id:05d}"

        db.session.commit()
        log_action(
            f"Created resident #{resident.id} ({resident.last_name}, {resident.first_name})".upper(),
            entity_type="resident",
            entity_id=resident.id,
            meta={"barangay_id": resident.barangay_id},
        )
        flash("Resident added successfully!", "success")
        return redirect(url_for("main.list_residents"))
    return render_template("resident_form.html", form=form, title="Add Resident")


@main_bp.route("/residents/<int:resident_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin", "clerk")
def edit_resident(resident_id: int):
    resident = db.get_or_404(Resident, resident_id)
    if resident.is_archived:
        flash("Archived residents cannot be edited. Restore first.", "warning")
        return redirect(url_for("main.list_residents"))
    form = ResidentForm(obj=resident)
    _populate_resident_street_choices(form)
    # Make the button label clearer in edit mode
    form.submit.label.text = "Update"

    if form.validate_on_submit():
        if form.birth_date.data and form.birth_date.data > dt_date.today():
            form.birth_date.errors.append("Birth date cannot be in the future.")
            return render_template("resident_form.html", form=form, title="Edit Resident")

        first = (form.first_name.data or "").strip()
        last = (form.last_name.data or "").strip()
        existing = Resident.query.filter(
            func.lower(Resident.first_name) == first.lower(),
            func.lower(Resident.last_name) == last.lower(),
            Resident.birth_date == form.birth_date.data,
            Resident.id != resident.id,
        ).first()
        if existing:
            msg = "Resident already exists."
            if existing.is_archived:
                msg = "Resident already exists but is archived. Restore the record instead."
            form.first_name.errors.append(msg)
            return render_template("resident_form.html", form=form, title="Edit Resident")

        barangay_id = form.barangay_id.data.strip() if form.barangay_id.data else resident.barangay_id
        if barangay_id:
            normalized = barangay_id.strip().upper()
            current = (resident.barangay_id or "").strip().upper()
            if normalized != current and not KNL_ID_PATTERN.match(normalized):
                form.barangay_id.errors.append("Barangay ID must follow format KNL-YYYY-##### (e.g., KNL-2026-00001).")
                return render_template("resident_form.html", form=form, title="Edit Resident")
            existing = Resident.query.filter(
                func.upper(Resident.barangay_id) == normalized,
                Resident.id != resident.id,
            ).first()
            if existing:
                form.barangay_id.errors.append("Barangay ID is already in use.")
                return render_template("resident_form.html", form=form, title="Edit Resident")
            barangay_id = normalized

        resident.barangay_id = barangay_id
        resident.first_name = form.first_name.data
        resident.middle_name = form.middle_name.data
        resident.last_name = form.last_name.data
        resident.gender = form.gender.data
        resident.birth_date = form.birth_date.data
        resident.marital_status = form.marital_status.data
        resident.contact_number = normalize_phone_for_storage(form.contact_number.data)
        resident.occupation = (form.occupation.data or "").strip() or None
        resident.years_on_barangay = int(form.years_on_barangay.data) if (form.years_on_barangay.data or "").strip().isdigit() else None
        resident.emergency_contact_name = (form.emergency_contact_name.data or "").strip() or None
        resident.emergency_contact_relationship = (form.emergency_contact_relationship.data or "").strip() or None
        resident.emergency_contact_number = normalize_phone_for_storage(form.emergency_contact_number.data)
        resident.emergency_contact_address = _resolve_emergency_contact_address(form)
        resident.street_id = form.street_id.data
        resident.address = form.address.data
        resident.updated_at = utcnow()
        resident.updated_by_id = current_user.id
        # Update photo only if a new capture was provided
        new_photo_rel_path = None
        if form.photo_data.data:
            new_photo_rel_path = save_or_keep_resident_photo(form.photo_data.data)

        if new_photo_rel_path:
            resident.photo_path = new_photo_rel_path

        # Update signature only if a new one was captured
        if form.signature_data.data:
            sig_rel_path = save_or_keep_resident_signature(form.signature_data.data)
            if sig_rel_path:
                resident.signature_path = sig_rel_path

        db.session.commit()
        log_action(
            f"Updated resident #{resident.id}",
            entity_type="resident",
            entity_id=resident.id,
        )
        flash("Resident updated successfully!", "success")
        return redirect(url_for("main.list_residents"))

    return render_template("resident_form.html", form=form, title="Edit Resident")


@main_bp.route("/residents/<int:resident_id>")
@login_required
@roles_required("admin", "clerk")
def resident_profile(resident_id: int):
    resident = db.get_or_404(Resident, resident_id)
    doc_status = (request.args.get("status") or "").strip()
    show_archived = (request.args.get("archived") or "").strip() == "1"
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))

    docs_query = Document.query.filter(Document.resident_id == resident.id)
    if not show_archived:
        docs_query = docs_query.filter(Document.is_archived.is_(False))
    if doc_status in DOCUMENT_STATUSES:
        if doc_status == "draft":
            docs_query = docs_query.filter(Document.status.in_(DRAFT_LIKE_STATUSES))
        else:
            docs_query = docs_query.filter(Document.status == doc_status)
    docs_query = docs_query.order_by(Document.issue_date.desc())

    pagination = db.paginate(docs_query, page=page, per_page=per_page, error_out=False)
    documents = pagination.items
    doc_types = DocumentType.query.order_by(DocumentType.name.asc()).all()
    user_ids = {
        uid
        for doc in documents
        for uid in (doc.updated_by_id, doc.issued_by_id, doc.created_by_id)
        if uid
    }
    user_map = _build_user_map(user_ids)
    updated_by = None
    if resident.updated_by_id:
        updated_by = user_map.get(resident.updated_by_id)
    if not updated_by and resident.created_by_id:
        updated_by = _build_user_map({resident.created_by_id}).get(resident.created_by_id)

    # Recent activity for this resident
    try:
        recent_activity = TransactionLog.query.filter(
            TransactionLog.entity_type == "resident",
            TransactionLog.entity_id == resident.id,
        ).order_by(TransactionLog.timestamp.desc()).limit(10).all()
    except Exception:
        recent_activity = []

    return render_template(
        "resident_detail.html",
        resident=resident,
        documents=documents,
        pagination=pagination,
        doc_status=doc_status,
        show_archived=show_archived,
        document_types=doc_types,
        user_map=user_map,
        resident_updated_by=updated_by,
        recent_activity=recent_activity,
    )


@main_bp.route("/residents/<int:resident_id>/delete", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def delete_resident(resident_id: int):
    resident = db.get_or_404(Resident, resident_id)
    display = f"{resident.last_name}, {resident.first_name}".upper()
    resident.is_archived = True
    resident.archived_at = utcnow()
    resident.archived_by_id = current_user.id
    resident.updated_at = utcnow()
    resident.updated_by_id = current_user.id
    Document.query.filter(
        Document.resident_id == resident.id,
        Document.is_archived.is_(False),
    ).update(
        {
            "is_archived": True,
            "archived_at": utcnow(),
            "archived_by_id": current_user.id,
            "updated_at": utcnow(),
            "updated_by_id": current_user.id,
        },
        synchronize_session=False,
    )
    db.session.commit()
    log_action(
        f"Archived resident #{resident_id} ({display})",
        entity_type="resident",
        entity_id=resident_id,
    )
    flash("Resident archived.", "info")
    return redirect(url_for("main.list_residents"))


@main_bp.route("/residents/bulk-archive", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def bulk_archive_residents():
    ids = [int(x) for x in request.form.getlist("resident_ids") if x.isdigit()]
    if not ids:
        flash("Select at least one resident to archive.", "warning")
        return redirect(url_for("main.list_residents"))

    residents = Resident.query.filter(Resident.id.in_(ids), Resident.is_archived.is_(False)).all()
    if not residents:
        flash("No active residents selected.", "warning")
        return redirect(url_for("main.list_residents"))

    now = utcnow()
    resident_ids = []
    for resident in residents:
        resident_ids.append(resident.id)
        resident.is_archived = True
        resident.archived_at = now
        resident.archived_by_id = current_user.id
        resident.updated_at = now
        resident.updated_by_id = current_user.id

    Document.query.filter(
        Document.resident_id.in_(resident_ids),
        Document.is_archived.is_(False),
    ).update(
        {
            "is_archived": True,
            "archived_at": now,
            "archived_by_id": current_user.id,
            "updated_at": now,
            "updated_by_id": current_user.id,
        },
        synchronize_session=False,
    )

    db.session.commit()

    for resident in residents:
        display = f"{resident.last_name}, {resident.first_name}".upper()
        log_action(
            f"Archived resident #{resident.id} ({display}) (bulk)",
            entity_type="resident",
            entity_id=resident.id,
        )

    flash(f"Archived {len(residents)} resident(s).", "info")
    return redirect(url_for("main.list_residents"))


@main_bp.route("/residents/<int:resident_id>/restore", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def restore_resident(resident_id: int):
    resident = db.get_or_404(Resident, resident_id)
    resident.is_archived = False
    resident.archived_at = None
    resident.archived_by_id = None
    resident.updated_at = utcnow()
    resident.updated_by_id = current_user.id
    db.session.commit()
    log_action(
        f"Restored resident #{resident_id}",
        entity_type="resident",
        entity_id=resident_id,
    )
    flash("Resident restored.", "success")
    return redirect(url_for("main.list_archived_residents"))


@main_bp.route("/residents/<int:resident_id>/purge", methods=["POST"])
@login_required
@roles_required("admin")
def purge_resident(resident_id: int):
    resident = db.get_or_404(Resident, resident_id)
    if not resident.is_archived:
        flash("Only archived residents can be permanently deleted.", "warning")
        return redirect(url_for("main.list_residents"))
    if resident.photo_path:
        abs_path = os.path.join(current_app.static_folder, resident.photo_path)
        if os.path.isfile(abs_path):
            os.remove(abs_path)
    db.session.delete(resident)
    db.session.commit()
    log_action(
        f"Permanently deleted resident #{resident_id}",
        entity_type="resident",
        entity_id=resident_id,
    )
    flash("Resident permanently deleted.", "success")
    return redirect(url_for("main.list_archived_residents"))


@main_bp.route("/residents/bulk-restore", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def bulk_restore_residents():
    ids = [int(x) for x in request.form.getlist("resident_ids") if x.isdigit()]
    if not ids:
        flash("Select at least one resident to restore.", "warning")
        return redirect(url_for("main.list_archived_residents"))
    residents = Resident.query.filter(Resident.id.in_(ids), Resident.is_archived.is_(True)).all()
    if not residents:
        flash("No archived residents selected.", "warning")
        return redirect(url_for("main.list_archived_residents"))
    now = utcnow()
    for resident in residents:
        resident.is_archived = False
        resident.archived_at = None
        resident.archived_by_id = None
        resident.updated_at = now
        resident.updated_by_id = current_user.id
    db.session.commit()
    for resident in residents:
        log_action(
            f"Restored resident #{resident.id} (bulk)",
            entity_type="resident",
            entity_id=resident.id,
        )
    flash(f"Restored {len(residents)} resident(s).", "success")
    return redirect(url_for("main.list_archived_residents"))


@main_bp.route("/residents/bulk-purge", methods=["POST"])
@login_required
@roles_required("admin")
def bulk_purge_residents():
    ids = [int(x) for x in request.form.getlist("resident_ids") if x.isdigit()]
    if not ids:
        flash("Select at least one resident to delete.", "warning")
        return redirect(url_for("main.list_archived_residents"))
    residents = Resident.query.filter(Resident.id.in_(ids), Resident.is_archived.is_(True)).all()
    if not residents:
        flash("No archived residents selected.", "warning")
        return redirect(url_for("main.list_archived_residents"))
    for resident in residents:
        if resident.photo_path:
            abs_path = os.path.join(current_app.static_folder, resident.photo_path)
            if os.path.isfile(abs_path):
                os.remove(abs_path)
        db.session.delete(resident)
    db.session.commit()
    for resident in residents:
        log_action(
            f"Permanently deleted resident #{resident.id} (bulk)",
            entity_type="resident",
            entity_id=resident.id,
        )
    flash(f"Permanently deleted {len(residents)} archived resident(s).", "success")
    return redirect(url_for("main.list_archived_residents"))


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@main_bp.route("/documents")
@login_required
@roles_required("admin", "clerk")
def list_documents():
    q = (request.args.get("q") or "").strip()
    type_id = (request.args.get("type") or "").strip()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()
    sort = (request.args.get("sort") or "issue_desc").strip()
    status = (request.args.get("status") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))

    query = Document.query.join(Resident).join(DocumentType)
    query = query.filter(Document.is_archived.is_(False))

    if q:
        query = query.filter(_multi_search_filter(q, DOCUMENT_SEARCH_COLUMNS))

    if type_id.isdigit():
        query = query.filter(Document.document_type_id == int(type_id))

    if status in DOCUMENT_STATUSES:
        if status == "draft":
            query = query.filter(Document.status.in_(DRAFT_LIKE_STATUSES))
        else:
            query = query.filter(Document.status == status)

    # Optional date range (YYYY-MM-DD)
    try:
        if date_from:
            df = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(Document.issue_date >= df)
        if date_to:
            dt_ = datetime.strptime(date_to, "%Y-%m-%d")
            query = query.filter(Document.issue_date <= dt_)
    except ValueError:
        pass

    # Sorting
    if sort == "issue_asc":
        query = query.order_by(Document.issue_date.asc(), Document.id.asc())
    elif sort == "type_asc":
        query = query.order_by(DocumentType.name.asc(), Document.issue_date.desc())
    elif sort == "type_desc":
        query = query.order_by(DocumentType.name.desc(), Document.issue_date.desc())
    elif sort == "resident_asc":
        query = query.order_by(Resident.last_name.asc(), Resident.first_name.asc(), Document.issue_date.desc())
    elif sort == "resident_desc":
        query = query.order_by(Resident.last_name.desc(), Resident.first_name.desc(), Document.issue_date.desc())
    else:
        query = query.order_by(Document.issue_date.desc(), Document.id.desc())

    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    documents = pagination.items
    types = DocumentType.query.order_by(DocumentType.name.asc()).all()
    user_ids = {
        uid
        for doc in documents
        for uid in (doc.updated_by_id, doc.issued_by_id, doc.created_by_id)
        if uid
    }
    user_map = _build_user_map(user_ids)
    return render_template(
        "documents.html",
        documents=documents,
        document_types=types,
        q=q,
        type_id=type_id,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        status=status,
        archived_view=False,
        pagination=pagination,
        user_map=user_map,
    )


@main_bp.route("/documents/archived")
@login_required
@roles_required("admin", "clerk")
def list_archived_documents():
    q = (request.args.get("q") or "").strip()
    type_id = (request.args.get("type") or "").strip()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()
    sort = (request.args.get("sort") or "issue_desc").strip()
    status = (request.args.get("status") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))

    query = Document.query.join(Resident).join(DocumentType).filter(Document.is_archived.is_(True))

    if q:
        query = query.filter(_multi_search_filter(q, DOCUMENT_SEARCH_COLUMNS))

    if type_id.isdigit():
        query = query.filter(Document.document_type_id == int(type_id))

    if status in DOCUMENT_STATUSES:
        if status == "draft":
            query = query.filter(Document.status.in_(DRAFT_LIKE_STATUSES))
        else:
            query = query.filter(Document.status == status)

    # Optional date range (YYYY-MM-DD)
    try:
        if date_from:
            df = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(Document.issue_date >= df)
        if date_to:
            dt_ = datetime.strptime(date_to, "%Y-%m-%d")
            query = query.filter(Document.issue_date <= dt_)
    except ValueError:
        pass

    # Sorting
    if sort == "issue_asc":
        query = query.order_by(Document.issue_date.asc(), Document.id.asc())
    elif sort == "type_asc":
        query = query.order_by(DocumentType.name.asc(), Document.issue_date.desc())
    elif sort == "type_desc":
        query = query.order_by(DocumentType.name.desc(), Document.issue_date.desc())
    elif sort == "resident_asc":
        query = query.order_by(Resident.last_name.asc(), Resident.first_name.asc(), Document.issue_date.desc())
    elif sort == "resident_desc":
        query = query.order_by(Resident.last_name.desc(), Resident.first_name.desc(), Document.issue_date.desc())
    else:
        query = query.order_by(Document.issue_date.desc(), Document.id.desc())

    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    documents = pagination.items
    types = DocumentType.query.order_by(DocumentType.name.asc()).all()
    user_ids = {
        uid
        for doc in documents
        for uid in (doc.updated_by_id, doc.issued_by_id, doc.created_by_id)
        if uid
    }
    user_map = _build_user_map(user_ids)
    return render_template(
        "documents.html",
        documents=documents,
        document_types=types,
        q=q,
        type_id=type_id,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        status=status,
        archived_view=True,
        pagination=pagination,
        user_map=user_map,
    )


@main_bp.route("/documents/<int:document_id>/pdf", methods=["GET"])
@login_required
@roles_required("admin", "clerk")
def download_document_pdf(document_id: int):
    """Download the generated document. Serves PDF when available, otherwise falls back to DOCX."""
    doc = db.get_or_404(Document, document_id)
    if doc.is_archived:
        flash("Archived documents cannot be downloaded.", "warning")
        return redirect(url_for("main.list_documents"))
    if doc.status != "issued":
        flash("Only issued documents can be downloaded.", "warning")
        return redirect(url_for("main.list_documents"))

    file_path = doc.file_path or doc.generated_docx_path
    if not file_path:
        flash("No generated document file found.", "danger")
        return redirect(url_for("main.list_documents"))

    abs_path = resolve_stored_path_to_abs(file_path)
    if not abs_path or not os.path.exists(abs_path):
        flash("Document file is missing on disk. Please run document regeneration.", "danger")
        return redirect(url_for("main.list_documents"))

    is_pdf = abs_path.suffix.lower() == ".pdf"
    mimetype = "application/pdf" if is_pdf else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    log_action(
        "Downloaded document PDF" if is_pdf else "Downloaded document DOCX",
        entity_type="document",
        entity_id=doc.id,
        meta={
            "resident_id": doc.resident_id,
            "document_type_id": doc.document_type_id,
        },
    )

    return send_file(str(abs_path), mimetype=mimetype, as_attachment=True)


@main_bp.route("/documents/issue", methods=["GET", "POST"])
@login_required
@roles_required("admin", "clerk")
def issue_document():
    form = DocumentForm()
    field_configs = _document_type_field_configs()
    resident_field_values = _resident_document_field_values()
    resident_photo_values = _resident_photo_values()
    resident_signature_values = _resident_signature_values()
    form.resident_id.choices = [
        (r.id, f"{r.last_name}, {r.first_name}".upper())
        for r in Resident.query.filter(Resident.is_archived.is_(False)).order_by(Resident.last_name.asc())
    ]
    form.document_type_id.choices = [
        (d.id, d.name) for d in DocumentType.query.order_by(DocumentType.name.asc())
    ]

    if request.method == "GET":
        pref_resident_id = request.args.get("resident_id", type=int)
        pref_doc_type_id = request.args.get("document_type_id", type=int)
        if pref_resident_id and form.resident_id.data is None:
            form.resident_id.data = pref_resident_id
        if pref_doc_type_id and form.document_type_id.data is None:
            form.document_type_id.data = pref_doc_type_id

    if form.validate_on_submit():
        resident = db.get_or_404(Resident, form.resident_id.data)
        doc_type = db.get_or_404(DocumentType, form.document_type_id.data)
        if resident.is_archived:
            flash("Cannot create documents for archived residents.", "warning")
            return render_template("document_form.html", form=form, title="Create Draft", document_type_field_configs=field_configs, custom_field_values=_posted_document_field_values(), resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)

        if form.issue_date.data and form.issue_date.data > dt_date.today():
            form.issue_date.errors.append("Issue date cannot be in the future.")
            return render_template("document_form.html", form=form, title="Create Draft", document_type_field_configs=field_configs, custom_field_values=_posted_document_field_values(), resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)
        if form.issue_date.data and resident.birth_date and form.issue_date.data < resident.birth_date:
            form.issue_date.errors.append("Issue date cannot be before the resident's birth date.")
            return render_template("document_form.html", form=form, title="Create Draft", document_type_field_configs=field_configs, custom_field_values=_posted_document_field_values(), resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)

        field_values, field_errors = _collect_document_field_values(doc_type, resident)
        if field_errors:
            for error in field_errors:
                flash(error, "danger")
            return render_template("document_form.html", form=form, title="Create Draft", document_type_field_configs=field_configs, custom_field_values=_posted_document_field_values(), resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)

        # If the user captured a new photo during issuance, store it on the resident record
        if form.resident_photo_data.data:
            new_path = save_or_keep_resident_photo(form.resident_photo_data.data)
            if new_path:
                resident.photo_path = new_path

        issued = form.issue_date.data or dt_date.today()
        doc = Document(
            resident_id=resident.id,
            document_type_id=doc_type.id,
            details=form.details.data,
            field_values=json.dumps(field_values) if field_values else None,
            issue_date=issued,
            status="draft",
            created_by_id=current_user.id,
        )
        db.session.add(doc)
        db.session.commit()

        log_action(
            f"Created document draft #{doc.id} (type_id={doc.document_type_id}) for resident_id={doc.resident_id}",
            entity_type="document",
            entity_id=doc.id,
            meta={"resident_id": doc.resident_id, "document_type_id": doc.document_type_id, "status": doc.status},
        )
        flash("Document draft created.", "success")
        return redirect(url_for("main.list_documents"))

    # Default issue date for convenience
    if not form.issue_date.data:
        form.issue_date.data = dt_date.today()

    return render_template("document_form.html", form=form, title="Create Draft", document_type_field_configs=field_configs, custom_field_values={}, resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)


@main_bp.route("/documents/<int:document_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin", "clerk")
def edit_document(document_id: int):
    document = db.get_or_404(Document, document_id)
    field_configs = _document_type_field_configs()
    resident_field_values = _resident_document_field_values()
    resident_photo_values = _resident_photo_values()
    resident_signature_values = _resident_signature_values()
    if document.is_archived:
        flash("Archived documents cannot be edited.", "warning")
        return redirect(url_for("main.list_documents"))
    if document.status == "issued":
        flash("Issued documents cannot be edited. Use Revise to create a new draft.", "warning")
        return redirect(url_for("main.list_documents"))
    form = DocumentForm(obj=document)

    _ctx = dict(
        form=form, title="Edit Document", document=document, doc_history=doc_history,
        document_type_field_configs=field_configs,
    )

    # Document history (revision trail)
    try:
        doc_history = TransactionLog.query.filter(
            TransactionLog.entity_type == "document",
            TransactionLog.entity_id == document.id,
        ).order_by(TransactionLog.timestamp.desc()).limit(20).all()
    except Exception:
        doc_history = []

    # Populate selects
    form.resident_id.choices = [
        (r.id, f"{r.last_name}, {r.first_name}".upper())
        for r in Resident.query.filter(Resident.is_archived.is_(False)).order_by(Resident.last_name.asc())
    ]
    form.document_type_id.choices = [
        (d.id, d.name) for d in DocumentType.query.order_by(DocumentType.name.asc())
    ]
    form.submit.label.text = "Update"

    # Set defaults for GET
    if form.resident_id.data is None:
        form.resident_id.data = document.resident_id
    if form.document_type_id.data is None:
        form.document_type_id.data = document.document_type_id
    if not form.issue_date.data:
        form.issue_date.data = document.issue_date

    if form.validate_on_submit():
        resident = db.get_or_404(Resident, form.resident_id.data)
        if resident.is_archived:
            flash("Cannot assign archived residents to documents.", "warning")
            return render_template("document_form.html", **_ctx, custom_field_values=_posted_document_field_values(), resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)

        if form.issue_date.data and form.issue_date.data > dt_date.today():
            form.issue_date.errors.append("Issue date cannot be in the future.")
            return render_template("document_form.html", **_ctx, custom_field_values=_posted_document_field_values(), resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)
        if form.issue_date.data and resident.birth_date and form.issue_date.data < resident.birth_date:
            form.issue_date.errors.append("Issue date cannot be before the resident's birth date.")
            return render_template("document_form.html", **_ctx, custom_field_values=_posted_document_field_values(), resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)

        doc_type = db.get_or_404(DocumentType, form.document_type_id.data)
        field_values, field_errors = _collect_document_field_values(doc_type, resident)
        if field_errors:
            for error in field_errors:
                flash(error, "danger")
            return render_template("document_form.html", **_ctx, custom_field_values=_posted_document_field_values(), resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)

        document.resident_id = form.resident_id.data
        document.document_type_id = form.document_type_id.data
        document.details = form.details.data
        document.field_values = json.dumps(field_values) if field_values else None
        document.issue_date = form.issue_date.data or document.issue_date
        document.updated_at = utcnow()
        document.updated_by_id = current_user.id

        if form.resident_photo_data.data:
            new_path = save_or_keep_resident_photo(form.resident_photo_data.data)
            if new_path:
                resident.photo_path = new_path
            resident.updated_at = utcnow()
            resident.updated_by_id = current_user.id

        if document.status in {"approved", "pending"}:
            document.status = "draft"
            document.approved_at = None
            document.approved_by_id = None

        db.session.commit()

        log_action(
            f"Updated document #{document.id}",
            entity_type="document",
            entity_id=document.id,
            meta={"status": document.status},
        )
        flash("Document updated successfully!", "success")
        return redirect(url_for("main.list_documents"))

    return render_template("document_form.html", **_ctx, custom_field_values=_document_field_values(document), resident_field_values=resident_field_values, resident_photo_values=resident_photo_values, resident_signature_values=resident_signature_values)


@main_bp.route("/documents/<int:document_id>/issue", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def finalize_document_issue(document_id: int):
    document = db.get_or_404(Document, document_id)
    if document.is_archived:
        flash("Archived documents cannot be issued.", "warning")
        return redirect(url_for("main.list_documents"))
    if document.status not in DRAFT_LIKE_STATUSES:
        flash("Only draft documents can be issued.", "warning")
        return redirect(url_for("main.list_documents"))

    resident = document.resident
    doc_type = document.document_type
    if resident and resident.is_archived:
        flash("Cannot issue documents for archived residents.", "warning")
        return redirect(url_for("main.list_documents"))

    if doc_type and doc_type.requires_photo and resident and not resident.photo_path:
        flash(
            f"{doc_type.name} requires a resident photo. Please capture a photo before issuing.",
            "warning",
        )
        return redirect(url_for("main.list_documents"))

    issue_date = document.issue_date.date() if hasattr(document.issue_date, "date") else document.issue_date
    if issue_date and issue_date > dt_date.today():
        flash("Issue date cannot be in the future.", "warning")
        return redirect(url_for("main.list_documents"))
    if issue_date and resident and resident.birth_date and issue_date < resident.birth_date:
        flash("Issue date cannot be before the resident's birth date.", "warning")
        return redirect(url_for("main.list_documents"))

    if not document.issue_date:
        document.issue_date = utcnow()

    try:
        docx_rel_path, pdf_rel_path = render_document_files(document)
    except DocumentGenerationError as exc:
        document.generation_status = "failed"
        document.generation_error = str(exc)
        db.session.commit()
        flash(f"Document issuance blocked: {exc}", "danger")
        return redirect(url_for("main.list_documents"))

    if document.status in {"pending", "approved"}:
        document.approved_at = None
        document.approved_by_id = None
    document.status = "issued"
    document.issued_at = utcnow()
    document.issued_by_id = current_user.id
    document.updated_at = utcnow()
    document.updated_by_id = current_user.id

    document.generated_docx_path = docx_rel_path
    document.file_path = pdf_rel_path
    document.generation_status = "ready"
    document.generation_error = None
    document.generated_at = utcnow()
    db.session.commit()

    log_action(
        f"Issued document #{document.id}",
        entity_type="document",
        entity_id=document.id,
        meta={"status": document.status},
    )
    flash("Document issued successfully!", "success")
    return redirect(url_for("main.list_documents"))


@main_bp.route("/documents/<int:document_id>/request-approval", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def request_document_approval(document_id: int):
    document = db.get_or_404(Document, document_id)
    if document.is_archived:
        flash("Archived documents cannot be submitted for approval.", "warning")
        return redirect(url_for("main.list_documents"))
    if document.status != "draft":
        flash("Only draft documents can be submitted for approval.", "warning")
        return redirect(url_for("main.list_documents"))

    document.status = "pending"
    document.updated_at = utcnow()
    document.updated_by_id = current_user.id
    db.session.commit()

    log_action(
        f"Requested approval for document #{document.id}",
        entity_type="document",
        entity_id=document.id,
        meta={"status": document.status},
    )
    flash("Document submitted for approval.", "success")
    return redirect(url_for("main.list_documents"))


@main_bp.route("/documents/<int:document_id>/approve", methods=["POST"])
@login_required
@roles_required("admin")
def approve_document(document_id: int):
    document = db.get_or_404(Document, document_id)
    if document.is_archived:
        flash("Archived documents cannot be approved.", "warning")
        return redirect(url_for("main.list_documents"))
    if document.status != "pending":
        flash("Only pending documents can be approved.", "warning")
        return redirect(url_for("main.list_documents"))

    document.status = "approved"
    document.approved_at = utcnow()
    document.approved_by_id = current_user.id
    document.updated_at = utcnow()
    document.updated_by_id = current_user.id
    db.session.commit()

    log_action(
        f"Approved document #{document.id}",
        entity_type="document",
        entity_id=document.id,
        meta={"status": document.status},
    )
    flash("Document approved.", "success")
    return redirect(url_for("main.list_documents"))


@main_bp.route("/documents/<int:document_id>/revise", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def revise_document(document_id: int):
    document = db.get_or_404(Document, document_id)
    if document.is_archived:
        flash("Archived documents cannot be revised.", "warning")
        return redirect(url_for("main.list_documents"))
    if document.status != "issued":
        flash("Only issued documents can be revised.", "warning")
        return redirect(url_for("main.list_documents"))

    new_doc = Document(
        resident_id=document.resident_id,
        document_type_id=document.document_type_id,
        status="draft",
        details=document.details,
        issue_date=dt_date.today(),
        created_by_id=current_user.id,
    )
    db.session.add(new_doc)
    db.session.commit()

    log_action(
        f"Created revision draft #{new_doc.id} from document #{document.id}",
        entity_type="document",
        entity_id=new_doc.id,
        meta={"source_document_id": document.id},
    )
    flash("Draft created from issued document. Update it and re-issue.", "success")
    return redirect(url_for("main.edit_document", document_id=new_doc.id))


@main_bp.route("/documents/<int:document_id>/history")
@login_required
@roles_required("admin", "clerk")
def document_history(document_id: int):
    document = db.get_or_404(Document, document_id)
    logs = (
        TransactionLog.query.filter_by(entity_type="document", entity_id=document.id)
        .order_by(TransactionLog.timestamp.desc())
        .all()
    )
    return render_template("document_history.html", document=document, logs=logs)


@main_bp.route("/documents/<int:document_id>/delete", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def delete_document(document_id: int):
    document = db.get_or_404(Document, document_id)
    document.is_archived = True
    document.archived_at = utcnow()
    document.archived_by_id = current_user.id
    document.updated_at = utcnow()
    document.updated_by_id = current_user.id
    db.session.commit()
    log_action(
        f"Archived document #{document_id}",
        entity_type="document",
        entity_id=document_id,
    )
    flash("Document archived.", "info")
    return redirect(url_for("main.list_documents"))


@main_bp.route("/documents/bulk-archive", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def bulk_archive_documents():
    ids = [int(x) for x in request.form.getlist("document_ids") if x.isdigit()]
    if not ids:
        flash("Select at least one document to archive.", "warning")
        return redirect(url_for("main.list_documents"))

    docs = Document.query.filter(Document.id.in_(ids), Document.is_archived.is_(False)).all()
    if not docs:
        flash("No active documents selected.", "warning")
        return redirect(url_for("main.list_documents"))

    now = utcnow()
    for doc in docs:
        doc.is_archived = True
        doc.archived_at = now
        doc.archived_by_id = current_user.id
        doc.updated_at = now
        doc.updated_by_id = current_user.id

    db.session.commit()

    for doc in docs:
        log_action(
            f"Archived document #{doc.id} (bulk)",
            entity_type="document",
            entity_id=doc.id,
        )
    flash(f"Archived {len(docs)} document(s).", "info")
    return redirect(url_for("main.list_documents"))


@main_bp.route("/documents/<int:document_id>/restore", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def restore_document(document_id: int):
    document = db.get_or_404(Document, document_id)
    document.is_archived = False
    document.archived_at = None
    document.archived_by_id = None
    document.updated_at = utcnow()
    document.updated_by_id = current_user.id
    db.session.commit()
    log_action(
        f"Restored document #{document_id}",
        entity_type="document",
        entity_id=document_id,
    )
    flash("Document restored.", "success")
    return redirect(url_for("main.list_archived_documents"))


@main_bp.route("/documents/<int:document_id>/purge", methods=["POST"])
@login_required
@roles_required("admin")
def purge_document(document_id: int):
    document = db.get_or_404(Document, document_id)
    if not document.is_archived:
        flash("Only archived documents can be permanently deleted.", "warning")
        return redirect(url_for("main.list_documents"))
    if document.file_path:
        abs_path = resolve_stored_path_to_abs(document.file_path)
        if abs_path and os.path.isfile(abs_path):
            os.remove(abs_path)
    db.session.delete(document)
    db.session.commit()
    log_action(
        f"Permanently deleted document #{document_id}",
        entity_type="document",
        entity_id=document_id,
    )
    flash("Document permanently deleted.", "success")
    return redirect(url_for("main.list_archived_documents"))


@main_bp.route("/documents/bulk-restore", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def bulk_restore_documents():
    ids = [int(x) for x in request.form.getlist("document_ids") if x.isdigit()]
    if not ids:
        flash("Select at least one document to restore.", "warning")
        return redirect(url_for("main.list_archived_documents"))
    docs = Document.query.filter(Document.id.in_(ids), Document.is_archived.is_(True)).all()
    if not docs:
        flash("No archived documents selected.", "warning")
        return redirect(url_for("main.list_archived_documents"))
    now = utcnow()
    for doc in docs:
        doc.is_archived = False
        doc.archived_at = None
        doc.archived_by_id = None
        doc.updated_at = now
        doc.updated_by_id = current_user.id
    db.session.commit()
    for doc in docs:
        log_action(
            f"Restored document #{doc.id} (bulk)",
            entity_type="document",
            entity_id=doc.id,
        )
    flash(f"Restored {len(docs)} document(s).", "success")
    return redirect(url_for("main.list_archived_documents"))


@main_bp.route("/documents/bulk-purge", methods=["POST"])
@login_required
@roles_required("admin")
def bulk_purge_documents():
    ids = [int(x) for x in request.form.getlist("document_ids") if x.isdigit()]
    if not ids:
        flash("Select at least one document to delete.", "warning")
        return redirect(url_for("main.list_archived_documents"))
    docs = Document.query.filter(Document.id.in_(ids), Document.is_archived.is_(True)).all()
    if not docs:
        flash("No archived documents selected.", "warning")
        return redirect(url_for("main.list_archived_documents"))
    for doc in docs:
        if doc.file_path:
            abs_path = resolve_stored_path_to_abs(doc.file_path)
            if abs_path and os.path.isfile(abs_path):
                os.remove(abs_path)
        db.session.delete(doc)
    db.session.commit()
    for doc in docs:
        log_action(
            f"Permanently deleted document #{doc.id} (bulk)",
            entity_type="document",
            entity_id=doc.id,
        )
    flash(f"Permanently deleted {len(docs)} archived document(s).", "success")
    return redirect(url_for("main.list_archived_documents"))


@main_bp.route("/documents/bulk-issue", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def bulk_issue_documents():
    resident_ids = [int(x) for x in request.form.getlist("resident_ids") if x.isdigit()]
    doc_type_id = request.form.get("document_type_id", type=int)
    if not resident_ids:
        flash("Select at least one resident.", "warning")
        return redirect(request.referrer or url_for("main.list_residents"))
    if not doc_type_id:
        flash("Select a document type.", "warning")
        return redirect(request.referrer or url_for("main.list_residents"))
    doc_type = db.session.get(DocumentType, doc_type_id)
    if not doc_type:
        flash("Invalid document type.", "warning")
        return redirect(request.referrer or url_for("main.list_residents"))
    residents = Resident.query.filter(Resident.id.in_(resident_ids), Resident.is_archived.is_(False)).all()
    if not residents:
        flash("No active residents found for the selected IDs.", "warning")
        return redirect(request.referrer or url_for("main.list_residents"))
    today = dt_date.today()
    count = 0
    for resident in residents:
        doc = Document(
            resident_id=resident.id,
            document_type_id=doc_type.id,
            status="draft",
            issue_date=today,
            created_by_id=current_user.id,
        )
        db.session.add(doc)
        count += 1
    db.session.commit()
    log_action(
        f"Bulk issued {count} '{doc_type.name}' draft(s)",
        entity_type="document",
        meta={"count": count, "document_type_id": doc_type.id},
    )
    flash(f"Created {count} draft document(s) of type '{doc_type.name}'.", "success")
    return redirect(url_for("main.list_documents"))


@main_bp.route("/residents/export")
@login_required
@roles_required("admin", "clerk")
def export_residents():
    fmt = request.args.get("format", "csv").lower()
    q = (request.args.get("q") or "").strip()
    query = Resident.query.filter(Resident.is_archived.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Resident.first_name.ilike(like),
                Resident.last_name.ilike(like),
                Resident.knl_id.ilike(like),
            )
        )
    residents = query.order_by(Resident.last_name, Resident.first_name).all()
    rows = []
    for r in residents:
        rows.append({
            "KNL ID": r.knl_id,
            "Last Name": r.last_name,
            "First Name": r.first_name,
            "Middle Name": r.middle_name or "",
            "Suffix": r.suffix or "",
            "Gender": r.gender or "",
            "Birth Date": r.birth_date.strftime("%Y-%m-%d") if r.birth_date else "",
            "Age": r.age if r.age is not None else "",
            "Marital Status": r.marital_status or "",
            "Mobile": r.mobile or "",
            "Email": r.email or "",
            "Address": r.address or "",
        })
    filename = f"residents_export_{utcnow().strftime('%Y%m%d_%H%M%S')}"
    if fmt == "xlsx":
        if not rows:
            flash("No residents to export.", "info")
            return redirect(request.referrer or url_for("main.list_residents"))
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "Residents"
        headers = list(rows[0].keys())
        ws.append(headers)
        col_widths = [max(len(str(row.get(h, ""))) for row in rows + [dict(zip(headers, headers))]) + 2 for h in headers]
        for col_idx, width in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = min(width, 40)
        for row in rows:
            ws.append([row.get(h, "") for h in headers])
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}.xlsx"},
        )
    if not rows:
        flash("No residents to export.", "info")
        return redirect(request.referrer or url_for("main.list_residents"))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
    )

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
import re
from datetime import date as dt_date, datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import login_required, current_user

from sqlalchemy import func, or_

from .forms import DocumentForm, ResidentForm
from .helpers import log_action, roles_required, save_captured_image
from .pdf_utils import generate_document_pdf
from .extensions import db
from .models import Document, DocumentType, Resident, TransactionLog, User
from .time_utils import utcnow


DOCUMENT_STATUSES = ("draft", "pending", "approved", "issued")
DRAFT_LIKE_STATUSES = ("draft", "pending", "approved")
BRGY_ID_PATTERN = re.compile(r"^BRGY-\\d{4}-\\d{5}$", re.IGNORECASE)


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
        return None


main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def index():
    """Dashboard with quick stats + charts."""
    resident_count = Resident.query.filter(Resident.is_archived.is_(False)).count()
    document_count = Document.query.filter(
        Document.is_archived.is_(False),
        Document.status == "issued",
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
    month_rows = (
        db.session.query(func.to_char(Document.issue_date, 'YYYY-MM'), func.count(Document.id))
        .filter(Document.is_archived.is_(False), Document.status == "issued")
        .group_by(func.to_char(Document.issue_date, 'YYYY-MM'))
        .order_by(func.to_char(Document.issue_date, 'YYYY-MM'))
        .all()
    )
    month_labels = [m for m, _ in month_rows][-6:]
    month_values = [int(c) for _, c in month_rows][-6:]

    # Recent activity (audit trail)
    from .models import TransactionLog

    try:
        recent_logs = TransactionLog.query.order_by(TransactionLog.timestamp.desc()).limit(8).all()
    except Exception:
        # If the audit table isn't available yet, keep the dashboard usable.
        recent_logs = []

    return render_template(
        "index.html",
        resident_count=resident_count,
        document_count=document_count,
        gender_labels=gender_labels,
        gender_values=gender_values,
        doc_type_labels=doc_type_labels,
        doc_type_values=doc_type_values,
        month_labels=month_labels,
        month_values=month_values,
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
            like = f"%{q}%"
            query = query.filter(
                or_(
                    Resident.first_name.ilike(like),
                    Resident.last_name.ilike(like),
                    Resident.barangay_id.ilike(like),
                    DocumentType.name.ilike(like),
                    Document.details.ilike(like),
                )
            )
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
            like = f"%{q}%"
            query = query.filter(
                or_(
                    Resident.first_name.ilike(like),
                    Resident.last_name.ilike(like),
                    Resident.barangay_id.ilike(like),
                    Resident.address.ilike(like),
                )
            )
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

    # Default window: last 30 days
    if not date_to:
        date_to = dt_date.today()
    if not date_from:
        date_from = date_to.replace(day=1)

    query = Document.query.join(DocumentType).join(Resident)
    query = query.filter(
        Document.issue_date >= date_from,
        Document.issue_date <= date_to,
        Document.is_archived.is_(False),
        Document.status == "issued",
    )

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
        Document.issue_date >= date_from,
        Document.issue_date <= date_to,
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
                "Resident": f"{d.resident.last_name}, {d.resident.first_name}" if d.resident else "",
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
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from xml.sax.saxutils import escape as xml_escape

        bio = io.BytesIO()
        doc = SimpleDocTemplate(bio, pagesize=letter, leftMargin=0.5*inch, rightMargin=0.5*inch, topMargin=0.5*inch, bottomMargin=0.5*inch)
        styles = getSampleStyleSheet()
        base_style = ParagraphStyle(
            "ReportBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            alignment=TA_LEFT,
        )
        header_style = ParagraphStyle(
            "ReportHeader",
            parent=base_style,
            fontName="Helvetica-Bold",
        )
        story = []
        story.append(Paragraph(f"Issued Documents Report ({date_from.isoformat()} to {date_to.isoformat()})", styles["Title"]))
        story.append(Spacer(1, 0.2*inch))

        headers = ["Date", "Type", "Resident", "Details"]
        data = [[Paragraph(h, header_style) for h in headers]]
        for d in docs[:300]:
            details = (d.details or "").strip()
            if len(details) > 300:
                details = f"{details[:297]}..."
            data.append([
                Paragraph(xml_escape(d.issue_date.strftime("%Y-%m-%d") if d.issue_date else ""), base_style),
                Paragraph(xml_escape(d.document_type.name if d.document_type else ""), base_style),
                Paragraph(xml_escape(f"{d.resident.last_name}, {d.resident.first_name}" if d.resident else ""), base_style),
                Paragraph(xml_escape(details), base_style),
            ])

        table = Table(data, repeatRows=1, colWidths=[1.0*inch, 1.4*inch, 2.2*inch, 2.4*inch])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("WORDWRAP", (0, 0), (-1, -1), "CJK"),
                ]
            )
        )
        story.append(table)
        doc.build(story)
        bio.seek(0)
        log_action("Exported reports (PDF)", entity_type="report", meta={"from": date_from.isoformat(), "to": date_to.isoformat(), "rows": len(rows)})
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
    sort = (request.args.get("sort") or "name_asc").strip()
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))

    query = Resident.query
    query = query.filter(Resident.is_archived.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Resident.first_name.ilike(like),
                Resident.last_name.ilike(like),
                Resident.barangay_id.ilike(like),
                Resident.address.ilike(like),
            )
        )
    if gender:
        query = query.filter(Resident.gender == gender)

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
    return render_template(
        "residents.html",
        residents=residents,
        q=q,
        gender=gender,
        sort=sort,
        archived_view=False,
        pagination=pagination,
        user_map=user_map,
    )


@main_bp.route("/residents/archived")
@login_required
@roles_required("admin", "clerk")
def list_archived_residents():
    q = (request.args.get("q") or "").strip()
    gender = (request.args.get("gender") or "").strip()
    sort = (request.args.get("sort") or "name_asc").strip()
    page = request.args.get("page", 1, type=int)
    per_page = int(current_app.config.get("DEFAULT_PAGE_SIZE", 20))

    query = Resident.query.filter(Resident.is_archived.is_(True))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Resident.first_name.ilike(like),
                Resident.last_name.ilike(like),
                Resident.barangay_id.ilike(like),
                Resident.address.ilike(like),
            )
        )
    if gender:
        query = query.filter(Resident.gender == gender)

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
    return render_template(
        "residents.html",
        residents=residents,
        q=q,
        gender=gender,
        sort=sort,
        archived_view=True,
        pagination=pagination,
        user_map=user_map,
    )


@main_bp.route("/residents/add", methods=["GET", "POST"])
@login_required
@roles_required("admin", "clerk")
def add_resident():
    form = ResidentForm()
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
            if not BRGY_ID_PATTERN.match(normalized):
                form.barangay_id.errors.append("Barangay ID must follow format BRGY-YYYY-##### (e.g., BRGY-2026-00001).")
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
            address=form.address.data,
            created_by_id=current_user.id,
        )
        # In-app webcam capture (no external upload).
        photo_rel_path = None
        if form.photo_data.data:
            photo_rel_path = save_captured_image(form.photo_data.data, "residents")
        if photo_rel_path:
            resident.photo_path = photo_rel_path
        db.session.add(resident)
        # Ensure we have an ID for consistent auto-generated Barangay IDs
        db.session.flush()
        if not resident.barangay_id:
            resident.barangay_id = f"BRGY-{dt_date.today().year}-{resident.id:05d}"

        db.session.commit()
        log_action(
            f"Created resident #{resident.id} ({resident.last_name}, {resident.first_name})",
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
            if normalized != current and not BRGY_ID_PATTERN.match(normalized):
                form.barangay_id.errors.append("Barangay ID must follow format BRGY-YYYY-##### (e.g., BRGY-2026-00001).")
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
        resident.address = form.address.data
        resident.updated_at = utcnow()
        resident.updated_by_id = current_user.id
        # Update photo only if a new capture was provided
        new_photo_rel_path = None
        if form.photo_data.data:
            new_photo_rel_path = save_captured_image(form.photo_data.data, "residents")

        if new_photo_rel_path:
            resident.photo_path = new_photo_rel_path

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
    )


@main_bp.route("/residents/<int:resident_id>/delete", methods=["POST"])
@login_required
@roles_required("admin", "clerk")
def delete_resident(resident_id: int):
    resident = db.get_or_404(Resident, resident_id)
    display = f"{resident.last_name}, {resident.first_name}"
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
        display = f"{resident.last_name}, {resident.first_name}"
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
        like = f"%{q}%"
        query = query.filter(
            or_(
                Resident.first_name.ilike(like),
                Resident.last_name.ilike(like),
                Resident.barangay_id.ilike(like),
                DocumentType.name.ilike(like),
                Document.details.ilike(like),
            )
        )

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
        like = f"%{q}%"
        query = query.filter(
            or_(
                Resident.first_name.ilike(like),
                Resident.last_name.ilike(like),
                Resident.barangay_id.ilike(like),
                DocumentType.name.ilike(like),
                Document.details.ilike(like),
            )
        )

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
    """Download the locally-generated PDF for a document."""
    doc = db.get_or_404(Document, document_id)
    if doc.is_archived:
        flash("Archived documents cannot be downloaded.", "warning")
        return redirect(url_for("main.list_documents"))
    if doc.status != "issued":
        flash("Only issued documents can be downloaded.", "warning")
        return redirect(url_for("main.list_documents"))
    if not doc.file_path:
        # Try generating on-demand if missing
        pdf_rel_path = generate_document_pdf(doc)
        if pdf_rel_path:
            doc.file_path = pdf_rel_path
            db.session.commit()
        else:
            flash("PDF could not be generated for this document.", "danger")
            return redirect(url_for("main.list_documents"))

    # doc.file_path is stored relative to the /static directory
    pdf_abs_path = os.path.join(current_app.root_path, "static", doc.file_path)
    if not os.path.exists(pdf_abs_path):
        # Auto-regenerate on demand (e.g., after moving ...
        try:
            doc.file_path = generate_document_pdf(doc)
            db.session.commit()
            pdf_abs_path = os.path.join(current_app.root_path, "static", doc.file_path)
        except Exception:
            db.session.rollback()
            flash("PDF file is missing on disk and could not be regenerated.", "danger")
            return redirect(url_for("main.list_documents"))

    if not os.path.exists(pdf_abs_path):
        flash("PDF file is missing on disk. Please re-issue or regenerate.", "danger")
        return redirect(url_for("main.list_documents"))

    log_action(
        "Downloaded document PDF",
        entity_type="document",
        entity_id=doc.id,
        meta={
            "resident_id": doc.resident_id,
            "document_type_id": doc.document_type_id,
        },
    )

    return send_file(pdf_abs_path, mimetype="application/pdf", as_attachment=True)


@main_bp.route("/documents/issue", methods=["GET", "POST"])
@login_required
@roles_required("admin", "clerk")
def issue_document():
    form = DocumentForm()
    form.resident_id.choices = [
        (r.id, f"{r.last_name}, {r.first_name}")
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
            return render_template("document_form.html", form=form, title="Create Draft")

        if form.issue_date.data and form.issue_date.data > dt_date.today():
            form.issue_date.errors.append("Issue date cannot be in the future.")
            return render_template("document_form.html", form=form, title="Create Draft")
        if form.issue_date.data and resident.birth_date and form.issue_date.data < resident.birth_date:
            form.issue_date.errors.append("Issue date cannot be before the resident's birth date.")
            return render_template("document_form.html", form=form, title="Create Draft")

        # If the user captured a new photo during issuance, store it on the resident record
        if form.resident_photo_data.data:
            resident.photo_path = save_captured_image(form.resident_photo_data.data, "residents")

        issued = form.issue_date.data or dt_date.today()
        doc = Document(
            resident_id=resident.id,
            document_type_id=doc_type.id,
            details=form.details.data,
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

    return render_template("document_form.html", form=form, title="Create Draft")


@main_bp.route("/documents/<int:document_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin", "clerk")
def edit_document(document_id: int):
    document = db.get_or_404(Document, document_id)
    if document.is_archived:
        flash("Archived documents cannot be edited.", "warning")
        return redirect(url_for("main.list_documents"))
    if document.status == "issued":
        flash("Issued documents cannot be edited. Use Revise to create a new draft.", "warning")
        return redirect(url_for("main.list_documents"))
    form = DocumentForm(obj=document)

    # Populate selects
    form.resident_id.choices = [
        (r.id, f"{r.last_name}, {r.first_name}")
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
            return render_template("document_form.html", form=form, title="Edit Document")

        if form.issue_date.data and form.issue_date.data > dt_date.today():
            form.issue_date.errors.append("Issue date cannot be in the future.")
            return render_template("document_form.html", form=form, title="Edit Document")
        if form.issue_date.data and resident.birth_date and form.issue_date.data < resident.birth_date:
            form.issue_date.errors.append("Issue date cannot be before the resident's birth date.")
            return render_template("document_form.html", form=form, title="Edit Document")

        document.resident_id = form.resident_id.data
        document.document_type_id = form.document_type_id.data
        document.details = form.details.data
        document.issue_date = form.issue_date.data or document.issue_date
        document.updated_at = utcnow()
        document.updated_by_id = current_user.id

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

    return render_template("document_form.html", form=form, title="Edit Document")


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

    if document.status in {"pending", "approved"}:
        document.approved_at = None
        document.approved_by_id = None
    document.status = "issued"
    document.issued_at = utcnow()
    document.issued_by_id = current_user.id
    document.updated_at = utcnow()
    document.updated_by_id = current_user.id

    if not document.issue_date:
        document.issue_date = utcnow()

    db.session.commit()

    pdf_rel_path = generate_document_pdf(document)
    if pdf_rel_path:
        document.file_path = pdf_rel_path
        db.session.commit()

    log_action(
        f"Issued document #{document.id}",
        entity_type="document",
        entity_id=document.id,
        meta={"status": document.status},
    )
    flash("Document issued successfully!", "success")
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

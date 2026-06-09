"""
DOCX template pipeline.

Handles:
- Loading and validating DOCX templates against required placeholders.
- Building a render context from resident / document data.
- Rendering the template and converting the result to PDF.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
import zipfile
import json
from datetime import date
from pathlib import Path

from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm
from docx import Document as WordDocument
from docx.shared import Inches
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from flask import current_app
from jinja2.exceptions import TemplateSyntaxError
from PIL import Image

from .extensions import db
from .image_processing import composite_transparent_on_white, remove_background_to_white
from .formatting import apply_document_formatting, format_full_address, format_resident_name
from .models import User

REQUIRED_PLACEHOLDERS = {
    "resident_name",
    "address",
    "purpose",
    "issue_date",
    "resident_photo",
    "qr_code",
}


class TemplateValidationError(Exception):
    pass


class DocumentGenerationError(Exception):
    pass


def _safe_filename(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in text).strip("_")


_UUID_PREFIX_RE = re.compile(r"^(?:[0-9a-fA-F]{32}_)+")


def _normalize_template_filename(filename: str) -> str:
    base = os.path.basename(filename)
    base = _UUID_PREFIX_RE.sub("", base)
    safe = _safe_filename(base)
    if not safe.lower().endswith(".docx"):
        safe = f"{safe}.docx"
    return safe


def template_storage_dir() -> Path:
    configured = current_app.config.get("DOCX_TEMPLATE_UPLOAD_DIR")
    if configured:
        base = Path(configured)
    else:
        base = Path(current_app.static_folder) / "uploads" / "doc_templates"
    base.mkdir(parents=True, exist_ok=True)
    return base


def document_output_dir() -> Path:
    configured = current_app.config.get("DOCX_OUTPUT_DIR")
    if configured:
        base = Path(configured)
    else:
        base = Path(current_app.static_folder) / "uploads" / "documents"
    base.mkdir(parents=True, exist_ok=True)
    return base


def resolve_stored_path_to_abs(stored_path: str | None) -> Path | None:
    if not stored_path:
        return None
    rel = str(stored_path).strip().lstrip("/")
    template_abs = template_storage_dir() / rel
    if template_abs.exists():
        return template_abs
    output_abs = document_output_dir() / rel
    if output_abs.exists():
        return output_abs
    # Backward-compatibility with older static-based paths
    static_candidate = Path(current_app.static_folder) / rel
    if static_candidate.exists():
        return static_candidate
    return None


def store_template_upload(file_storage, document_type_name: str) -> tuple[str, str]:
    if not file_storage or not getattr(file_storage, "filename", ""):
        raise TemplateValidationError("Template file is required.")
    filename = str(file_storage.filename)
    if not filename.lower().endswith(".docx"):
        raise TemplateValidationError("Template must be a .docx file.")

    safe_name = _normalize_template_filename(filename)
    doc_type_slug = _safe_filename(document_type_name.lower().replace(" ", "-")) or "document"
    rel_dir = Path(doc_type_slug)
    abs_dir = template_storage_dir() / doc_type_slug
    abs_dir.mkdir(parents=True, exist_ok=True)
    final_name = safe_name
    abs_path = abs_dir / final_name
    file_storage.save(abs_path)
    rel_path = str((rel_dir / final_name).as_posix())
    return rel_path, safe_name


def remove_template_file(rel_template_path: str | None) -> None:
    if not rel_template_path:
        return
    rel = str(rel_template_path).strip().lstrip("/")
    if rel.startswith("static/"):
        rel = rel[len("static/"):]
    abs_path = resolve_stored_path_to_abs(rel)
    try:
        if abs_path and abs_path.exists() and abs_path.is_file():
            abs_path.unlink()
    except Exception:
        current_app.logger.exception("Failed to remove old template file")
        return


def parse_docx_placeholders(abs_template_path: str) -> set[str]:
    placeholders: set[str] = set()
    with zipfile.ZipFile(abs_template_path, "r") as zf:
        xml_entries = [n for n in zf.namelist() if n.startswith("word/") and n.endswith(".xml")]
        pattern = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
        for name in xml_entries:
            raw = zf.read(name).decode("utf-8", errors="ignore")
            placeholders.update(pattern.findall(raw))
    return placeholders


def validate_template(abs_template_path: str) -> dict[str, list[str]]:
    return validate_template_with_required(abs_template_path, REQUIRED_PLACEHOLDERS)


def validate_template_with_required(abs_template_path: str, required_placeholders: set[str], extra_allowed: set[str] | None = None) -> dict[str, list[str]]:
    placeholders = parse_docx_placeholders(abs_template_path)
    missing = sorted(required_placeholders - placeholders)
    allowed = set(OPTIONAL_PLACEHOLDERS) | set(required_placeholders) | set(extra_allowed or set())
    unknown = sorted(p for p in placeholders if p not in allowed)
    return {"missing": missing, "unknown": unknown}


OPTIONAL_PLACEHOLDERS = {
    "resident_id",
    "document_id",
    "document_type",
    "captain_name",
    "resident_signature",
    "year_on_barangay",
    "birth_date",
    "validity",
    "author",
    "marital_status",
    "first_name",
    "middle_name",
    "last_name",
    "validity",
    "expiration_date",
    "emergency_contact_name",
    "emergency_contact_number",
    "emergency_contact_relationship",
    "emergency_contact_address",
}


def _relative_static_to_abs(path_value: str | None) -> str | None:
    if not path_value:
        return None
    rel = str(path_value).strip().lstrip("/")
    if rel.startswith("static/"):
        rel = rel[7:]
    candidate = Path(current_app.static_folder) / rel
    if candidate.exists():
        return str(candidate)

    upload_root = current_app.config.get("UPLOAD_FOLDER")
    if upload_root:
        rel2 = rel
        if rel2.startswith("uploads/"):
            rel2 = rel2[len("uploads/"):]
        upload_candidate = Path(upload_root) / rel2
        if upload_candidate.exists():
            return str(upload_candidate)
    return None


def _resolve_author_name(document) -> str:
    user_id = getattr(document, "issued_by_id", None) or getattr(document, "approved_by_id", None) or getattr(document, "created_by_id", None)
    if not user_id:
        return "System"
    try:
        user = db.session.get(User, user_id)
    except Exception:
        current_app.logger.exception("Failed to resolve user for document")
        user = None
    return getattr(user, "username", None) or f"User {user_id}"


def _compute_year_on_barangay(document, issue_dt: date) -> str:
    resident = document.resident
    if getattr(resident, "years_on_barangay", None) is not None:
        return str(resident.years_on_barangay)
    base = getattr(resident, "created_at", None)
    if hasattr(base, "date"):
        base = base.date()
    if not base:
        return "N/A"
    years = max(0, issue_dt.year - base.year - ((issue_dt.month, issue_dt.day) < (base.month, base.day)))
    return str(years)


def _compute_validity(document, issue_dt: date) -> str:
    months = getattr(document.document_type, "validity_months", None) if document.document_type else None
    if months:
        if months == 1:
            return "1 month"
        if months == 12:
            return "1 year"
        if months < 12:
            return f"{months} months"
        years = months // 12
        remainder = months % 12
        if remainder == 0:
            return f"{years} year{'s' if years > 1 else ''}"
        return f"{years} year{'s' if years > 1 else ''} and {remainder} month{'s' if remainder > 1 else ''}"

    custom = (getattr(document.document_type, "validity_text", None) or "").strip() if document.document_type else ""
    if custom:
        return custom

    name = ((document.document_type.name if document.document_type else "") or "").lower()
    if "residency" in name or "clearance" in name:
        return "6 months"
    if "id" in name:
        return "1 year"
    return "As stated by barangay policy"


def _compute_expiration(issue_dt: date, months: int | None) -> str:
    if not months:
        return ""
    exp_year = issue_dt.year + (issue_dt.month + months - 1) // 12
    exp_month = (issue_dt.month + months - 1) % 12 + 1
    exp_day = min(issue_dt.day, [31, 29 if exp_year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][exp_month - 1])
    from datetime import date
    try:
        exp = date(exp_year, exp_month, exp_day)
    except ValueError:
        exp = date(exp_year, exp_month, 1)
    return exp.strftime("%B %d, %Y")


def _build_knl_document_id(document, issue_dt: date) -> str:
    doc_num = getattr(document, "id", None)
    suffix = f"{int(doc_num):05d}" if doc_num else "TEMP"
    return f"KNL-{issue_dt.year}-{suffix}"


def _build_qr_image(document, issue_dt: date, knl_document_id: str) -> str:
    import qrcode

    document_id = int(document.id)
    target = document_output_dir() / f"qr_{document_id}.png"
    resident = document.resident
    resident_name = " ".join(p for p in [resident.first_name, resident.middle_name, resident.last_name] if p) if resident else "Unknown resident"
    message = (
        "This is to certify that this barangay document is authentic. "
        f"Document ID: {knl_document_id}. "
        f"Resident: {resident_name}. "
        f"Document Type: {document.document_type.name if document.document_type else 'Document'}. "
        f"Issue Date: {issue_dt.strftime('%B %d, %Y')}."
    )
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=0,
    )
    qr.add_data(message)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img.save(target)
    return str(target)


def _custom_field_values(document) -> dict[str, str]:
    try:
        parsed = json.loads(getattr(document, "field_values", None) or "{}")
    except Exception:
        current_app.logger.exception("Failed to load placeholder_config JSON")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value or "") for key, value in parsed.items()}


def _field_config_names(document_type) -> set[str]:
    try:
        parsed = json.loads(getattr(document_type, "field_config", None) or "{}")
    except Exception:
        current_app.logger.exception("Failed to load placeholder_config JSON in candidate check")
        return set()
    fields = parsed.get("fields") if isinstance(parsed, dict) else []
    if not isinstance(fields, list):
        return set()
    return {str(field.get("name", "")).strip() for field in fields if isinstance(field, dict) and str(field.get("name", "")).strip()}


def _build_resident_photo_for_document(photo_abs: str, document_id: int) -> str:
    from PIL import Image

    target = document_output_dir() / f"resident_photo_doc_{document_id}.png"
    white_bg_source = document_output_dir() / f"resident_photo_white_{document_id}.png"
    try:
        if current_app.config.get("ENABLE_ID_PHOTO_PIPELINE", True):
            composite_transparent_on_white(photo_abs, str(white_bg_source))
        else:
            remove_background_to_white(photo_abs, str(white_bg_source))
        source_path = white_bg_source
    except Exception:
        current_app.logger.exception("Resident photo background processing failed; using existing image.")
        source_path = Path(photo_abs)

    img = Image.open(source_path)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    img.save(target, dpi=(300, 300))
    return str(target)


def _build_resident_signature_for_document(tpl: DocxTemplate, resident) -> str | InlineImage:
    sig_abs = _relative_static_to_abs(getattr(resident, "signature_path", None))
    if sig_abs and Path(sig_abs).exists():
        return InlineImage(tpl, sig_abs, width=Mm(35))
    return ""


def _build_context(document, tpl: DocxTemplate) -> dict:
    resident = document.resident
    issue_dt = document.issue_date.date() if hasattr(document.issue_date, "date") else document.issue_date
    if not issue_dt:
        issue_dt = date.today()

    resident_name = format_resident_name(resident.first_name, resident.middle_name, resident.last_name)
    photo_abs = _relative_static_to_abs(getattr(resident, "photo_path", None))
    knl_document_id = _build_knl_document_id(document, issue_dt)
    qr_abs = _build_qr_image(document, issue_dt, knl_document_id)

    if not photo_abs:
        raise DocumentGenerationError("Resident photo is missing. Capture photo before issuing.")

    resident_photo_for_docx = _build_resident_photo_for_document(photo_abs, document.id)
    birth_date = resident.birth_date.strftime("%B %d, %Y") if getattr(resident, "birth_date", None) else ""
    author = _resolve_author_name(document)
    year_on_barangay = _compute_year_on_barangay(document, issue_dt)
    validity = _compute_validity(document, issue_dt)
    expiration_date = _compute_expiration(issue_dt, getattr(document.document_type, "validity_months", None) if document.document_type else None)

    context = {
        "resident_name": resident_name,
        "first_name": resident.first_name or "",
        "middle_name": resident.middle_name or "",
        "last_name": resident.last_name or "",
        "address": resident.full_address or "",
        "purpose": (document.details or "").strip(),
        "issue_date": issue_dt.strftime("%B %d, %Y"),
        "resident_id": resident.barangay_id or "",
        "document_id": knl_document_id,
        "document_type": document.document_type.name if document.document_type else "",
        "captain_name": "",
        "birth_date": birth_date,
        "marital_status": resident.marital_status or "",
        "emergency_contact_name": resident.emergency_contact_name or "",
        "emergency_contact_number": resident.emergency_contact_number or "",
        "emergency_contact_relationship": resident.emergency_contact_relationship or "",
        "emergency_contact_address": resident.emergency_contact_address or "",
        "author": author,
        "year_on_barangay": year_on_barangay,
        "validity": validity,
        "expiration_date": expiration_date,
        "resident_photo": InlineImage(tpl, resident_photo_for_docx),
        "captain_signature": "",
        "resident_signature": _build_resident_signature_for_document(tpl, resident),
        "qr_code": InlineImage(tpl, qr_abs, width=Inches(1), height=Inches(1)),
    }
    context.update(_custom_field_values(document))
    return apply_document_formatting(context)


def _convert_docx_to_pdf(docx_path: Path) -> Path | None:
    """Convert DOCX to PDF via LibreOffice. Returns None if LibreOffice is unavailable."""
    libreoffice_bin = current_app.config.get("LIBREOFFICE_BIN", "soffice")
    timeout = int(current_app.config.get("LIBREOFFICE_TIMEOUT_SECONDS", 90))
    out_dir = docx_path.parent
    cmd = [
        libreoffice_bin,
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(docx_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        current_app.logger.warning("LibreOffice not found — PDF conversion skipped.")
        return None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        current_app.logger.exception("LibreOffice PDF conversion failed — skipping PDF.")
        return None

    pdf_path = out_dir / f"{docx_path.stem}.pdf"
    return pdf_path if pdf_path.exists() else None


def _normalize_photo_formatting(docx_path: Path) -> None:
    """Normalize table cell margins and paragraph spacing around photos in rendered DOCX."""
    doc = WordDocument(str(docx_path))

    def _set_zero_cell_margins(cell) -> None:
        tc = cell._tc
        tc_pr = tc.get_or_add_tcPr()
        tc_mar = tc_pr.find(qn("w:tcMar"))
        if tc_mar is None:
            tc_mar = OxmlElement("w:tcMar")
            tc_pr.append(tc_mar)
        for side in ("top", "start", "bottom", "end"):
            node = tc_mar.find(qn(f"w:{side}"))
            if node is None:
                node = OxmlElement(f"w:{side}")
                tc_mar.append(node)
            node.set(qn("w:w"), "0")
            node.set(qn("w:type"), "dxa")

        # Legacy Word margin keys still used by some templates.
        for side in ("left", "right"):
            node = tc_mar.find(qn(f"w:{side}"))
            if node is None:
                node = OxmlElement(f"w:{side}")
                tc_mar.append(node)
            node.set(qn("w:w"), "0")
            node.set(qn("w:type"), "dxa")

    def _set_zero_table_margins(table) -> None:
        tbl = table._tbl
        tbl_pr = tbl.tblPr
        if tbl_pr is None:
            tbl_pr = OxmlElement("w:tblPr")
            tbl.insert(0, tbl_pr)
        tbl_cell_mar = tbl_pr.find(qn("w:tblCellMar"))
        if tbl_cell_mar is None:
            tbl_cell_mar = OxmlElement("w:tblCellMar")
            tbl_pr.append(tbl_cell_mar)
        for side in ("top", "start", "bottom", "end", "left", "right"):
            node = tbl_cell_mar.find(qn(f"w:{side}"))
            if node is None:
                node = OxmlElement(f"w:{side}")
                tbl_cell_mar.append(node)
            node.set(qn("w:w"), "0")
            node.set(qn("w:type"), "dxa")

    # Remove paragraph/cell spacing inside tables to prevent left strip/margin.
    for table in doc.tables:
        _set_zero_table_margins(table)
        for row in table.rows:
            for cell in row.cells:
                _set_zero_cell_margins(cell)
                for para in cell.paragraphs:
                    pf = para.paragraph_format
                    pf.space_before = 0
                    pf.space_after = 0
                    pf.left_indent = 0
                    pf.right_indent = 0
                    pf.first_line_indent = 0
                    para.alignment = 0

    # Trim paragraph spacing to reduce visual offset around images.
    for para in doc.paragraphs:
        pf = para.paragraph_format
        pf.space_before = 0
        pf.space_after = 0

    WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
    A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

    def _get_container_width(drawing_elem) -> int | None:
        r_elem = drawing_elem.getparent()
        if r_elem is None:
            return None
        p_elem = r_elem.getparent()
        if p_elem is None:
            return None

        # 1. Table cell container
        tc_elem = p_elem.getparent()
        if tc_elem is not None and tc_elem.tag == qn("w:tc"):
            tc_pr = tc_elem.find(qn("w:tcPr"))
            if tc_pr is not None:
                tc_w = tc_pr.find(qn("w:tcW"))
                if tc_w is not None:
                    w_val = tc_w.get(qn("w:w"))
                    w_type = tc_w.get(qn("w:type"))
                    if w_val and w_type == "dxa":
                        return int(w_val) * 635

        # 2. Text box container — walk up from paragraph to find shape with dimensions
        ancestor = p_elem.getparent()
        for _ in range(20):
            if ancestor is None:
                break
            sp_pr = ancestor.find("{%s}spPr" % WPS_NS)
            if sp_pr is not None:
                xfrm = sp_pr.find("{%s}xfrm" % A_NS)
                if xfrm is not None:
                    ext = xfrm.find("{%s}ext" % A_NS)
                    if ext is not None and ext.get("cx"):
                        return int(ext.get("cx"))
            ancestor = ancestor.getparent()

        return None

    # Resize shapes inside containers to match container width.
    for shape in doc.inline_shapes:
        container_width = _get_container_width(shape._inline)
        if container_width is None or container_width <= 0:
            continue

        old_cx = int(shape.width)
        old_cy = int(shape.height)
        if old_cx <= 0:
            continue

        ratio = container_width / old_cx
        shape.width = container_width
        shape.height = int(old_cy * ratio)

    doc.save(str(docx_path))


def render_document_files(document) -> tuple[str, str]:
    dt = document.document_type
    if not dt or not dt.template_path or not dt.template_active:
        raise DocumentGenerationError("No active DOCX template configured for this document type.")

    template_abs_path = resolve_stored_path_to_abs(dt.template_path)
    if not template_abs_path:
        raise DocumentGenerationError("Configured DOCX template file is missing on disk.")

    required_placeholders = set(REQUIRED_PLACEHOLDERS)
    if getattr(dt, "placeholder_config", None):
        try:
            parsed = json.loads(dt.placeholder_config)
            custom_required = parsed.get("required") if isinstance(parsed, dict) else None
            if isinstance(custom_required, list):
                normalized = {str(x).strip() for x in custom_required if str(x).strip()}
                if normalized:
                    required_placeholders = normalized
        except Exception:
            current_app.logger.exception("Failed to process placeholder rules")
            pass

    validation = validate_template_with_required(
        str(template_abs_path),
        required_placeholders,
        extra_allowed=_field_config_names(dt),
    )
    if validation["missing"]:
        raise DocumentGenerationError(
            "Template is missing required placeholders: " + ", ".join(validation["missing"])
        )

    output_dir = document_output_dir() / str(document.document_type_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    docx_abs = output_dir / f"document_{document.id}.docx"

    tpl = DocxTemplate(str(template_abs_path))
    context = _build_context(document, tpl)
    try:
        tpl.render(context)
    except TemplateSyntaxError as exc:
        raise DocumentGenerationError(
            "Template syntax error in DOCX placeholders. "
            "Use placeholders like {{ resident_name }} only, and remove invalid tags. "
            f"Details: {exc}"
        ) from exc
    tpl.save(docx_abs)
    _normalize_photo_formatting(docx_abs)

    pdf_abs = _convert_docx_to_pdf(docx_abs)

    pdf_rel = ""
    if pdf_abs:
        pdf_rel = str(pdf_abs.relative_to(document_output_dir()).as_posix())

    docx_rel = str(docx_abs.relative_to(document_output_dir()).as_posix())
    return docx_rel, pdf_rel


def preview_document_type_template(doc_type) -> tuple[bytes, str]:
    """Render a document-type template with dummy data and return (docx_bytes, filename).

    The caller is responsible for passing a ``DocumentType`` instance that has
    an active, valid template file on disk.
    """
    if not doc_type or not doc_type.template_path or not doc_type.template_active:
        raise DocumentGenerationError("No active template configured for this document type.")

    template_abs = resolve_stored_path_to_abs(doc_type.template_path)
    if not template_abs:
        raise DocumentGenerationError("Template file is missing on disk.")

    required_placeholders = set(REQUIRED_PLACEHOLDERS)
    if getattr(doc_type, "placeholder_config", None):
        try:
            parsed = json.loads(doc_type.placeholder_config)
            custom_required = parsed.get("required") if isinstance(parsed, dict) else None
            if isinstance(custom_required, list):
                normalized = {str(x).strip() for x in custom_required if str(x).strip()}
                if normalized:
                    required_placeholders = normalized
        except Exception:
            current_app.logger.exception("Failed to parse placeholder_config for preview")
            pass

    field_names = _field_config_names(doc_type)
    validation = validate_template_with_required(str(template_abs), required_placeholders, extra_allowed=field_names)
    if validation["missing"]:
        raise DocumentGenerationError(
            "Template is missing required placeholders: " + ", ".join(validation["missing"])
        )

    tpl = DocxTemplate(str(template_abs))

    # Build dummy context ----------------------------------------------------
    dummy_context = {
        "resident_name": "Juan B. Dela Cruz",
        "first_name": "Juan",
        "middle_name": "B.",
        "last_name": "Dela Cruz",
        "address": "123 Rizal St., Barangay Poblacion, Sample City",
        "purpose": "Sample Document Purpose",
        "issue_date": "January 15, 2025",
        "resident_id": "KNL-2025-00001",
        "document_id": "KNL-2025-00001-001",
        "document_type": doc_type.name or "Sample Document Type",
        "captain_name": "Capt. Juan A. Santos",
        "birth_date": "March 20, 1990",
        "marital_status": "Married",
        "emergency_contact_name": "Maria C. Dela Cruz",
        "emergency_contact_number": "0917 123 4567",
        "emergency_contact_relationship": "Spouse",
        "emergency_contact_address": "123 Rizal St., Barangay Poblacion, Sample City",
        "author": "Admin User",
        "year_on_barangay": "5 years",
        "validity": "Valid for 6 months",
        "expiration_date": "July 15, 2025",
        "resident_signature": "",
    }

    # Generate placeholder images for resident_photo and qr_code ------------
    with tempfile.TemporaryDirectory() as tmpdir:
        placeholder_img = _make_placeholder_image(tmpdir, "PHOTO", (240, 280))
        qr_img = _make_placeholder_image(tmpdir, "QR", (200, 200))
        dummy_context["resident_photo"] = InlineImage(tpl, placeholder_img, width=Mm(25), height=Mm(30))
        dummy_context["qr_code"] = InlineImage(tpl, qr_img, width=Inches(1), height=Inches(1))

        # Custom fields from field_config
        if getattr(doc_type, "field_config", None):
            try:
                fc = json.loads(doc_type.field_config)
                fields = fc.get("fields", []) if isinstance(fc, dict) else []
                n = 1
                for f in fields:
                    key = f.get("name", f"field_{n}")
                    dummy_context[key] = f"[{f.get('label', key)}]"
                    n += 1
            except Exception:
                current_app.logger.exception("Failed to parse field_config for preview")
                pass

        docx_bytes = io.BytesIO()
        try:
            tpl.render(dummy_context)
        except TemplateSyntaxError as exc:
            raise DocumentGenerationError(
                "Template syntax error. Use placeholders like {{ resident_name }} only. "
                f"Details: {exc}"
            ) from exc
        tpl.save(docx_bytes)
        docx_bytes.seek(0)
        slug = re.sub(r"[^a-z0-9]+", "-", (doc_type.name or "preview").lower()).strip("-")
        return docx_bytes.getvalue(), f"{slug}-preview.docx"


def _make_placeholder_image(directory: str, text: str, size: tuple[int, int]) -> str:
    """Create a small gray placeholder PNG with centered text."""
    path = os.path.join(directory, f"placeholder_{text.lower()}.png")
    img = Image.new("RGB", size, (200, 200, 200))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size[0] - tw) / 2, (size[1] - th) / 2), text, fill=(120, 120, 120))
    img.save(path)
    return path


def libreoffice_diagnostics() -> tuple[bool, str]:
    libreoffice_bin = current_app.config.get("LIBREOFFICE_BIN", "soffice")
    try:
        out = subprocess.run([libreoffice_bin, "--version"], capture_output=True, text=True, timeout=15, check=True)
        message = (out.stdout or out.stderr or "LibreOffice available").strip()
        return True, message
    except Exception as exc:
        return False, str(exc)

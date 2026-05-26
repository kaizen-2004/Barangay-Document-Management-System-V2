from __future__ import annotations

import os
import re
import subprocess
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

from .extensions import db
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
    "captain_signature",
    "year_on_barangay",
    "birth_date",
    "validity",
    "author",
    "marital_status",
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
        user = None
    return getattr(user, "username", None) or f"User {user_id}"


def _compute_year_on_barangay(document, issue_dt: date) -> str:
    resident = document.resident
    base = getattr(resident, "created_at", None)
    if hasattr(base, "date"):
        base = base.date()
    if not base:
        return "N/A"
    years = max(0, issue_dt.year - base.year - ((issue_dt.month, issue_dt.day) < (base.month, base.day)))
    return str(years)


def _compute_validity(document, issue_dt: date) -> str:
    custom = (getattr(document.document_type, "validity_text", None) or "").strip() if document.document_type else ""
    if custom:
        return custom

    name = ((document.document_type.name if document.document_type else "") or "").lower()
    if "residency" in name or "residency" in name:
        return "6 months"
    if "clearance" in name:
        return "6 months"
    if "id" in name:
        return "1 year"
    return "As stated by barangay policy"


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
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value or "") for key, value in parsed.items()}


def _field_config_names(document_type) -> set[str]:
    try:
        parsed = json.loads(getattr(document_type, "field_config", None) or "{}")
    except Exception:
        return set()
    fields = parsed.get("fields") if isinstance(parsed, dict) else []
    if not isinstance(fields, list):
        return set()
    return {str(field.get("name", "")).strip() for field in fields if isinstance(field, dict) and str(field.get("name", "")).strip()}


def _build_resident_photo_2x2(photo_abs: str, document_id: int) -> str:
    from PIL import Image

    target = document_output_dir() / f"resident_photo_2x2_{document_id}.png"
    img = Image.open(photo_abs)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    square = img.crop((left, top, left + side, top + side)).resize((700, 700))
    square.save(target)
    return str(target)


def _build_context(document, tpl: DocxTemplate) -> dict:
    resident = document.resident
    issue_dt = document.issue_date.date() if hasattr(document.issue_date, "date") else document.issue_date
    if not issue_dt:
        issue_dt = date.today()

    resident_name = " ".join([p for p in [resident.first_name, resident.middle_name, resident.last_name] if p])
    photo_abs = _relative_static_to_abs(getattr(resident, "photo_path", None))
    knl_document_id = _build_knl_document_id(document, issue_dt)
    qr_abs = _build_qr_image(document, issue_dt, knl_document_id)

    if not photo_abs:
        raise DocumentGenerationError("Resident photo is missing. Capture photo before issuing.")

    resident_photo_for_docx = _build_resident_photo_2x2(photo_abs, document.id)
    birth_date = resident.birth_date.strftime("%B %d, %Y") if getattr(resident, "birth_date", None) else ""
    author = _resolve_author_name(document)
    year_on_barangay = _compute_year_on_barangay(document, issue_dt)
    validity = _compute_validity(document, issue_dt)

    context = {
        "resident_name": resident_name,
        "address": resident.address or "",
        "purpose": (document.details or "").strip(),
        "issue_date": issue_dt.strftime("%B %d, %Y"),
        "resident_id": resident.barangay_id or "",
        "document_id": knl_document_id,
        "document_type": document.document_type.name if document.document_type else "",
        "captain_name": "",
        "birth_date": birth_date,
        "marital_status": resident.marital_status or "",
        "author": author,
        "year_on_barangay": year_on_barangay,
        "validity": validity,
        "resident_photo": InlineImage(tpl, resident_photo_for_docx, width=Mm(50.8), height=Mm(50.8)),
        "captain_signature": "",
        "qr_code": InlineImage(tpl, qr_abs, width=Inches(1), height=Inches(1)),
    }
    context.update(_custom_field_values(document))
    return context


def _convert_docx_to_pdf(docx_path: Path) -> Path:
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
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise DocumentGenerationError(f"PDF conversion failed: {exc}") from exc

    pdf_path = out_dir / f"{docx_path.stem}.pdf"
    if not pdf_path.exists():
        raise DocumentGenerationError("PDF conversion did not produce a file.")
    return pdf_path


def _enforce_exact_photo_box(docx_path: Path) -> None:
    """Force resident photo shape to exact 2x2 inches in rendered DOCX."""
    doc = WordDocument(str(docx_path))
    target = Inches(2)

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

    # Resident photo is rendered at ~50.8mm width. Normalize matching shapes.
    for shape in doc.inline_shapes:
        width = int(shape.width)
        if 1700000 <= width <= 1950000:
            shape.width = target
            shape.height = target

    # Trim paragraph spacing to reduce visual offset around images.
    for para in doc.paragraphs:
        pf = para.paragraph_format
        pf.space_before = 0
        pf.space_after = 0

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
    _enforce_exact_photo_box(docx_abs)

    pdf_abs = _convert_docx_to_pdf(docx_abs)

    docx_rel = str(docx_abs.relative_to(document_output_dir()).as_posix())
    pdf_rel = str(pdf_abs.relative_to(document_output_dir()).as_posix())
    return docx_rel, pdf_rel


def libreoffice_diagnostics() -> tuple[bool, str]:
    libreoffice_bin = current_app.config.get("LIBREOFFICE_BIN", "soffice")
    try:
        out = subprocess.run([libreoffice_bin, "--version"], capture_output=True, text=True, timeout=15, check=True)
        message = (out.stdout or out.stderr or "LibreOffice available").strip()
        return True, message
    except Exception as exc:
        return False, str(exc)

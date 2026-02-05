"""PDF generation utilities.

This project uses ReportLab for PDF output so it works in development
without external system dependencies.

The PDF layout is *template-per-document-type* (Barangay ID, Clearance,
Business Clearance, Residency, etc.).
"""

from __future__ import annotations

import os
from io import BytesIO
from datetime import date
from calendar import monthrange
from xml.sax.saxutils import escape as xml_escape

from .time_utils import utcnow
from typing import Optional

from flask import current_app
from .extensions import db
from .models import User
from reportlab.lib.pagesizes import LETTER, A4
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.lib.utils import simpleSplit, ImageReader
from reportlab.platypus import Paragraph

from pypdf import PdfReader, PdfWriter


def _safe_filename(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in text).strip("_")


def _resident_display_name(resident) -> str:
    parts = [resident.first_name, resident.middle_name, resident.last_name]
    return " ".join([p for p in parts if p])


def _draw_header(c: canvas.Canvas, title: str) -> None:
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(4.25 * inch, 10.5 * inch, "REPUBLIC OF THE PHILIPPINES")
    c.setFont("Helvetica", 11)
    c.drawCentredString(4.25 * inch, 10.25 * inch, "BARANGAY DOCUMENT MANAGEMENT SYSTEM")
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(4.25 * inch, 9.85 * inch, title)

    # light divider
    c.line(0.75 * inch, 9.6 * inch, 7.75 * inch, 9.6 * inch)


def _resolve_photo_abs_path(photo_path: str) -> Optional[str]:
    """Resolve whatever we stored in Resident.photo_path to an absolute file path.

    In older iterations, we stored paths like:
      - uploads/residents/<file>
      - static/uploads/residents/<file>
      - /static/uploads/residents/<file>
      - absolute paths (rare)

    ReportLab needs an absolute filesystem path.
    """
    if not photo_path:
        return None

    p = str(photo_path).strip()
    if not p:
        return None

    # Absolute path?
    if os.path.isabs(p) and os.path.exists(p):
        return p

    rel = p.lstrip("/")
    # Normalize away a leading "static/" if the DB stored it.
    if rel.startswith("static/"):
        rel = rel[len("static/"):]

    # 1) Resolve relative to Flask static folder.
    static_candidate = os.path.join(current_app.static_folder, rel)
    if os.path.exists(static_candidate):
        return static_candidate

    # 2) Resolve relative to UPLOAD_FOLDER (which defaults to <root>/static/uploads)
    uploads_root = current_app.config.get(
        "UPLOAD_FOLDER",
        os.path.join(current_app.root_path, "static", "uploads"),
    )

    # If rel already starts with uploads/, drop that prefix when joining uploads_root.
    rel2 = rel
    if rel2.startswith("uploads/"):
        rel2 = rel2[len("uploads/"):]
    uploads_candidate = os.path.join(uploads_root, rel2)
    if os.path.exists(uploads_candidate):
        return uploads_candidate

    # 3) As a last resort, try joining app root.
    root_candidate = os.path.join(current_app.root_path, rel)
    if os.path.exists(root_candidate):
        return root_candidate

    return None


def _draw_resident_photo(
    c: canvas.Canvas,
    resident,
    x: float,
    y: float,
    w: float = 1.25 * inch,
    h: float = 1.25 * inch,
    draw_box: bool = True,
    padding: float = 2,
    crop_to_fill: bool = True,
) -> None:
    """Draw resident photo if present. (x, y) is the lower-left corner."""

    photo_rel = getattr(resident, "photo_path", None)
    photo_abs = _resolve_photo_abs_path(photo_rel) if photo_rel else None
    if not photo_abs:
        return

    try:
        if draw_box:
            c.rect(x, y, w, h, stroke=1, fill=0)
        target_w = max(1, w - (padding * 2))
        target_h = max(1, h - (padding * 2))
        if crop_to_fill:
            try:
                from PIL import Image

                img = Image.open(photo_abs)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                src_w, src_h = img.size
                target_ratio = target_w / target_h
                src_ratio = src_w / src_h
                if src_ratio > target_ratio:
                    new_w = int(src_h * target_ratio)
                    left = int((src_w - new_w) / 2)
                    img = img.crop((left, 0, left + new_w, src_h))
                else:
                    new_h = int(src_w / target_ratio)
                    top = int((src_h - new_h) / 2)
                    img = img.crop((0, top, src_w, top + new_h))
                c.drawImage(
                    ImageReader(img),
                    x + padding,
                    y + padding,
                    width=target_w,
                    height=target_h,
                    preserveAspectRatio=False,
                    anchor="c",
                )
                return
            except Exception:
                pass

        c.drawImage(
            photo_abs,
            x + padding,
            y + padding,
            width=target_w,
            height=target_h,
            preserveAspectRatio=True,
            anchor="c",
        )
    except Exception:
        # If an image is unreadable, don't break document generation.
        pass


def _draw_signature_block(c: canvas.Canvas, y: float, left_title: str = "Barangay Captain") -> None:
    c.setFont("Helvetica", 10)
    c.drawString(0.9 * inch, y, "Prepared by:")
    c.line(0.9 * inch, y - 0.25 * inch, 3.25 * inch, y - 0.25 * inch)
    c.drawString(0.9 * inch, y - 0.42 * inch, "Barangay Secretary")

    c.drawString(4.5 * inch, y, "Approved by:")
    c.line(4.5 * inch, y - 0.25 * inch, 7.6 * inch, y - 0.25 * inch)
    c.drawString(4.5 * inch, y - 0.42 * inch, left_title)


def _add_months(value: date, months: int) -> date:
    """Add months to a date, clamping the day to month length."""
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _format_date_long(value: date) -> str:
    return value.strftime("%B %d, %Y")


def _resolve_prepared_by(doc) -> str:
    user_id = getattr(doc, "issued_by_id", None) or getattr(doc, "approved_by_id", None) or getattr(doc, "created_by_id", None)
    if not user_id:
        return "System"
    try:
        user = db.session.get(User, user_id)
    except Exception:
        user = None
    return getattr(user, "username", None) or f"User {user_id}"


def _build_reference_no(doc, issue_dt: date) -> str:
    if getattr(doc, "id", None):
        return f"KNL-{issue_dt.year}-{doc.id:05d}"
    return f"KNL-{issue_dt.year}-TEMP"


def _draw_underlined_text(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    *,
    font_name: str = "Times-Bold",
    font_size: int = 11,
    max_width: float | None = None,
) -> None:
    """Draw bold text with an underline, auto-shrinking to fit if needed."""
    if text is None:
        text = ""
    text = str(text)

    size = font_size
    while max_width is not None and size > 8:
        if c.stringWidth(text, font_name, size) <= max_width:
            break
        size -= 1

    c.setFont(font_name, size)
    c.drawString(x, y, text)
    width = c.stringWidth(text, font_name, size)
    c.setLineWidth(0.8)
    c.line(x, y - 1.5, x + width, y - 1.5)


def _draw_paragraph(
    c: canvas.Canvas,
    text: str,
    *,
    x: float,
    top_y: float,
    max_width: float,
    max_height: float,
    base_style: ParagraphStyle,
) -> float:
    """Draw a wrapped Paragraph within a max box, auto-shrinking font if needed."""
    font_size = base_style.fontSize
    leading = base_style.leading

    while True:
        style = ParagraphStyle(
            name=base_style.name,
            parent=base_style,
            fontName=base_style.fontName,
            fontSize=font_size,
            leading=leading,
            alignment=base_style.alignment,
            leftIndent=base_style.leftIndent,
            rightIndent=base_style.rightIndent,
            firstLineIndent=base_style.firstLineIndent,
            spaceBefore=base_style.spaceBefore,
            spaceAfter=base_style.spaceAfter,
        )
        para = Paragraph(text, style)
        _, height = para.wrap(max_width, max_height)
        if height <= max_height or font_size <= 9:
            para.drawOn(c, x, top_y - height)
            return height
        font_size -= 1
        leading = max(11, font_size + 2)


def _template_pdf_path(filename: str) -> str | None:
    base_dir = os.path.abspath(os.path.join(current_app.root_path, "..", "template_assets", "pdf"))
    abs_path = os.path.join(base_dir, filename)
    return abs_path if os.path.exists(abs_path) else None


def _merge_pdf_template(template_path: str, overlay_pdf: BytesIO, output_path: str) -> None:
    overlay_pdf.seek(0)
    template_reader = PdfReader(template_path)
    overlay_reader = PdfReader(overlay_pdf)

    template_page = template_reader.pages[0]
    template_page.merge_page(overlay_reader.pages[0])

    writer = PdfWriter()
    writer.add_page(template_page)
    with open(output_path, "wb") as f:
        writer.write(f)


def _build_residency_overlay(doc) -> BytesIO:
    resident = doc.resident
    issue_dt = doc.issue_date.date() if hasattr(doc.issue_date, "date") else doc.issue_date
    if not issue_dt:
        issue_dt = date.today()

    name = xml_escape(_resident_display_name(resident))
    birth_date = xml_escape(_format_date_long(resident.birth_date))
    marital = xml_escape(resident.marital_status or "N/A")
    address = xml_escape(resident.address or "N/A")
    purpose = xml_escape((doc.details or "").strip() or "N/A")

    issued_on = _format_date_long(issue_dt)
    valid_until = _format_date_long(_add_months(issue_dt, 6))
    prepared_by = _resolve_prepared_by(doc)
    reference_no = _build_reference_no(doc, issue_dt)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)

    # Layout coordinates (A4). Top area is blank in the new template, so we place clean text here.
    left_margin = 78.0
    max_width = 440.0

    heading_x = left_margin

    signature_line_x = 380
    signature_line_y = 275
    signature_line_width = 125.2

    photo_box_x = 65.72
    photo_box_y = 278.87
    photo_box_w = 107.93
    photo_box_h = 103.2
    thumb_box_x = 205.31

    footer_x = 70.0
    footer_start_y = 200.0

    prep_x = 360.0
    prep_y = 50.0

    body_style = ParagraphStyle(
        name="ResidencyBody",
        fontName="Times-Roman",
        fontSize=12,
        leading=16,
        alignment=TA_JUSTIFY,
        firstLineIndent=18,
    )

    paragraph_1 = (
        "This is to certify that Mr./Ms./Mrs. "
        f"<b><u>{name}</u></b>, born on <b><u>{birth_date}</u></b>, "
        f"<b><u>{marital}</u></b>, is a resident of <b><u>{address}</u></b> since birth."
    )
    paragraph_undersigned = (
        "The undersigned has certified that after a reasonable inquiry, I have verified the authenticity of "
        "barangay residency showing that the applicant has been residing in the barangay for at least six (6) months "
        "prior to the application of this Affidavit of Residency."
    )
    paragraph_2 = (
        "This certification is issued upon the request of the above-named person as a supporting document for "
        f"<b><u>{purpose}</u></b>. "
        "This certify further that she/he has no derogatory record and a person of good moral character."
    )

    paragraph_issue = (
        f"Issued this <b><u>{issued_on}</u></b>, at Barangay Krus Na Ligas, District IV, Quezon City."
    )

    def _measure_height(text: str, style: ParagraphStyle, width: float) -> float:
        para = Paragraph(text, style)
        _, height = para.wrap(width, 1000)
        return height

    paragraph_gap = 18
    heading_gap = 22
    paragraph_heights = [
        _measure_height(paragraph_1, body_style, max_width),
        _measure_height(paragraph_undersigned, body_style, max_width),
        _measure_height(paragraph_2, body_style, max_width),
        _measure_height(paragraph_issue, body_style, max_width),
    ]
    total_paragraph_height = sum(paragraph_heights) + (paragraph_gap * 3)

    # Keep the text block just above the photo/signature area to leave room for a future header.
    bottom_target = photo_box_y + photo_box_h + 60
    heading_y = bottom_target + heading_gap + total_paragraph_height

    # Heading
    c.setFont("Times-Bold", 12)
    c.drawString(heading_x, heading_y, "TO WHOM IT MAY CONCERN:")

    y = heading_y - heading_gap
    height = _draw_paragraph(
        c,
        paragraph_1,
        x=left_margin,
        top_y=y,
        max_width=max_width,
        max_height=160,
        base_style=body_style,
    )
    y -= height + paragraph_gap

    height = _draw_paragraph(
        c,
        paragraph_undersigned,
        x=left_margin,
        top_y=y,
        max_width=max_width,
        max_height=160,
        base_style=body_style,
    )
    y -= height + paragraph_gap

    height = _draw_paragraph(
        c,
        paragraph_2,
        x=left_margin,
        top_y=y,
        max_width=max_width,
        max_height=160,
        base_style=body_style,
    )
    y -= height + paragraph_gap

    _draw_paragraph(
        c,
        paragraph_issue,
        x=left_margin,
        top_y=y,
        max_width=max_width,
        max_height=100,
        base_style=body_style,
    )

    # Normalize signature area (avoid duplicate lines from template)
    c.setFillColorRGB(1, 1, 1)
    c.rect(
        signature_line_x - 4,
        signature_line_y - 22,
        signature_line_width + 8,
        30,
        stroke=0,
        fill=1,
    )
    c.setFillColorRGB(0, 0, 0)
    c.setLineWidth(1.0)
    c.line(signature_line_x, signature_line_y, signature_line_x + signature_line_width, signature_line_y)
    c.setFont("Times-Roman", 12)
    c.drawCentredString(signature_line_x + (signature_line_width / 2), signature_line_y - 12, "Applicant Signature")

    # Photo / thumb labels
    c.setFont("Times-Roman", 12)
    photo_label_y = photo_box_y - 18
    c.drawCentredString(photo_box_x + (photo_box_w / 2), photo_label_y, "APPLICANT PHOTO")
    c.drawCentredString(thumb_box_x + (photo_box_w / 2), photo_label_y, "APPLICANT THUMBMARK")

    # Footer (labels + values)
    c.setFont("Times-Roman", 12)
    issued_at_y = footer_start_y
    issued_on_y = footer_start_y - 16
    valid_until_y = footer_start_y - 32
    c.drawString(footer_x, issued_at_y, "Issued At: Barangay Krus Na Ligas")
    c.drawString(footer_x, issued_on_y, "Issued On:")
    _draw_underlined_text(c, issued_on, footer_x + 70, issued_on_y, font_size=12)
    c.drawString(footer_x, valid_until_y, "Valid Until:")
    _draw_underlined_text(c, valid_until, footer_x + 70, valid_until_y, font_size=12)

    # Prepared by / Reference
    c.setFont("Times-Roman", 12)
    c.drawString(prep_x, prep_y, f"Prepared by: {prepared_by}")
    c.drawString(prep_x, prep_y - 16, f"Reference No: {reference_no}")

    # Photo box (template already has the border)
    _draw_resident_photo(
        c,
        resident,
        x=photo_box_x,
        y=photo_box_y,
        w=photo_box_w,
        h=photo_box_h,
        draw_box=False,
        padding=0,
        crop_to_fill=True,
    )

    c.save()
    buffer.seek(0)
    return buffer


def _generate_residency_pdf(doc, output_path: str) -> None:
    template_path = _template_pdf_path("CERTIFICATE-OF-RESIDENCY.pdf")
    if not template_path:
        # Fallback to basic layout if template is missing.
        c = canvas.Canvas(output_path, pagesize=A4)
        _template_residency(c, doc)
        c.save()
        return

    overlay = _build_residency_overlay(doc)
    _merge_pdf_template(template_path, overlay, output_path)


from reportlab.lib.utils import simpleSplit


def _wrap_text(text: str, font_name: str, font_size: int, max_width: float) -> list[str]:
    """Word-wrap text to a given width using ReportLab font metrics."""
    if not text:
        return [""]
    return simpleSplit(str(text), font_name, font_size, max_width)


def _draw_kv(c: canvas.Canvas, x: float, y: float, key: str, value: str, *, value_max_width: float | None = None) -> float:
    """Draw a key/value line with basic wrapping for long values."""
    key_font = "Helvetica-Bold"
    val_font = "Helvetica"
    font_size = 10

    c.setFont(key_font, font_size)
    c.drawString(x, y, f"{key}:")

    value_x = x + 1.6 * inch
    if value_max_width is None:
        # 7.6in is the right margin used elsewhere in this file
        value_max_width = (7.6 * inch) - value_x

    c.setFont(val_font, font_size)
    lines = simpleSplit(str(value or "-"), val_font, font_size, value_max_width)
    if not lines:
        lines = ["-"]

    # First line on same row; subsequent lines wrap beneath with same indent.
    c.drawString(value_x, y, lines[0])
    y -= 0.28 * inch
    for line in lines[1:]:
        c.drawString(value_x, y, line)
        y -= 0.28 * inch

    return y



def _add_wrapped_lines(text_obj, paragraph: str, *, font_name: str = "Helvetica", font_size: int = 11, max_width: float = 6.8 * inch, prefix: str = "") -> None:
    """Add wrapped lines to a ReportLab text object."""
    if paragraph is None:
        return
    paragraph = str(paragraph)
    # preserve explicit newlines as paragraph breaks
    for raw_line in paragraph.split("\n"):
        wrapped = simpleSplit((prefix + raw_line).rstrip(), font_name, font_size, max_width)
        if not wrapped:
            text_obj.textLine(prefix.rstrip())
        else:
            for ln in wrapped:
                text_obj.textLine(ln)


def _draw_wrapped_text_paged(
    c: canvas.Canvas,
    *,
    x: float,
    top_y: float,
    bottom_y: float,
    max_width: float,
    font_name: str,
    font_size: int,
    leading: int,
    content: str,
    redraw_page_top,
) -> float:
    """Draw wrapped text and paginate.

    - `content` may contain newlines; we preserve them.
    - When we hit the bottom margin, we flush the text object, create a new
      page, call `redraw_page_top()` (to redraw header/photo/etc.), and
      continue.

    Returns the y position (text cursor) after rendering the last line.
    """

    def new_text(start_y: float):
        t = c.beginText(x, start_y)
        t.setFont(font_name, font_size)
        t.setLeading(leading)
        return t

    y = top_y
    text = new_text(y)

    for raw_line in str(content or "").split("\n"):
        # Keep blank lines as spacing.
        if raw_line.strip() == "":
            if text.getY() <= bottom_y:
                c.drawText(text)
                c.showPage()
                y = redraw_page_top() or top_y
                text = new_text(y)
            text.textLine("")
            continue

        wrapped = simpleSplit(raw_line, font_name, font_size, max_width) or [raw_line]
        for ln in wrapped:
            if text.getY() <= bottom_y:
                c.drawText(text)
                c.showPage()
                y = redraw_page_top() or top_y
                text = new_text(y)
            text.textLine(ln)

    c.drawText(text)
    return text.getY()
def _template_barangay_id(c: canvas.Canvas, doc) -> None:
    resident = doc.resident
    _draw_header(c, "BARANGAY IDENTIFICATION CARD")

    # Photo at top-right
    _draw_resident_photo(c, resident, x=6.2 * inch, y=8.0 * inch, w=1.3 * inch, h=1.3 * inch, crop_to_fill=True)

    y = 9.25 * inch
    c.setFont("Helvetica", 11)
    c.drawString(0.9 * inch, y, "This certifies that the person below is a registered resident of the barangay.")

    y -= 0.45 * inch
    y = _draw_kv(c, 0.9 * inch, y, "Full Name", _resident_display_name(resident))
    y = _draw_kv(c, 0.9 * inch, y, "Barangay ID", resident.barangay_id or "-")
    y = _draw_kv(c, 0.9 * inch, y, "Address", resident.address or "-")
    # Model field is `birth_date` (some earlier iterations used `birthdate`).
    birth_dt = getattr(resident, "birth_date", None) or getattr(resident, "birthdate", None)
    y = _draw_kv(
        c,
        0.9 * inch,
        y,
        "Birth Date",
        birth_dt.strftime("%B %d, %Y") if birth_dt else "-",
    )
    y = _draw_kv(c, 0.9 * inch, y, "Gender", resident.gender or "-")

    y -= 0.2 * inch
    c.setFont("Helvetica", 10)
    c.drawString(0.9 * inch, y, f"Issued on: {doc.issue_date.strftime('%B %d, %Y')}")


def _template_clearance(c: canvas.Canvas, doc, title: str, body: str) -> None:
    resident = doc.resident

    def page_top() -> float:
        _draw_header(c, title)
        _draw_resident_photo(c, resident, x=6.2 * inch, y=8.0 * inch, w=1.3 * inch, h=1.3 * inch, crop_to_fill=True)
        y0 = 9.2 * inch
        c.setFont("Helvetica", 11)
        c.drawString(0.9 * inch, y0, "TO WHOM IT MAY CONCERN:")
        return y0 - 0.5 * inch

    top_y = page_top()

    name = _resident_display_name(resident)
    addr = resident.address or "this barangay"
    statement = body.format(name=name, address=addr)

    content = statement
    if doc.details:
        bullets = "\n".join([f"- {ln}" for ln in str(doc.details).split("\n") if ln.strip()])
        if bullets:
            content += "\n\nAdditional details:\n" + bullets

    _draw_wrapped_text_paged(
        c,
        x=0.9 * inch,
        top_y=top_y,
        bottom_y=2.8 * inch,  # reserve space for issued date + signatures
        max_width=6.8 * inch,
        font_name="Helvetica",
        font_size=11,
        leading=14,
        content=content,
        redraw_page_top=page_top,
    )

    c.setFont("Helvetica", 10)
    c.drawString(0.9 * inch, 2.2 * inch, f"Issued on: {doc.issue_date.strftime('%B %d, %Y')}")
    _draw_signature_block(c, y=1.6 * inch)


def _template_barangay_clearance(c: canvas.Canvas, doc) -> None:
    body = (
        "This is to certify that {name}, a resident of {address}, is of good moral character and has no derogatory record "
        "in this barangay as of the date of issuance.\n\n"
        "This clearance is issued upon request for whatever legal purpose it may serve."
    )
    _template_clearance(c, doc, "BARANGAY CLEARANCE", body)


def _template_business_clearance(c: canvas.Canvas, doc) -> None:
    body = (
        "This is to certify that {name} is hereby granted BARANGAY BUSINESS CLEARANCE to operate a business within "
        "the jurisdiction of this barangay, subject to existing rules and regulations.\n\n"
        "Please ensure compliance with local ordinances and other regulatory requirements."
    )
    _template_clearance(c, doc, "BUSINESS CLEARANCE", body)


def _template_residency(c: canvas.Canvas, doc) -> None:
    body = (
        "This is to certify that {name} is a bona fide resident of {address}.\n\n"
        "This certification is issued upon request for whatever purpose it may serve."
    )
    _template_clearance(c, doc, "CERTIFICATE OF RESIDENCY", body)


def _template_generic(c: canvas.Canvas, doc) -> None:
    resident = doc.resident
    title = (doc.document_type.name if doc.document_type else "DOCUMENT").upper()

    def page_top() -> float:
        _draw_header(c, title)
        _draw_resident_photo(c, resident, x=6.2 * inch, y=8.0 * inch, w=1.3 * inch, h=1.3 * inch, crop_to_fill=True)

        y0 = 9.25 * inch
        c.setFont("Helvetica", 11)
        c.drawString(0.9 * inch, y0, "Document Details")

        y0 -= 0.45 * inch
        y0 = _draw_kv(c, 0.9 * inch, y0, "Resident", _resident_display_name(resident))
        y0 = _draw_kv(c, 0.9 * inch, y0, "Issued", doc.issue_date.strftime("%B %d, %Y"))
        return y0 - 0.2 * inch

    top_y = page_top()
    if doc.details:
        _draw_wrapped_text_paged(
            c,
            x=0.9 * inch,
            top_y=top_y,
            bottom_y=2.8 * inch,
            max_width=6.8 * inch,
            font_name="Helvetica",
            font_size=10,
            leading=13,
            content=str(doc.details),
            redraw_page_top=page_top,
        )

    _draw_signature_block(c, y=1.6 * inch)


def generate_document_pdf(doc) -> str:
    """Generate a PDF for a Document row and return the *relative* file path.

    The app serves files from ``/static`` and the PDF download route resolves
    ``doc.file_path`` relative to that folder. So we store generated PDFs under
    ``static/uploads/documents/<doc_type>`` and return a path like
    ``uploads/documents/<doc_type>/<filename>.pdf``.
    """

    doc_type_name = (doc.document_type.name if doc.document_type else "document")
    folder = _safe_filename(doc_type_name.lower()) or "document"

    # Store under static/uploads so the app can serve it back with send_from_directory.
    uploads_root = current_app.config.get(
        "UPLOAD_FOLDER",
        os.path.join(current_app.root_path, "static", "uploads"),
    )
    root_upload_dir = os.path.join(uploads_root, "documents", folder)
    os.makedirs(root_upload_dir, exist_ok=True)

    ts = utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{folder}_resident_{doc.resident_id}_{ts}.pdf"
    abs_path = os.path.join(root_upload_dir, filename)

    template_key = ""
    if doc.document_type and doc.document_type.template_path:
        template_key = str(doc.document_type.template_path).strip().lower()

    name = doc_type_name.lower()
    if template_key == "residency" or "residency" in name:
        _generate_residency_pdf(doc, abs_path)
        rel_path = os.path.join("uploads", "documents", folder, filename)
        return rel_path

    c = canvas.Canvas(abs_path, pagesize=LETTER)

    template_map = {
        "barangay_id": _template_barangay_id,
        "barangay_clearance": _template_barangay_clearance,
        "business_clearance": _template_business_clearance,
        "residency": _template_residency,
        "generic": _template_generic,
    }

    if template_key in template_map:
        template_map[template_key](c, doc)
    else:
        if "barangay id" in name or name.strip() == "barangay id" or "identification" in name:
            _template_barangay_id(c, doc)
        elif "business" in name and "clearance" in name:
            _template_business_clearance(c, doc)
        elif "clearance" in name:
            _template_barangay_clearance(c, doc)
        elif "residency" in name:
            _template_residency(c, doc)
        else:
            _template_generic(c, doc)

    # Do not call showPage() here; templates may paginate as needed and
    # reportlab will finalize the current page on save().
    c.save()

    # Path relative to <app_root>/static
    rel_path = os.path.join("uploads", "documents", folder, filename)
    return rel_path

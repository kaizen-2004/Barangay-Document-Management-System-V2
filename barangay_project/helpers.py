"""
Utility functions for the Barangay Document Management System.

This module defines helper functions that are used across multiple
blueprints, such as logging actions to the transaction log.  By
centralizing these helpers here, we avoid circular imports and keep
route modules focused on view logic.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import uuid
from functools import wraps

from flask import abort, current_app, has_request_context, request
from flask_login import current_user
from werkzeug.utils import secure_filename

from .extensions import db
from .image_processing import (
    composite_transparent_on_white,
    generate_transparent_png,
    process_captured_person_photo,
    remove_background_to_white,
)
from .models import TransactionLog


def roles_required(*roles: str):
    """Require the current user to have one of the given roles.

    Usage:
        @login_required
        @roles_required("admin")
        def view(...):
            ...
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            # If the user is not logged in, Flask-Login will handle it via @login_required.
            if not current_user.is_authenticated:
                abort(401)
            if roles and getattr(current_user, "role", None) not in roles:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def get_client_ip() -> str | None:
    """Best-effort client IP for rate limiting and audit logs."""
    if not has_request_context():
        return None
    try:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or None
    except Exception:
        current_app.logger.exception("Failed to get client IP from X-Forwarded-For")
        return None
    try:
        return request.remote_addr
    except Exception:
        current_app.logger.exception("Failed to get client IP from request.remote_addr")
        return None


def log_action(
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    meta: dict | None = None,
) -> None:
    """Record an action in the transaction log.

    This function creates a new `TransactionLog` entry associated
    with the currently authenticated user.  If no user is
    authenticated, the action is not logged.

    Args:
        action: A description of the action performed.
    """
    # Ensure that we only log actions for authenticated users
    if current_user.is_authenticated:
        ip = get_client_ip()
        ua = None
        if has_request_context():
            try:
                ua = (request.user_agent.string or "")[:255]
            except Exception:
                current_app.logger.exception("Failed to parse User-Agent")
                ua = None

        log = TransactionLog(
            user_id=current_user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=ip,
            user_agent=ua,
            meta=meta,
        )
        db.session.add(log)
        db.session.commit()


ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}
CAPTURE_PHOTO_PREFIXES = ("uploads/photos/original/", "uploads/photos/processed/")


def _save_processed_white_background(abs_path: str, upload_root: str, subfolder: str, unique_stem: str) -> str:
    processed_dir = os.path.join(upload_root, "processed", subfolder)
    processed_name = f"{unique_stem}.png"
    processed_abs = os.path.join(processed_dir, processed_name)

    if current_app.config.get("ENABLE_ID_PHOTO_PIPELINE", True):
        generate_transparent_png(abs_path, processed_abs, preset="id_photo")
    else:
        remove_background_to_white(abs_path, processed_abs)
    return f"uploads/processed/{subfolder}/{processed_name}"


def is_processed_photo_path(value: str | None) -> bool:
    return bool(value and str(value).startswith("uploads/photos/processed/"))


def is_static_upload_path(value: str | None) -> bool:
    return bool(value and str(value).strip().lstrip("/").startswith("uploads/"))


def promote_capture_photo_to_resident(path: str | None) -> str | None:
    """Copy a temporary processed capture into permanent resident storage."""
    rel = str(path or "").strip().lstrip("/")
    if not rel:
        return None
    if not rel.startswith("uploads/photos/processed/"):
        return rel

    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.static_folder, "uploads")
    )
    static_root = os.path.dirname(upload_root)
    source_abs = os.path.abspath(os.path.join(static_root, rel))
    allowed_root = os.path.abspath(os.path.join(upload_root, "photos", "processed"))
    if not source_abs.startswith(allowed_root + os.sep) or not os.path.isfile(source_abs):
        current_app.logger.warning("promote_capture_photo_to_resident: source file missing: %s", source_abs)
        return None

    target_dir = os.path.join(upload_root, "processed", "residents")
    os.makedirs(target_dir, exist_ok=True)
    target_name = f"{uuid.uuid4().hex}.png"
    target_abs = os.path.join(target_dir, target_name)
    shutil.copy2(source_abs, target_abs)
    return f"uploads/processed/residents/{target_name}"


def save_or_keep_resident_photo(value: str | None) -> str | None:
    """Persist a photo form value, whether it is a data URL or saved static path."""
    if not value:
        return None
    if is_static_upload_path(value):
        return promote_capture_photo_to_resident(value)
    return save_captured_image(value, "residents")


def save_signature_data_url(data_url: str | None, subfolder: str) -> str | None:
    """Save a canvas-captured signature from a data URL and return the relative path.

    Canvas outputs clean PNG data with a transparent background — no
    background removal is needed.  The file is saved under
    ``uploads/original/<subfolder>/<uuid>.png``.
    """
    if not data_url:
        return None

    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        return None

    ext = m.group("ext").lower()
    if ext == "jpeg":
        ext = "jpg"

    try:
        raw = base64.b64decode(m.group("data"), validate=True)
    except Exception:
        current_app.logger.exception("Failed to decode base64 signature data")
        return None

    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.static_folder, "uploads")
    )
    target_dir = os.path.join(upload_root, "original", subfolder)
    os.makedirs(target_dir, exist_ok=True)

    unique_stem = uuid.uuid4().hex
    unique_name = f"{unique_stem}.{ext}"
    abs_path = os.path.join(target_dir, unique_name)

    # Save the full canvas signature as-is (no cropping)
    from PIL import Image
    from io import BytesIO
    try:
        img = Image.open(BytesIO(raw)).convert("RGBA")
        img.save(abs_path, format="PNG")
    except Exception:
        current_app.logger.exception("Failed to process signature; saving raw data.")
        with open(abs_path, "wb") as f:
            f.write(raw)

    return f"uploads/original/{subfolder}/{unique_name}"


def save_or_keep_resident_signature(value: str | None) -> str | None:
    """Persist a resident signature value, whether a data URL or saved static path."""
    if not value:
        return None
    if is_static_upload_path(value):
        return value
    return save_signature_data_url(value, "signatures")


def process_captured_signature(data_url: str, ink_sensitivity: int = 15) -> str | None:
    """Process a camera-captured signature: auto-crop to ink, remove white background.

    Uses adaptive contrast to extract dark ink from white paper regardless
    of lighting conditions. The paper background becomes transparent and the
    ink is preserved as black on transparent.
    """
    import logging
    from io import BytesIO

    from PIL import Image, ImageFilter

    logger = logging.getLogger(__name__)

    if not data_url:
        return None

    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        return None

    try:
        raw = base64.b64decode(m.group("data"), validate=True)
    except Exception:
        logger.exception("Failed to decode base64 signature data")
        return None

    try:
        img = Image.open(BytesIO(raw))
    except Exception:
        logger.exception("Failed to open signature image")
        return None

    # Step 1: Convert to grayscale
    gray = img.convert("L")

    # Step 2: Blur slightly to reduce camera noise
    gray = gray.filter(ImageFilter.GaussianBlur(radius=1))

    # Step 3: Find the paper's brightness (median of the image = likely paper)
    pixels = list(gray.getdata())
    pixels.sort()
    paper_brightness = pixels[len(pixels) // 2]

    # Step 4: Adaptive threshold — anything darker than paper is ink
    # ink_sensitivity: lower = stricter (markers only), higher = more sensitive (ballpoint pens)
    ink_threshold = min(paper_brightness - ink_sensitivity, 200)
    ink_threshold = max(ink_threshold, 60)

    # Step 5: Create ink mask — dark pixels = ink
    ink_mask = gray.point(lambda p: 0 if p < ink_threshold else 255)

    # Step 6: Dilate the mask slightly to capture thin strokes
    ink_mask = ink_mask.filter(ImageFilter.MaxFilter(size=3))

    # Step 7: Find bounding box of ink
    bbox = ink_mask.getbbox()
    if not bbox:
        logger.warning("No ink detected in signature capture, saving as-is")
        final = img.convert("RGBA")
    else:
        # Step 8: Crop with generous padding
        padding = 20
        x1 = max(0, bbox[0] - padding)
        y1 = max(0, bbox[1] - padding)
        x2 = min(img.width, bbox[2] + padding)
        y2 = min(img.height, bbox[3] + padding)

        cropped_gray = gray.crop((x1, y1, x2, y2))
        cropped_mask = ink_mask.crop((x1, y1, x2, y2))

        # Step 9: Create clean output — black ink on transparent background
        w, h = cropped_gray.size
        output = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gray_data = cropped_gray.load()
        mask_data = cropped_mask.load()
        out_data = output.load()

        for y in range(h):
            for x in range(w):
                brightness = gray_data[x, y]
                is_ink = mask_data[x, y] < 128
                if is_ink:
                    # Darker ink = more opaque; lighter ink = semi-transparent
                    alpha = int(max(0, min(255, (paper_brightness - brightness) * 255 / paper_brightness)))
                    alpha = max(alpha, 100)
                    out_data[x, y] = (0, 0, 0, alpha)
                else:
                    out_data[x, y] = (0, 0, 0, 0)

        final = output

    # Save to uploads
    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.static_folder, "uploads")
    )
    target_dir = os.path.join(upload_root, "original", "signatures")
    os.makedirs(target_dir, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}.png"
    abs_path = os.path.join(target_dir, unique_name)
    final.save(abs_path, format="PNG")

    return f"uploads/original/signatures/{unique_name}"


def delete_capture_photo_paths(paths: list[str]) -> int:
    """Delete temporary capture files under uploads/photos only."""
    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.static_folder, "uploads")
    )
    static_root = os.path.dirname(upload_root)
    deleted = 0
    for path in paths:
        rel = str(path or "").strip().lstrip("/")
        if not rel.startswith(CAPTURE_PHOTO_PREFIXES):
            continue
        abs_path = os.path.abspath(os.path.join(static_root, rel))
        allowed_root = os.path.abspath(os.path.join(upload_root, "photos"))
        if not abs_path.startswith(allowed_root + os.sep):
            continue
        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
                deleted += 1
        except Exception:
            current_app.logger.exception("Failed to delete temporary capture image: %s", rel)
    return deleted


def save_camera_capture_for_processing(data_url: str | None, *, level: int = 2, **kwargs) -> dict | None:
    """Save a camera image, process it, and return original/processed static paths."""
    if not data_url:
        return None

    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        return None

    ext = m.group("ext").lower()
    if ext == "jpeg":
        ext = "jpg"

    try:
        raw = base64.b64decode(m.group("data"), validate=True)
    except Exception:
        current_app.logger.exception("Failed to decode base64 image data")
        return None

    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.static_folder, "uploads")
    )
    unique_stem = uuid.uuid4().hex
    original_dir = os.path.join(upload_root, "photos", "original")
    processed_dir = os.path.join(upload_root, "processed", "residents")
    os.makedirs(original_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)

    original_name = f"{unique_stem}.{ext}"
    processed_name = f"{unique_stem}.png"
    original_abs = os.path.join(original_dir, original_name)
    processed_abs = os.path.join(processed_dir, processed_name)

    with open(original_abs, "wb") as f:
        f.write(raw)

    pipe_processed = kwargs.get("pipe_processed", False)
    warning = None
    if pipe_processed:
        shutil.copy2(original_abs, processed_abs)
    else:
        try:
            _, warning = process_captured_person_photo(original_abs, processed_abs, level=level)
        except Exception as exc:
            current_app.logger.exception("Camera photo processing failed; preserving original image.")
            warning = "Photo processing failed. Original captured image was preserved."
            if os.path.isfile(original_abs):
                shutil.copy2(original_abs, processed_abs)

    processed_rel = f"uploads/processed/residents/{processed_name}"
    return {
        "original_path": f"uploads/photos/original/{original_name}",
        "processed_path": processed_rel,
        "warning": warning,
    }


def reprocess_captured_photo(
    data_url: str | None,
    original_rel: str,
    processed_rel: str,
    level: int = 2,
    pipe_processed: bool = False,
) -> dict | None:
    """Re-process an existing camera capture, overwriting the previous result.

    Unlike ``save_camera_capture_for_processing`` which creates new unique
    files each time, this function overwrites the given original and
    processed files in place.  Useful when the user wants to re-run
    background removal on the same capture (e.g. with adjusted crop/zoom)
    without accumulating duplicate images.
    """
    if not data_url or not original_rel or not processed_rel:
        return None

    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        return None

    try:
        raw = base64.b64decode(m.group("data"), validate=True)
    except Exception:
        current_app.logger.exception("Failed to decode base64 image data in reprocess")
        return None

    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.static_folder, "uploads")
    )
    static_root = os.path.dirname(upload_root)
    original_abs = os.path.abspath(os.path.join(static_root, original_rel))
    processed_abs = os.path.abspath(os.path.join(static_root, processed_rel))

    photos_root = os.path.abspath(os.path.join(upload_root, "photos"))
    processed_root = os.path.abspath(os.path.join(upload_root, "processed"))
    if not original_abs.startswith(photos_root + os.sep):
        return None
    if not processed_abs.startswith(photos_root + os.sep) and not processed_abs.startswith(processed_root + os.sep):
        return None

    os.makedirs(os.path.dirname(original_abs), exist_ok=True)
    with open(original_abs, "wb") as f:
        f.write(raw)

    warning = None
    if pipe_processed:
        shutil.copy2(original_abs, processed_abs)
    else:
        try:
            _, warning = process_captured_person_photo(original_abs, processed_abs, level=level)
        except Exception:
            current_app.logger.exception("Camera photo re-processing failed; preserving previous processed image.")
            warning = "Re-processing failed. Previous processed image was preserved."

    return {
        "original_path": original_rel,
        "processed_path": processed_rel,
        "warning": warning,
    }


def save_uploaded_image(file_storage, subfolder: str) -> str | None:
    """Save an uploaded image under static/uploads/<subfolder> and return relative path.

    Returns:
        Relative path (e.g., 'uploads/residents/<file>.jpg') or None if no file.
    """
    if not file_storage or not getattr(file_storage, "filename", ""):
        return None

    filename = secure_filename(file_storage.filename)
    if "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return None

    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.static_folder, "uploads")
    )
    target_dir = os.path.join(upload_root, "original", subfolder)
    os.makedirs(target_dir, exist_ok=True)

    unique_stem = uuid.uuid4().hex
    unique_name = f"{unique_stem}.{ext}"
    abs_path = os.path.join(target_dir, unique_name)
    file_storage.save(abs_path)

    try:
        processed_dir = os.path.join(upload_root, "processed", subfolder)
        processed_name = f"{unique_stem}.png"
        processed_abs = os.path.join(processed_dir, processed_name)
        process_captured_person_photo(abs_path, processed_abs)
        return f"uploads/processed/{subfolder}/{processed_name}"
    except Exception:
        current_app.logger.exception("Image background processing failed; using original image.")
        return f"uploads/original/{subfolder}/{unique_name}"


_DATA_URL_RE = re.compile(r"^data:image/(?P<ext>png|jpeg|jpg);base64,(?P<data>.+)$")


def save_captured_image(data_url: str | None, subfolder: str) -> str | None:
    """Save a webcam-captured image from a Data URL (data:image/...;base64,...).

    Returns a relative path under static/ (e.g., 'uploads/residents/<uuid>.jpg')
    or None if the data_url is empty/invalid.
    """
    if not data_url:
        return None

    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        return None

    ext = m.group("ext").lower()
    if ext == "jpeg":
        ext = "jpg"

    try:
        raw = base64.b64decode(m.group("data"), validate=True)
    except Exception:
        current_app.logger.exception("Failed to decode base64 image data in save_or_keep")
        return None

    upload_root = current_app.config.get(
        "UPLOAD_FOLDER", os.path.join(current_app.static_folder, "uploads")
    )
    target_dir = os.path.join(upload_root, "original", subfolder)
    os.makedirs(target_dir, exist_ok=True)

    unique_stem = uuid.uuid4().hex
    unique_name = f"{unique_stem}.{ext}"
    abs_path = os.path.join(target_dir, unique_name)
    with open(abs_path, "wb") as f:
        f.write(raw)

    try:
        return _save_processed_white_background(abs_path, upload_root, subfolder, unique_stem)
    except Exception:
        current_app.logger.exception("Image background processing failed; using original image.")
        return f"uploads/original/{subfolder}/{unique_name}"

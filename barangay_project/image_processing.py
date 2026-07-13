from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from .id_photo_pipeline import IDPhotoPipeline, PIPELINE_PRESETS, ml_available

logger = logging.getLogger(__name__)

_REMBG_SESSION = None


def bg_removal_available() -> bool:
    try:
        import rembg  # noqa: F401
        return True
    except ImportError:
        return False


def _has_internet(timeout: float = 3.0) -> bool:
    try:
        import socket
        sock = socket.create_connection(("www.remove.bg", 443), timeout=timeout)
        sock.close()
        return True
    except (OSError, socket.timeout):
        return False


def _remove_background_api(source: bytes) -> bytes:
    import urllib.request
    import urllib.error

    api_key = os.environ.get("REMOVE_BG_API_KEY", "")
    if not api_key:
        from flask import current_app
        api_key = current_app.config.get("REMOVE_BG_API_KEY", "")
    if not api_key:
        raise ValueError("No remove.bg API key configured")

    boundary = "----BarangayBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="size"\r\n\r\n'
        f"auto\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image_file"; filename="photo.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + source + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        "https://api.remove.bg/v1.0/removebg",
        data=body,
        headers={
            "X-Api-Key": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _remove_background_local(source: bytes, level: int = 2) -> bytes:
    global _REMBG_SESSION
    import sys
    if getattr(sys, 'frozen', False):
        _capi = os.path.join(sys._MEIPASS, 'onnxruntime', 'capi')
        if os.path.isdir(_capi):
            os.add_dll_directory(_capi)
    try:
        from rembg import new_session, remove
    except ImportError:
        raise ImportError(
            "rembg is required for offline background removal. "
            "Install with: uv sync"
        )

    max_dim = int(os.environ.get("BG_REMOVAL_MAX_DIMENSION", "320"))
    source = _resize_for_speed(source, max_dim)

    if _REMBG_SESSION is None:
        _REMBG_SESSION = new_session("u2net_human_seg")

    use_alpha_matting = os.environ.get("BG_USE_ALPHA_MATTING", "true").lower() in ("1", "true", "yes")
    if use_alpha_matting:
        preset = _REMOVAL_PRESETS.get(level, _REMOVAL_PRESETS[2])
        return remove(
            source,
            session=_REMBG_SESSION,
            alpha_matting=True,
            alpha_matting_foreground_threshold=preset["alpha_matting_foreground_threshold"],
            alpha_matting_background_threshold=preset["alpha_matting_background_threshold"],
            alpha_matting_erode_size=preset["alpha_matting_erode_size"],
            post_process_mask=True,
        )
    return remove(
        source,
        session=_REMBG_SESSION,
        alpha_matting=False,
        post_process_mask=False,
    )


def _remove_background(source: bytes, level: int = 2) -> bytes:
    api_key = os.environ.get("REMOVE_BG_API_KEY", "")
    if not api_key:
        try:
            from flask import current_app
            api_key = current_app.config.get("REMOVE_BG_API_KEY", "")
        except RuntimeError:
            pass

    if api_key and _has_internet():
        try:
            logger.info("Using remove.bg API for background removal")
            return _remove_background_api(source)
        except Exception as exc:
            logger.warning("remove.bg API failed, falling back to local: %s", exc)

    logger.info("Using local rembg for background removal")
    return _remove_background_local(source, level=level)

_REMOVAL_PRESETS = {
    1: {
        "alpha_matting_foreground_threshold": 200,
        "alpha_matting_background_threshold": 40,
        "alpha_matting_erode_size": 0,
    },
    2: {
        "alpha_matting_foreground_threshold": 220,
        "alpha_matting_background_threshold": 25,
        "alpha_matting_erode_size": 1,
    },
    3: {
        "alpha_matting_foreground_threshold": 240,
        "alpha_matting_background_threshold": 15,
        "alpha_matting_erode_size": 2,
    },
}

PRESET_LABELS = {1: "Gentle", 2: "Normal", 3: "Strong"}

_LEVEL_TO_PIPELINE_PRESET = {
    1: "portrait",
    2: "id_photo",
    3: "id_photo",
}


def _resize_for_speed(source: bytes, max_dim: int = 800) -> bytes:
    from PIL import Image, ImageOps
    if max_dim < 1:
        return source
    img = Image.open(BytesIO(source))
    img = ImageOps.exif_transpose(img)
    w, h = img.size
    if max(w, h) <= max_dim:
        return source
    ratio = max_dim / max(w, h)
    img = img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def remove_background_to_white(input_path: str, output_path: str, level: int = 2) -> str:
    from PIL import Image, ImageOps
    input_file = Path(input_path)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    cutout_bytes = _remove_background(input_file.read_bytes(), level=level)
    with Image.open(BytesIO(cutout_bytes)) as cutout:
        rgba = ImageOps.exif_transpose(cutout).convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        white.alpha_composite(rgba)
        white.convert("RGB").save(output_file, format="PNG")

    return str(output_file)


def _white_background_copy(input_path: str, output_path: str) -> str:
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_path) as image:
        rgba = ImageOps.exif_transpose(image).convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        white.alpha_composite(rgba)
        white.convert("RGB").save(output_file, format="PNG")
    return str(output_file)


def save_processed_photo(image, output_path: str, width: int, height: int) -> str:
    from PIL import ImageOps
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    ImageOps.exif_transpose(image).convert("RGB").resize(
        (width, height), Image.Resampling.LANCZOS
    ).save(output_file, format="PNG")
    return str(output_file)


def generate_transparent_png(input_path: str, output_path: str, preset: str = "id_photo") -> str:
    from flask import current_app

    skip_normalize = current_app.config.get("ID_PHOTO_SKIP_NORMALIZE", False)
    pipeline = IDPhotoPipeline(preset=preset, skip_normalize=skip_normalize)
    return pipeline.process_to_file(input_path, output_path)


def composite_transparent_on_white(input_path: str, output_path: str) -> str:
    from PIL import Image, ImageOps
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_path) as image:
        rgba = ImageOps.exif_transpose(image).convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        white.alpha_composite(rgba)
        white.convert("RGB").save(output_file, format="PNG")
    return str(output_file)


def process_captured_person_photo(
    input_path: str, output_path: str, level: int = 2
) -> tuple[str, str | None]:
    from PIL import Image
    from flask import current_app

    width = int(current_app.config.get("PROCESSED_PHOTO_WIDTH", 600))
    height = int(current_app.config.get("PROCESSED_PHOTO_HEIGHT", 600))
    warning = None

    use_pipeline = current_app.config.get("ENABLE_ID_PHOTO_PIPELINE", True)

    if current_app.config.get("ENABLE_BACKGROUND_REMOVAL", True):
        if use_pipeline:
            preset = _LEVEL_TO_PIPELINE_PRESET.get(level, "id_photo")
            try:
                generate_transparent_png(input_path, output_path, preset=preset)
                with Image.open(output_path) as transparent:
                    resized = Image.new("RGBA", (width, height), (255, 255, 255, 255))
                    img_ratio = transparent.width / transparent.height
                    target_ratio = width / height
                    if img_ratio > target_ratio:
                        new_w = width
                        new_h = int(width / img_ratio)
                    else:
                        new_h = height
                        new_w = int(height * img_ratio)
                    resampled = transparent.resize(
                        (new_w, new_h), Image.Resampling.LANCZOS
                    )
                    paste_x = (width - new_w) // 2
                    paste_y = (height - new_h) // 2
                    resized.paste(resampled, (paste_x, paste_y), resampled)
                    resized.convert("RGB").save(output_path, format="PNG")
                return output_path, warning
            except Exception as exc:
                current_app.logger.exception(
                    "ID photo pipeline failed, falling back to legacy removal."
                )
                warning = f"ID photo pipeline skipped: {exc}"

        try:
            remove_background_to_white(input_path, output_path, level=level)
            with Image.open(output_path) as processed:
                save_processed_photo(processed, output_path, width, height)
            return output_path, warning
        except Exception as exc:
            current_app.logger.exception("Background removal failed.")
            warning = f"Background removal skipped: {exc}"

    _white_background_copy(input_path, output_path)
    with Image.open(output_path) as processed:
        save_processed_photo(processed, output_path, width, height)
    return output_path, warning

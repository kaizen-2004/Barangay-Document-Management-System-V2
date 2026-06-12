from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from .id_photo_pipeline import IDPhotoPipeline, PIPELINE_PRESETS

_REMBG_SESSION = None

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


def _remove_background(source: bytes, level: int = 2) -> bytes:
    global _REMBG_SESSION
    import sys
    import os as _os
    if getattr(sys, 'frozen', False):
        _capi = _os.path.join(sys._MEIPASS, 'onnxruntime', 'capi')
        if _os.path.isdir(_capi):
            _os.add_dll_directory(_capi)
    from rembg import new_session, remove

    max_dim = int(_os.environ.get("BG_REMOVAL_MAX_DIMENSION", "320"))
    source = _resize_for_speed(source, max_dim)

    if _REMBG_SESSION is None:
        _REMBG_SESSION = new_session("u2net_human_seg")

    use_alpha_matting = _os.environ.get("BG_USE_ALPHA_MATTING", "true").lower() in ("1", "true", "yes")
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


def remove_background_to_white(input_path: str, output_path: str, level: int = 2) -> str:
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


def save_processed_photo(image: Image.Image, output_path: str, width: int, height: int) -> str:
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

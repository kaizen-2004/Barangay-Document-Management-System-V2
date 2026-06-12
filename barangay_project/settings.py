"""Runtime settings stored in a JSON file.

These settings can be changed from the admin UI without editing .env.
The JSON file is created automatically in the instance folder.
"""
import json
import os

from flask import current_app

_DEFAULTS = {
    "public_url": "",
    "barangay_name": "Krus Na Ligas",
    "system_name": "Barangay Management System",
}

_file_path = None


def _get_path() -> str:
    global _file_path
    if _file_path is None:
        try:
            _file_path = os.path.join(current_app.instance_path, "settings.json")
        except Exception:
            _file_path = os.path.join(os.getcwd(), "data", "settings.json")
    return _file_path


def load_settings() -> dict:
    path = _get_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = dict(_DEFAULTS)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(_DEFAULTS)


def save_settings(settings: dict) -> None:
    path = _get_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def get_setting(key: str, fallback=None) -> str:
    settings = load_settings()
    return settings.get(key, fallback if fallback is not None else _DEFAULTS.get(key, ""))

"""Shared formatting helpers for document generation and display.

All formatting decisions are centralised here so that document templates,
resident detail pages, and autofill APIs produce consistent output.
"""

from __future__ import annotations

import re
from typing import Any

# ── hardcoded barangay context ──────────────────────────────────────

BARANGAY_NAME = "Krus Na Ligas"
BARANGAY_CITY = "Quezon City"


# ── text helpers ────────────────────────────────────────────────────

def uppercase(value: str | None) -> str:
    """Return the string in uppercase, or an empty string for None."""
    return (value or "").upper()


# ── name formatting ─────────────────────────────────────────────────

def format_resident_name(
    first: str | None,
    middle: str | None,
    last: str | None,
) -> str:
    """Philippine ID-standard: LAST, FIRST M."""
    given_parts = []
    if first:
        given_parts.append(str(first).strip().upper())
    if middle:
        m = str(middle).strip().upper()
        given_parts.append(f"{m[0]}." if m else "")
    given = " ".join(given_parts)
    if last:
        return f"{str(last).strip().upper()}, {given}"
    return given


# ── address formatting ──────────────────────────────────────────────

def format_full_address(house_no: str | None, street_name: str | None) -> str:
    """Append barangay and city to the street address."""
    parts = []
    if house_no:
        parts.append(str(house_no).strip())
    if street_name:
        parts.append(str(street_name).strip())
    parts.append(f"Brgy. {BARANGAY_NAME}, {BARANGAY_CITY}")
    return ", ".join(parts)


# ── phone number helpers ────────────────────────────────────────────

def _strip_non_digits(raw: str) -> str:
    return re.sub(r"\D", "", str(raw))


def normalize_phone_for_storage(raw: str | None) -> str | None:
    """Strip all formatting; return only digits or None.

    This is the value that gets stored in the database.
    """
    if not raw:
        return None
    digits = _strip_non_digits(raw)
    return digits if digits else None


def format_ph_mobile(raw: str | None) -> str:
    """Format a Philippine mobile number as 09XX-XXX-XXXX.

    Accepts local (09…) or international (+63…) prefixes.
    Returns the raw input unchanged if it does not look like
    a PH mobile number.
    """
    if not raw:
        return ""
    digits = _strip_non_digits(raw)
    if not digits:
        return str(raw).strip()

    # Normalise +63 prefix → 0
    if digits.startswith("63") and len(digits) >= 11:
        digits = "0" + digits[2:]

    # Must be exactly 11 digits starting with 09
    if len(digits) == 11 and digits.startswith("09"):
        return f"{digits[:4]}-{digits[4:7]}-{digits[7:]}"

    # Not a mobile number — return original input as-is
    return str(raw).strip()


# ── document context formatting ─────────────────────────────────────

def apply_document_formatting(context: dict[str, Any]) -> dict[str, Any]:
    """Post-process the DOCX template context dictionary.

    - All string values → uppercase
    - ``InlineImage`` objects are left untouched
    - Phone-number fields are first formatted, then uppercased
    """
    formatted: dict[str, Any] = {}
    for key, value in context.items():
        try:
            from docxtpl import InlineImage
            if isinstance(value, InlineImage):
                formatted[key] = value
                continue
        except ImportError:
            pass
        if isinstance(value, str):
            if key.endswith("_number") and value:
                formatted[key] = format_ph_mobile(value).upper()
            else:
                formatted[key] = value.upper()
        else:
            formatted[key] = value
    return formatted

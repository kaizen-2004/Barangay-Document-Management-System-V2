"""Email sending via Brevo (Sendinblue) API."""
import json
import logging
import os
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, html: str) -> bool:
    """Send an email via Brevo API. Returns True on success."""
    api_key = os.environ.get("BREVO_API_KEY", "")
    from_email = os.environ.get("BREVO_FROM_EMAIL", "")
    from_name = os.environ.get("BREVO_FROM_NAME", "Barangay System")

    # Try Flask config first
    try:
        from flask import current_app
        api_key = api_key or current_app.config.get("BREVO_API_KEY", "")
        from_email = from_email or current_app.config.get("BREVO_FROM_EMAIL", "")
        from_name = from_name or current_app.config.get("BREVO_FROM_NAME", "Barangay System")
    except RuntimeError:
        pass

    if not api_key or not from_email:
        logger.warning("Brevo API key or from-email not configured. Skipping email.")
        return False

    payload = json.dumps({
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=payload,
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info("Email sent to %s: %s", to, subject)
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("Brevo API error %s: %s", e.code, body)
        return False
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False

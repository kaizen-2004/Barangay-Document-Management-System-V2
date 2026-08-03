"""Run Flask app for local/production use.

Usage:
  python run_server.py                    # HTTP via Waitress (default)
  python run_server.py --ssl              # HTTPS via werkzeug (auto-generates cert)
  python run_server.py --ssl --gen-cert   # HTTPS + force regenerate cert

Camera works on localhost without HTTPS. Install the system on each
individual PC and access via http://localhost:5000.
"""

import argparse
import datetime
import ipaddress
import os
import shutil
import socket
import ssl
import sys
import webbrowser
from pathlib import Path

from waitress import serve


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _app_dir() -> Path:
    """Return the directory containing the executable (or script in dev)."""
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _data_dir() -> Path:
    """Persistent storage directory — next to the exe, or project root in dev."""
    d = _app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _setup_frozen_env() -> None:
    """When bundled with PyInstaller, redirect DB and uploads to app dir."""
    import shutil

    if not _is_frozen():
        return
    data = _data_dir()
    uploads = _app_dir() / "static" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    # Copy seed doc_templates from the bundle to writable storage on first run.
    bundled_templates = Path(sys._MEIPASS) / "frontend" / "static" / "uploads" / "doc_templates"
    data_templates = data / "templates"
    if bundled_templates.exists() and not data_templates.exists():
        shutil.copytree(str(bundled_templates), str(data_templates))
        print(f" * Copied seed templates to {data_templates}")

    (uploads / "documents").mkdir(parents=True, exist_ok=True)
    (uploads / "photos").mkdir(parents=True, exist_ok=True)
    (uploads / "signatures").mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("DATABASE_URL", f"sqlite:///{data / 'barangay.db'}")
    os.environ.setdefault("UPLOAD_FOLDER", str(uploads))
    os.environ.setdefault("DOCUMENT_STORAGE_ROOT", str(data / "documents"))
    os.environ.setdefault("DOCX_TEMPLATE_UPLOAD_DIR", str(data / "templates"))
    os.environ.setdefault("DOCX_OUTPUT_DIR", str(data / "generated"))

    # Expose templates and static next to exe for easy editing
    _meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent / "_internal"))
    tpl_src = _meipass / "frontend" / "templates"
    tpl_dst = _app_dir() / "templates"
    if tpl_src.is_dir():
        shutil.copytree(tpl_src, tpl_dst, dirs_exist_ok=True)
    os.environ.setdefault("BARANGAY_TEMPLATES_DIR", str(tpl_dst))

    static_src = _meipass / "frontend" / "static"
    static_dst = _app_dir() / "static"
    if static_src.is_dir():
        shutil.copytree(static_src, static_dst, dirs_exist_ok=True)
    os.environ.setdefault("BARANGAY_STATIC_DIR", str(static_dst))


def _get_local_ips():
    ips = []
    try:
        hostname = socket.gethostname()
        ips.append(socket.gethostbyname(hostname))
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return list(dict.fromkeys(ips))


def _generate_cert(cert_path: Path, key_path: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PH"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)

    san_entries = [x509.DNSName("localhost")]
    for ip_str in _get_local_ips():
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
        except ValueError:
            pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    # Also save as .crt so Windows recognizes it on double-click
    crt_path = cert_path.with_suffix(".crt")
    crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f" * Self-signed cert generated: {cert_path.name} (also {crt_path.name})")


def _port_in_use(host: str, port: int) -> bool:
    """Check if a port is already bound by another process."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def main() -> None:
    _setup_frozen_env()

    from barangay_project.app import create_app

    parser = argparse.ArgumentParser(description="Run the Barangay app server")
    parser.add_argument("--ssl", action="store_true", help="Serve via HTTPS (auto-generates self-signed cert)")
    parser.add_argument("--gen-cert", action="store_true", help="Force regenerate the self-signed certificate")
    args, _ = parser.parse_known_args()

    app = create_app()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))

    if _port_in_use(host, port):
        print(f"\n ERROR: Port {port} is already in use.")
        print(f" Another server instance may be running. Close it first.\n")
        sys.exit(1)

    ips = _get_local_ips()
    cert_dir = _data_dir()
    cert_file = cert_dir / "cert.pem"
    key_file = cert_dir / "key.pem"

    if args.ssl:
        from werkzeug.serving import run_simple

        if args.gen_cert or not (cert_file.exists() and key_file.exists()):
            _generate_cert(cert_file, key_file)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_file), str(key_file))

        urls = "\n".join(f"  https://{ip}:{port}" for ip in ips)
        print(f"\n * HTTPS server on port {port}")
        print(f" * Access via:\n{urls}\n")
        print(f" * To remove browser warning, install cert on each PC:\n"
              f"    1. Copy {cert_file.with_suffix('.crt')} to the client PC\n"
              f"    2. Double-click cert.crt\n"
              f"    3. Click 'Install Certificate' > Local Machine\n"
              f"    4. Place in 'Trusted Root Certification Authorities'\n"
              f"    5. Restart browser\n")

        webbrowser.open(f"https://localhost:{port}")
        run_simple(host, port, app, ssl_context=ctx, threaded=True)
    else:
        urls = "\n".join(f"  http://{ip}:{port}" for ip in ips)
        print(f"\n * HTTP server on port {port}")
        print(f" * Access via:\n{urls}")
        print(f" * Camera works on localhost:\n     http://localhost:{port}\n")
        webbrowser.open(f"http://localhost:{port}")
        serve(app, host=host, port=port, channel_timeout=120, cleanup_interval=30)


if __name__ == "__main__":
    main()

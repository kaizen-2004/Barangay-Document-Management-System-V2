"""Run Flask app with Waitress for local/production use.

Usage:
  python run_server.py                  # HTTP via Waitress
  python run_server.py --ssl            # HTTPS via stdlib SSL server
  python run_server.py --ssl --gen-cert # generate cert + HTTPS

No external applications required — cert generation uses Python cryptography library.
"""

import argparse
import datetime
import os
import socket
import ssl
import sys
import webbrowser
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIRequestHandler

from waitress import serve

from barangay_project.app import create_app


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
    uploads = data / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    # Copy seed doc_templates from bundle to writable data dir on first run
    bundled_templates = Path(sys._MEIPASS) / "frontend" / "static" / "uploads" / "doc_templates"
    data_templates = uploads / "doc_templates"
    if bundled_templates.exists() and not data_templates.exists():
        shutil.copytree(str(bundled_templates), str(data_templates))
        print(f" * Copied seed templates to {data_templates}")

    (uploads / "documents").mkdir(parents=True, exist_ok=True)
    (uploads / "photos").mkdir(parents=True, exist_ok=True)
    (uploads / "signatures").mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("DATABASE_URL", f"sqlite:///{data / 'barangay.db'}")
    os.environ.setdefault("UPLOAD_FOLDER", str(uploads))
    os.environ.setdefault("DOCX_TEMPLATE_UPLOAD_DIR", str(data_templates))
    os.environ.setdefault("DOCX_OUTPUT_DIR", str(uploads / "documents"))


class SecureHandler(WSGIRequestHandler):
    def make_environ(self):
        environ = super().make_environ()
        environ["wsgi.url_scheme"] = "https"
        return environ

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


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

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
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
    print(f" * Self-signed cert generated: {cert_path.name}")


def main() -> None:
    _setup_frozen_env()

    parser = argparse.ArgumentParser(description="Run the Barangay app server")
    parser.add_argument("--ssl", action="store_true", help="Serve via HTTPS (self-signed cert)")
    parser.add_argument("--gen-cert", action="store_true", help="Generate self-signed cert before starting")
    args, _ = parser.parse_known_args()

    # When bundled as .exe, default to SSL with cert generation
    if _is_frozen():
        args.ssl = True
        if not os.environ.get("SKIP_GEN_CERT"):
            args.gen_cert = True

    app = create_app()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))

    if args.ssl:
        cert_dir = _data_dir()
        cert_file = cert_dir / "cert.pem"
        key_file = cert_dir / "key.pem"

        if args.gen_cert or not (cert_file.exists() and key_file.exists()):
            _generate_cert(cert_file, key_file)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_file), str(key_file))

        server = make_server(host, port, app, handler_class=SecureHandler)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

        ips = _get_local_ips()
        urls = "\n".join(f"  https://{ip}:{port}" for ip in ips)
        print(f"\n * HTTPS server on port {port}")
        print(f" * Access via:\n{urls}\n")
        print(" * Accept the self-signed cert warning in your browser.\n")

        webbrowser.open(f"https://localhost:{port}")
        server.serve_forever()
    else:
        ips = _get_local_ips()
        urls = "\n".join(f"  http://{ip}:{port}" for ip in ips)
        print(f"\n * HTTP server on port {port}")
        print(f" * Access via:\n{urls}\n")
        webbrowser.open(f"http://localhost:{port}")
        serve(app, host=host, port=port)


if __name__ == "__main__":
    main()

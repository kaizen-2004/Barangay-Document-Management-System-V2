import os
from datetime import date
from urllib.parse import quote_plus
import warnings

import pytest
import pymysql
from sqlalchemy.engine import make_url

from barangay_project.app import create_app
from barangay_project.config import TestingConfig
from barangay_project.extensions import db
from barangay_project.models import DocumentType, Resident, User


def _default_test_database_url() -> str:
    user = os.environ.get("TEST_DB_USER", "root")
    password = os.environ.get("TEST_DB_PASSWORD", "")
    host = os.environ.get("TEST_DB_HOST", "127.0.0.1")
    port = os.environ.get("TEST_DB_PORT", "3306")
    name = os.environ.get("TEST_DB_NAME", "barangay_test")
    charset = os.environ.get("TEST_DB_CHARSET", "utf8mb4")

    encoded_password = quote_plus(password)
    return f"mysql+pymysql://{user}:{encoded_password}@{host}:{port}/{name}?charset={charset}"


def _ensure_test_database(test_db_url: str) -> None:
    parsed = make_url(test_db_url)
    if not parsed.drivername.startswith("mysql"):
        return

    conn = pymysql.connect(
        host=parsed.host or "127.0.0.1",
        port=int(parsed.port or 3306),
        user=parsed.username or "root",
        password=parsed.password or "",
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{parsed.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    finally:
        conn.close()


@pytest.fixture
def app(tmp_path):
    upload_dir = tmp_path / "uploads"
    configured_test_db_url = os.environ.get("TEST_DATABASE_URL")
    if configured_test_db_url:
        test_db_url = configured_test_db_url
        _ensure_test_database(test_db_url)
    else:
        mysql_candidate = _default_test_database_url()
        try:
            _ensure_test_database(mysql_candidate)
            test_db_url = mysql_candidate
        except Exception as exc:
            test_db_url = f"sqlite:///{tmp_path / 'test.sqlite3'}"
            warnings.warn(
                f"MySQL test database unavailable ({exc}); falling back to SQLite for local tests.",
                RuntimeWarning,
            )

    class TestConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = test_db_url
        WTF_CSRF_ENABLED = False
        LOGIN_RATE_LIMIT_MAX = 3
        LOGIN_RATE_LIMIT_WINDOW_SECONDS = 60
        AUTO_MIGRATE = False
        AUTO_CREATE_DB = True
        UPLOAD_FOLDER = str(upload_dir)
        SECURITY_HEADERS_ENABLED = False
        ERROR_REPORT_EMAIL = ""

    app = create_app(TestConfig)
    return app


@pytest.fixture(autouse=True)
def _setup_db(app):
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db_session(app):
    with app.app_context():
        yield db.session


@pytest.fixture
def make_user(db_session):
    def _make_user(username, password, role="clerk"):
        user = User(
            username=username,
            role=role,
        )
        user.set_password(password)
        db_session.add(user)
        db_session.commit()
        return user

    return _make_user


@pytest.fixture
def make_resident(db_session):
    def _make_resident(
        first_name="John",
        last_name="Doe",
        gender="Male",
        birth_date=date(1990, 1, 1),
        address="Test Address",
        barangay_id="BRGY-TEST-0001",
    ):
        resident = Resident(
            first_name=first_name,
            last_name=last_name,
            gender=gender,
            birth_date=birth_date,
            address=address,
            barangay_id=barangay_id,
        )
        db_session.add(resident)
        db_session.commit()
        return resident

    return _make_resident


@pytest.fixture
def make_document_type(db_session):
    def _make_document_type(
        name="Test Clearance",
        description="Test document type",
        requires_photo=False,
        template_path="generic",
    ):
        doc_type = DocumentType(
            name=name,
            description=description,
            requires_photo=requires_photo,
            template_path=template_path,
        )
        db_session.add(doc_type)
        db_session.commit()
        return doc_type

    return _make_document_type

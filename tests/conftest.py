import os
from datetime import date

import pytest

from barangay_project.app import create_app
from barangay_project.config import TestingConfig
from barangay_project.extensions import db
from barangay_project.models import DocumentType, Resident, User


@pytest.fixture
def app(tmp_path):
    upload_dir = tmp_path / "uploads"
    test_db_url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.sqlite3'}")

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
        middle_name=None,
        gender="Male",
        birth_date=date(1990, 1, 1),
        address="Test Address",
        barangay_id="BRGY-TEST-0001",
        contact_number=None,
        occupation=None,
    ):
        resident = Resident(
            first_name=first_name,
            last_name=last_name,
            middle_name=middle_name,
            gender=gender,
            birth_date=birth_date,
            address=address,
            barangay_id=barangay_id,
            contact_number=contact_number,
            occupation=occupation,
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

from barangay_project.models import Document
from barangay_project.time_utils import utcnow


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["db"] is True


def test_index_requires_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_global_search_all(client, make_user, make_resident, make_document_type):
    make_user("clerk", "Clerk123!", role="clerk")
    resident = make_resident(first_name="Jane", last_name="Doe")
    doc_type = make_document_type(name="Residency")

    _ = client.post(
        "/login",
        data={"username": "clerk", "password": "Clerk123!"},
        follow_redirects=False,
    )

    doc = Document(
        resident_id=resident.id,
        document_type_id=doc_type.id,
        status="issued",
        details="Test details",
        issue_date=utcnow(),
    )
    from barangay_project.extensions import db

    db.session.add(doc)
    db.session.commit()

    resp = client.get("/search?q=Doe&scope=all", follow_redirects=False)
    assert resp.status_code == 200
    assert b"Doe" in resp.data
    assert b"Residency" in resp.data

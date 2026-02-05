import os
from datetime import date

from barangay_project.extensions import db
from barangay_project.models import Document


def _login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_document_workflow(client, app, make_user, make_resident, make_document_type):
    make_user("clerk", "Clerk123!", role="clerk")
    make_user("boss", "Admin123!", role="admin")

    resident = make_resident(birth_date=date(1990, 1, 1))
    doc_type = make_document_type(name="Test Clearance", requires_photo=False)

    _login(client, "clerk", "Clerk123!")

    resp = client.post(
        "/documents/issue",
        data={
            "resident_id": resident.id,
            "document_type_id": doc_type.id,
            "details": "Test document",
            "issue_date": date.today().isoformat(),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    doc = Document.query.first()
    assert doc is not None
    assert doc.status == "draft"

    resp = client.post(f"/documents/{doc.id}/request-approval", follow_redirects=False)
    assert resp.status_code == 302
    doc = db.session.get(Document, doc.id)
    assert doc.status == "pending"

    client.get("/logout", follow_redirects=False)
    _login(client, "boss", "Admin123!")

    resp = client.post(f"/documents/{doc.id}/approve", follow_redirects=False)
    assert resp.status_code == 302
    doc = db.session.get(Document, doc.id)
    assert doc.status == "approved"

    resp = client.post(f"/documents/{doc.id}/issue", follow_redirects=False)
    assert resp.status_code == 302
    doc = db.session.get(Document, doc.id)
    assert doc.status == "issued"
    assert doc.file_path

    rel = doc.file_path
    if rel.startswith("uploads/"):
        rel = rel[len("uploads/") :]
    abs_path = os.path.join(app.config["UPLOAD_FOLDER"], rel)
    assert os.path.exists(abs_path)

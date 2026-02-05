import os

from barangay_project.models import Document
from barangay_project.pdf_utils import generate_document_pdf
from barangay_project.time_utils import utcnow


def test_generate_document_pdf(app, make_resident, make_document_type):
    resident = make_resident(first_name="Alex", last_name="Smith")
    doc_type = make_document_type(name="Generic Certificate", template_path="generic")

    doc = Document(
        resident_id=resident.id,
        document_type_id=doc_type.id,
        status="issued",
        details="Test PDF generation",
        issue_date=utcnow(),
    )
    from barangay_project.extensions import db

    db.session.add(doc)
    db.session.commit()

    rel_path = generate_document_pdf(doc)
    assert rel_path.startswith("uploads/documents/")

    rel = rel_path
    if rel.startswith("uploads/"):
        rel = rel[len("uploads/") :]
    abs_path = os.path.join(app.config["UPLOAD_FOLDER"], rel)
    assert os.path.exists(abs_path)

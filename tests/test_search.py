import json

from barangay_project.extensions import db
from barangay_project.models import Document
from barangay_project.time_utils import utcnow


def _login(client):
    resp = client.post(
        "/login",
        data={"username": "clerk", "password": "Clerk123!"},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _make_doc(resident, doc_type, **kw):
    doc = Document(
        resident_id=resident.id,
        document_type_id=doc_type.id,
        status=kw.pop("status", "issued"),
        issue_date=utcnow(),
        **kw,
    )
    db.session.add(doc)
    db.session.commit()
    return doc


class TestGlobalSearch:
    def test_search_by_first_name(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        make_resident(first_name="Alice", last_name="Wonder")
        _login(client)
        resp = client.get("/search?q=Alice&scope=residents")
        assert resp.status_code == 200
        assert b"ALICE" in resp.data or b"Alice" in resp.data

    def test_search_by_middle_name(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        make_resident(first_name="Bob", middle_name="Middleton", last_name="Build")
        _login(client)
        resp = client.get("/search?q=Middleton&scope=residents")
        assert resp.status_code == 200
        assert b"BUILD" in resp.data

    def test_search_by_occupation(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        make_resident(first_name="Carol", last_name="Eng", occupation="Engineer")
        _login(client)
        resp = client.get("/search?q=Engineer&scope=residents")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True).upper()
        assert "CAROL" in html

    def test_search_by_contact_number(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        make_resident(first_name="Dave", last_name="Phone", contact_number="09175550000")
        _login(client)
        resp = client.get("/search?q=0917555&scope=residents")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True).upper()
        assert "DAVE" in html

    def test_multi_word_search(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        make_resident(first_name="Juan", last_name="Dela Cruz", barangay_id="BRGY-MW-001")
        make_resident(first_name="Juan", last_name="Santos", barangay_id="BRGY-MW-002")
        _login(client)
        resp = client.get("/search?q=Juan+Cruz&scope=residents")
        assert resp.status_code == 200
        assert b"BRGY-MW-001" in resp.data

    def test_multi_word_no_match(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        make_resident(first_name="Juan", last_name="Dela Cruz", barangay_id="BRGY-NOMATCH-001")
        _login(client)
        resp = client.get("/search?q=Maria+Cruz&scope=residents")
        assert resp.status_code == 200
        assert b"NOMATCH" not in resp.data

    def test_empty_query_still_renders(self, client, make_user, make_resident):
        """Empty q shows search page with no results section."""
        make_user("clerk", "Clerk123!", role="clerk")
        _login(client)
        resp = client.get("/search?q=&scope=all")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Search" in html

    def test_search_document_by_field_values(self, client, make_user, make_resident, make_document_type):
        make_user("clerk", "Clerk123!", role="clerk")
        resident = make_resident(first_name="Field", last_name="Values", barangay_id="BRGY-FVAL")
        doc_type = make_document_type(name="Cert")
        _login(client)
        _make_doc(resident, doc_type, details="Custom details", field_values=json.dumps({"purpose": "Travel", "destination": "Manila"}))
        resp = client.get("/search?q=Manila&scope=documents")
        assert resp.status_code == 200
        assert b"VALUES" in resp.data or b"Field" in resp.data

    def test_search_document_by_field_values_partial(self, client, make_user, make_resident, make_document_type):
        make_user("clerk", "Clerk123!", role="clerk")
        resident = make_resident(first_name="Partial", last_name="Match", barangay_id="BRGY-PMATCH")
        doc_type = make_document_type(name="Permit")
        _login(client)
        _make_doc(resident, doc_type, field_values=json.dumps({"business_name": "Tasty Kitchen"}))
        resp = client.get("/search?q=Kitchen&scope=documents")
        assert resp.status_code == 200
        assert b"MATCH" in resp.data

    def test_search_documents_scope(self, client, make_user, make_resident, make_document_type):
        make_user("clerk", "Clerk123!", role="clerk")
        r = make_resident(first_name="DocOnly", last_name="Test", barangay_id="BRGY-DOCONLY")
        dt = make_document_type(name="ScopeDoc")
        _login(client)
        _make_doc(r, dt)
        resp = client.get("/search?q=DocOnly&scope=documents")
        assert resp.status_code == 200
        assert b"TEST" in resp.data or b"DocOnly" in resp.data

    def test_search_residents_scope(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        make_resident(first_name="ResOnly", last_name="Test", barangay_id="BRGY-RESONLY")
        _login(client)
        resp = client.get("/search?q=ResOnly&scope=residents")
        assert resp.status_code == 200
        assert b"BRGY-RESONLY" in resp.data

    def test_search_document_by_status_filter(self, client, make_user, make_resident, make_document_type):
        make_user("clerk", "Clerk123!", role="clerk")
        r = make_resident(first_name="StatusFilt", last_name="Test", barangay_id="BRGY-STATDOC")
        dt = make_document_type(name="StatusDoc")
        _login(client)
        _make_doc(r, dt, status="draft")
        resp = client.get("/search?q=StatusFilt&scope=documents&status=draft")
        assert resp.status_code == 200
        assert b"TEST" in resp.data

    def test_archived_resident_excluded_by_default(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        r = make_resident(first_name="Archivee", last_name="Excluded", barangay_id="BRGY-ARCH-EXCL")
        r.is_archived = True
        db.session.commit()
        _login(client)
        resp = client.get("/search?q=Archivee&scope=residents")
        assert resp.status_code == 200
        assert b"ARCH-EXCL" not in resp.data

    def test_archived_resident_included_with_flag(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        r = make_resident(first_name="Archivee", last_name="Included", barangay_id="BRGY-ARCH-INCL")
        r.is_archived = True
        db.session.commit()
        _login(client)
        resp = client.get("/search?q=Archivee&scope=residents&archived=1")
        assert resp.status_code == 200
        assert b"ARCH-INCL" in resp.data


class TestResidentListSearch:
    def test_list_residents_search_extra_fields(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        make_resident(first_name="Extra", last_name="Fields", middle_name="Mid", occupation="Plumber", contact_number="09220001111", barangay_id="BRGY-EXTRA")
        _login(client)
        for term in ["Mid", "Plumber", "0922000"]:
            resp = client.get(f"/residents?q={term}")
            assert resp.status_code == 200
            assert b"BRGY-EXTRA" in resp.data, f"Term {term} didn't match"

    def test_list_residents_multi_word(self, client, make_user, make_resident):
        make_user("clerk", "Clerk123!", role="clerk")
        make_resident(first_name="Peter", last_name="Parker", barangay_id="BRGY-MW2-001")
        make_resident(first_name="Peter", last_name="Pan", barangay_id="BRGY-MW2-002")
        _login(client)
        resp = client.get("/residents?q=Peter+Parker")
        assert resp.status_code == 200
        assert b"Parker".upper() in resp.data.upper()


class TestDocumentListSearch:
    def test_list_documents_field_values(self, client, make_user, make_resident, make_document_type):
        make_user("clerk", "Clerk123!", role="clerk")
        r = make_resident(first_name="DocField", last_name="ValSearch")
        dt = make_document_type(name="CustomDoc")
        _login(client)
        _make_doc(r, dt, field_values=json.dumps({"reason": "Medical Checkup"}))
        resp = client.get("/documents?q=Medical")
        assert resp.status_code == 200
        assert b"DocField".upper() in resp.data.upper()

    def test_list_documents_multi_word(self, client, make_user, make_resident, make_document_type):
        make_user("clerk", "Clerk123!", role="clerk")
        r = make_resident(first_name="Multi", last_name="Word Search", barangay_id="BRGY-MW3-001")
        dt = make_document_type(name="Doc")
        _login(client)
        _make_doc(r, dt)
        resp = client.get("/documents?q=Multi+Search")
        assert resp.status_code == 200
        assert b"Multi".upper() in resp.data.upper()

    def test_list_documents_status_filter(self, client, make_user, make_resident, make_document_type):
        make_user("clerk", "Clerk123!", role="clerk")
        r = make_resident(first_name="StatusTest", last_name="Doc", barangay_id="BRGY-STATFILT")
        dt = make_document_type(name="StatusFilterDoc")
        _login(client)
        _make_doc(r, dt, status="pending")
        resp = client.get("/documents?q=StatusTest&status=issued")
        assert resp.status_code == 200
        assert b"STATFILT" not in resp.data

    def test_archived_document_search(self, client, make_user, make_resident, make_document_type):
        make_user("clerk", "Clerk123!", role="clerk")
        r = make_resident(first_name="ArchDoc", last_name="Search", barangay_id="BRGY-ARCHDOC")
        dt = make_document_type(name="ArchDocType")
        _login(client)
        doc = _make_doc(r, dt)
        doc.is_archived = True
        db.session.commit()
        resp = client.get("/documents/archived?q=ArchDoc")
        assert resp.status_code == 200
        assert b"ARCHDOC" in resp.data

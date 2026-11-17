from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
import pytest

from documents.models import Document, DocumentVersion
from documents.services import replace_document_file
from documents.tests.helpers import accept_terms, make_pdf_file


@pytest.fixture
def owner(db):
    return get_user_model().objects.create_user(
        username="archive-owner",
        password="test-password",
    )


@pytest.fixture
def ordinary_user(db):
    return get_user_model().objects.create_user(
        username="archive-ordinary-user",
        password="test-password",
    )


@pytest.fixture
def administrator(db):
    return get_user_model().objects.create_user(
        username="archive-administrator",
        password="test-password",
        is_staff=True,
    )


@pytest.fixture
def archive_document(owner, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(
        title="Archivable report",
        description="A document used to test archiving.",
        uploaded_by=owner,
    )
    version_one = DocumentVersion.objects.create(
        document=document,
        version_number=1,
        file=make_pdf_file("report-v1.pdf", b"Version 1"),
        uploaded_by=owner,
    )
    version_two = DocumentVersion.objects.create(
        document=document,
        version_number=2,
        file=make_pdf_file("report-v2.pdf", b"Version 2"),
        uploaded_by=owner,
    )
    document.current_version = version_two
    document.save(update_fields=["current_version"])
    return document


def archive_url(document):
    return reverse("documents:document_archive", args=[document.pk])


def restore_url(document):
    return reverse("documents:document_restore", args=[document.pk])


def detail_url(document):
    return reverse("documents:document_detail", args=[document.pk])


def edit_url(document):
    return reverse("documents:document_edit", args=[document.pk])


@pytest.mark.django_db
def test_ordinary_user_cannot_archive_document(
    client, archive_document, ordinary_user
):
    client.force_login(ordinary_user)
    client.raise_request_exception = False

    response = client.post(archive_url(archive_document))

    assert response.status_code == 403
    archive_document.refresh_from_db()
    assert not archive_document.is_archived


@pytest.mark.django_db
def test_administrator_can_archive_document(client, archive_document, administrator):
    client.force_login(administrator)

    response = client.post(archive_url(archive_document))

    assert response.status_code == 302
    archive_document.refresh_from_db()
    assert archive_document.is_archived


@pytest.mark.django_db
def test_ordinary_user_cannot_view_archived_document(
    client, archive_document, ordinary_user
):
    archive_document.is_archived = True
    archive_document.save(update_fields=["is_archived"])
    client.force_login(ordinary_user)

    response = client.get(detail_url(archive_document))

    assert response.status_code == 404


@pytest.mark.django_db
def test_administrator_can_view_archived_document_and_its_versions(
    client, archive_document, administrator
):
    archive_document.is_archived = True
    archive_document.save(update_fields=["is_archived"])
    client.force_login(administrator)
    accept_terms(administrator)

    response = client.get(detail_url(archive_document))

    assert response.status_code == 200
    assert b"Version 1" in response.content
    assert b"Version 2" in response.content
    assert b"Restore Document" in response.content
    assert response.content.index(b"Restore Document") < response.content.index(
        b"Version history"
    )


@pytest.mark.django_db
def test_archived_documents_page_excludes_active_documents(
    client, archive_document, administrator, owner
):
    archive_document.is_archived = True
    archive_document.save(update_fields=["is_archived"])
    active_document = Document.objects.create(
        title="Active report",
        uploaded_by=owner,
    )
    client.force_login(administrator)

    response = client.get(
        reverse("documents:document_list"),
        {"archived": "1"},
    )

    assert response.status_code == 200
    assert b"Archived Documents" in response.content
    assert b"Browse and restore archived university documents." in response.content
    assert list(response.context["documents"]) == [archive_document]
    assert active_document not in response.context["documents"]


@pytest.mark.django_db
def test_archive_button_is_in_document_details_header(
    client, archive_document, administrator
):
    client.force_login(administrator)
    accept_terms(administrator)

    response = client.get(detail_url(archive_document))

    assert response.status_code == 200
    assert response.content.count(b"Archive Document") == 1
    assert response.content.index(b"Archive Document") < response.content.index(
        b"Version history"
    )


@pytest.mark.django_db
def test_administrator_can_restore_document_for_ordinary_users(
    client, archive_document, administrator, ordinary_user
):
    archive_document.is_archived = True
    archive_document.save(update_fields=["is_archived"])
    client.force_login(administrator)

    response = client.post(restore_url(archive_document))

    assert response.status_code == 302
    assert response.headers["Location"] == detail_url(archive_document)
    archive_document.refresh_from_db()
    assert not archive_document.is_archived

    client.force_login(ordinary_user)
    accept_terms(ordinary_user)
    response = client.get(detail_url(archive_document))

    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
def test_archived_document_cannot_be_edited(
    client, archive_document, owner, method
):
    archive_document.is_archived = True
    archive_document.save(update_fields=["is_archived"])
    client.force_login(owner)

    response = getattr(client, method)(edit_url(archive_document))

    assert response.status_code == 404
    archive_document.refresh_from_db()
    assert archive_document.title == "Archivable report"


@pytest.mark.django_db
def test_archived_document_cannot_be_replaced(
    archive_document, administrator
):
    archive_document.is_archived = True
    archive_document.save(update_fields=["is_archived"])
    original_version_ids = list(
        archive_document.versions.values_list("pk", flat=True)
    )

    with pytest.raises(ValidationError, match="archived"):
        replace_document_file(
            document_id=archive_document.pk,
            uploaded_file=make_pdf_file(
                "replacement.pdf",
                b"replacement contents",
            ),
            uploader=administrator,
        )

    assert list(archive_document.versions.values_list("pk", flat=True)) == (
        original_version_ids
    )


@pytest.mark.django_db
def test_archiving_preserves_document_versions(
    client, archive_document, administrator
):
    version_ids = list(
        archive_document.versions.values_list("pk", flat=True)
    )
    current_version_id = archive_document.current_version_id
    client.force_login(administrator)

    response = client.post(archive_url(archive_document))

    assert response.status_code == 302
    archive_document.refresh_from_db()
    assert archive_document.is_archived
    assert archive_document.current_version_id == current_version_id
    assert list(archive_document.versions.values_list("pk", flat=True)) == (
        version_ids
    )

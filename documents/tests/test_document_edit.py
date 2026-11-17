from django.contrib.auth import get_user_model
from django.urls import reverse
import pytest

from documents.models import Document, DocumentCategory, DocumentVersion
from documents.tests.helpers import accept_terms, make_pdf_file


@pytest.fixture
def owner(db):
    return get_user_model().objects.create_user(
        username="document-owner",
        password="test-password",
    )


@pytest.fixture
def ordinary_user(db):
    return get_user_model().objects.create_user(
        username="ordinary-user",
        password="test-password",
    )


@pytest.fixture
def administrator(db):
    return get_user_model().objects.create_user(
        username="document-admin",
        password="test-password",
        is_staff=True,
    )


@pytest.fixture
def category(db):
    return DocumentCategory.objects.create(name="Reports")


@pytest.fixture
def document(owner, category, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(
        title="Annual report",
        description="Original description",
        uploaded_by=owner,
    )
    document.categories.add(category)
    version = DocumentVersion.objects.create(
        document=document,
        version_number=1,
        file=make_pdf_file("report.pdf", b"Version 1 contents"),
        uploaded_by=owner,
    )
    document.current_version = version
    document.save(update_fields=["current_version"])
    return document


def edit_url(document):
    return reverse("documents:document_edit", args=[document.pk])


def edit_data(category, **overrides):
    return {
        "title": "Annual report",
        "description": "Updated description",
        "categories": [str(category.pk)],
        **overrides,
    }


@pytest.mark.django_db
def test_original_uploader_can_edit_their_document(
    client, document, owner, category
):
    client.force_login(owner)

    response = client.get(edit_url(document))

    assert response.status_code == 200

    response = client.post(
        edit_url(document),
        edit_data(category, title="Updated by owner"),
    )

    assert response.status_code == 302
    document.refresh_from_db()
    assert document.title == "Updated by owner"


@pytest.mark.django_db
def test_another_ordinary_user_cannot_edit_the_document(
    client, document, ordinary_user, category
):
    client.force_login(ordinary_user)
    client.raise_request_exception = False

    response = client.get(edit_url(document))

    assert response.status_code == 403

    response = client.post(
        edit_url(document),
        edit_data(category, title="Unauthorized change"),
    )

    assert response.status_code == 403
    document.refresh_from_db()
    assert document.title == "Annual report"


@pytest.mark.django_db
def test_administrator_can_edit_another_users_document(
    client, document, administrator, owner, category
):
    client.force_login(administrator)

    response = client.post(
        edit_url(document),
        edit_data(category, title="Admin update"),
    )

    assert response.status_code == 302
    document.refresh_from_db()
    assert document.title == "Admin update"
    assert document.uploaded_by == owner


@pytest.mark.django_db
def test_updating_title_does_not_create_a_new_version(
    client, document, owner, category
):
    client.force_login(owner)

    response = client.post(
        edit_url(document),
        edit_data(category, title="Retitled report"),
    )

    assert response.status_code == 302
    document.refresh_from_db()
    assert document.title == "Retitled report"
    assert list(document.versions.values_list("version_number", flat=True)) == [1]
    assert document.current_version.version_number == 1


@pytest.mark.django_db
def test_replacing_file_creates_version_two(
    client, document, owner, category
):
    client.force_login(owner)

    response = client.post(
        edit_url(document),
        edit_data(
            category,
            uploaded_file=make_pdf_file(
                "replacement.pdf",
                b"Version 2 contents",
            ),
        ),
    )

    assert response.status_code == 302
    document.refresh_from_db()
    assert list(document.versions.values_list("version_number", flat=True)) == [
        1,
        2,
    ]
    assert document.current_version.version_number == 2


@pytest.mark.django_db
def test_version_one_remains_downloadable_after_file_replacement(
    client, document, owner, category
):
    original_version = document.current_version
    client.force_login(owner)
    accept_terms(owner)
    client.post(
        edit_url(document),
        edit_data(
            category,
            uploaded_file=make_pdf_file(
                "replacement.pdf",
                b"Version 2 contents",
            ),
        ),
    )

    response = client.get(
        reverse(
            "documents:download_version",
            args=[document.pk, original_version.pk],
        )
    )

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == (
        b"%PDF-1.4\nVersion 1 contents\n%%EOF"
    )


@pytest.mark.django_db
def test_administrator_file_replacement_preserves_original_uploader(
    client, document, administrator, owner, category
):
    client.force_login(administrator)

    response = client.post(
        edit_url(document),
        edit_data(
            category,
            uploaded_file=make_pdf_file(
                "replacement.pdf",
                b"Version 2 contents",
            ),
        ),
    )

    assert response.status_code == 302
    document.refresh_from_db()
    assert document.uploaded_by == owner
    assert document.current_version.uploaded_by == administrator

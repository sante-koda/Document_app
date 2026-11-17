from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.urls import reverse
import pytest

from documents.models import (
    Document,
    DocumentTerms,
    DocumentTermsAcceptance,
    DocumentVersion,
)
from documents.services import accept_document_terms
from documents.tests.helpers import make_pdf_file


@pytest.fixture(autouse=True)
def published_terms(db):
    return DocumentTerms.objects.create(
        version="1.0",
        content="Test document access terms.",
        is_published=True,
    )


@pytest.fixture
def terms_user(db):
    return get_user_model().objects.create_user(
        username="terms-user",
        password="test-password-123",
    )


@pytest.fixture
def terms_document(terms_user, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(
        title="Terms test document",
        description="A document used to test terms acceptance.",
        uploaded_by=terms_user,
    )
    previous_version = DocumentVersion.objects.create(
        document=document,
        version_number=1,
        file=make_pdf_file("terms-v1.pdf"),
        uploaded_by=terms_user,
    )
    current_version = DocumentVersion.objects.create(
        document=document,
        version_number=2,
        file=make_pdf_file("terms-v2.pdf"),
        uploaded_by=terms_user,
    )
    document.current_version = current_version
    document.save(update_fields=["current_version"])
    return document, previous_version


def redirect_next(response):
    return parse_qs(urlparse(response.headers["Location"]).query)["next"][0]


@pytest.mark.django_db
def test_unaccepted_user_is_redirected_to_terms_for_document(
    client, terms_user, terms_document
):
    document, _ = terms_document
    client.force_login(terms_user)
    document_url = reverse("documents:document_detail", args=[document.pk])

    response = client.get(document_url)

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == reverse(
        "documents:accept_terms"
    )
    assert redirect_next(response) == document_url


@pytest.mark.django_db
def test_accepting_terms_saves_acceptance_and_opens_requested_document(
    client, terms_user, terms_document
):
    document, _ = terms_document
    client.force_login(terms_user)
    document_url = reverse("documents:document_detail", args=[document.pk])
    accept_url = reverse("documents:accept_terms")

    terms_response = client.get(accept_url, {"next": document_url})
    response = client.post(
        accept_url,
        {"next": document_url, "accept_terms": "yes"},
    )

    assert terms_response.status_code == 200
    assert response.status_code == 302
    assert response.headers["Location"] == document_url
    assert DocumentTermsAcceptance.objects.filter(user=terms_user).exists()
    assert client.get(response.headers["Location"]).status_code == 200


@pytest.mark.django_db
def test_accepted_user_opens_document_without_terms_redirect(
    client, terms_user, terms_document
):
    document, _ = terms_document
    accept_document_terms(terms_user)
    client.force_login(terms_user)

    response = client.get(
        reverse("documents:document_detail", args=[document.pk])
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_declining_terms_returns_to_document_list_without_acceptance(
    client, terms_user, terms_document
):
    document, _ = terms_document
    client.force_login(terms_user)
    response = client.get(
        reverse("documents:document_detail", args=[document.pk])
    )

    assert response.status_code == 302
    terms_response = client.get(response.headers["Location"])
    assert terms_response.status_code == 200
    assert reverse("documents:document_list").encode() in terms_response.content

    list_response = client.get(reverse("documents:document_list"))

    assert list_response.status_code == 200
    assert not DocumentTermsAcceptance.objects.filter(user=terms_user).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("version_kind", ["current", "previous"])
def test_download_requires_terms_acceptance(
    client, terms_user, terms_document, version_kind
):
    document, previous_version = terms_document
    client.force_login(terms_user)
    if version_kind == "current":
        download_url = reverse(
            "documents:download_current", args=[document.pk]
        )
    else:
        download_url = reverse(
            "documents:download_version",
            args=[document.pk, previous_version.pk],
        )

    response = client.get(download_url)

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == reverse(
        "documents:accept_terms"
    )
    assert redirect_next(response) == download_url


@pytest.mark.django_db
def test_external_next_url_falls_back_to_document_list(
    client, terms_user
):
    client.force_login(terms_user)

    response = client.post(
        reverse("documents:accept_terms"),
        {"next": "https://example.org/outside", "accept_terms": "yes"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("documents:document_list")
    assert DocumentTermsAcceptance.objects.filter(user=terms_user).exists()

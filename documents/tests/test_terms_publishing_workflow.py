from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
import pytest

from documents.models import Document, DocumentTerms


@pytest.mark.django_db
def test_published_terms_workflow_through_admin_and_document_access():
    user_model = get_user_model()
    administrator = user_model.objects.create_superuser(
        username="terms-admin",
        email="terms-admin@example.test",
        password="test-password-123",
    )
    user = user_model.objects.create_user(
        username="terms-reader",
        password="test-password-123",
    )
    first_document = Document.objects.create(
        title="First terms workflow document",
        uploaded_by=user,
    )
    second_document = Document.objects.create(
        title="Second terms workflow document",
        uploaded_by=user,
    )
    admin_client = Client()
    admin_client.force_login(administrator)
    user_client = Client()
    user_client.force_login(user)
    admin_add_url = reverse("admin:documents_documentterms_add")
    terms_url = reverse("documents:accept_terms")

    publish_v1 = admin_client.post(
        admin_add_url,
        {
            "title": "Access Terms",
            "version": "1.0",
            "content": "Version one terms content.",
            "publish": "on",
            "created_by": "",
            "_save": "Save",
        },
    )

    assert publish_v1.status_code == 302
    version_one = DocumentTerms.objects.get(version="1.0")
    assert version_one.is_published

    first_document_url = reverse(
        "documents:document_detail", args=[first_document.pk]
    )
    first_redirect = user_client.get(first_document_url)

    assert first_redirect.status_code == 302
    assert first_redirect.headers["Location"].startswith(terms_url)
    terms_page_v1 = user_client.get(first_redirect.headers["Location"])
    assert terms_page_v1.status_code == 200
    assert b"Version 1.0" in terms_page_v1.content
    assert b"Version one terms content." in terms_page_v1.content

    accept_v1 = user_client.post(
        terms_url,
        {
            "next": first_document_url,
            "accept_terms": "yes",
        },
    )

    assert accept_v1.status_code == 302
    assert accept_v1.headers["Location"] == first_document_url
    assert user_client.get(first_document_url).status_code == 200

    publish_v2 = admin_client.post(
        admin_add_url,
        {
            "title": "Access Terms",
            "version": "2.0",
            "content": "Updated version two terms content.",
            "publish": "on",
            "created_by": "",
            "_save": "Save",
        },
    )

    assert publish_v2.status_code == 302, publish_v2.context["adminform"].form.errors
    version_one.refresh_from_db()
    version_two = DocumentTerms.objects.get(version="2.0")
    assert not version_one.is_published
    assert version_two.is_published

    second_document_url = reverse(
        "documents:document_detail", args=[second_document.pk]
    )
    second_redirect = user_client.get(second_document_url)

    assert second_redirect.status_code == 302
    assert second_redirect.headers["Location"].startswith(terms_url)
    terms_page_v2 = user_client.get(second_redirect.headers["Location"])
    assert terms_page_v2.status_code == 200
    assert b"Version 2.0" in terms_page_v2.content
    assert b"Updated version two terms content." in terms_page_v2.content

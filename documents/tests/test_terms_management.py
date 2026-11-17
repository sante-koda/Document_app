from django.contrib.auth import get_user_model
from django.urls import reverse
import pytest

from documents.models import DocumentTerms


@pytest.fixture
def terms_admin(db):
    return get_user_model().objects.create_user(
        username="terms-management-admin",
        password="test-password-123",
        is_staff=True,
    )


@pytest.mark.django_db
def test_staff_can_open_terms_create_page(client, terms_admin):
    client.force_login(terms_admin)

    response = client.get(reverse("documents:terms_create"))

    assert response.status_code == 200
    assert b"Create Terms and Conditions" in response.content


@pytest.mark.django_db
def test_staff_can_open_terms_edit_page(client, terms_admin):
    draft = DocumentTerms.objects.create(
        title="Draft terms",
        version="2.0",
        content="Draft content.",
    )
    client.force_login(terms_admin)

    response = client.get(
        reverse("documents:terms_edit", args=[draft.pk])
    )

    assert response.status_code == 200
    assert b"Edit Terms and Conditions" in response.content
    assert b"Draft terms" in response.content


@pytest.mark.django_db
def test_terms_management_shows_actions_for_drafts_only(client, terms_admin):
    published = DocumentTerms.objects.create(
        title="Published terms",
        version="1.0",
        content="Published content.",
        is_published=True,
    )
    draft = DocumentTerms.objects.create(
        title="Draft terms",
        version="2.0",
        content="Draft content.",
    )
    client.force_login(terms_admin)

    response = client.get(reverse("documents:terms_management"))

    assert response.status_code == 200
    assert b"<th class=\"px-6 py-3\">Actions</th>" in response.content
    assert (
        reverse("documents:terms_edit", args=[draft.pk]).encode()
        in response.content
    )
    assert (
        reverse("documents:terms_publish", args=[draft.pk]).encode()
        in response.content
    )
    assert (
        reverse("documents:terms_edit", args=[published.pk]).encode()
        not in response.content
    )
    assert (
        reverse("documents:terms_publish", args=[published.pk]).encode()
        not in response.content
    )

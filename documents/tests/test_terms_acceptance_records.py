from django.contrib.auth import get_user_model
from django.urls import reverse
import pytest

from documents.models import DocumentTerms, DocumentTermsAcceptance


@pytest.mark.django_db
def test_acceptance_records_include_all_active_users(client):
    user_model = get_user_model()
    users = [
        user_model.objects.create_user(
            username=username,
            password="test-password-123",
            is_staff=username == "koda",
        )
        for username in ("koda", "blessings", "james", "samuel")
    ]
    terms = DocumentTerms.objects.create(
        title="Document Access Terms",
        version="1.0",
        content="Test terms content.",
        is_published=True,
    )
    DocumentTermsAcceptance.objects.create(
        user=users[1],
        terms_version=terms.version,
    )
    client.force_login(users[0])

    response = client.get(reverse("documents:terms_acceptance_records"))

    assert response.status_code == 200
    assert [template.name for template in response.templates] == [
        "documents/terms_acceptance_records.html",
        "documents/base.html",
    ]
    assert response.context["total_users"] == 4
    assert response.context["accepted_count"] == 1
    assert response.context["pending_count"] == 3
    assert {
        record["user"].username for record in response.context["records"]
    } == {"koda", "blessings", "james", "samuel"}
    assert {
        record["user"].username
        for record in response.context["records"]
        if not record["has_accepted"]
    } == {"koda", "james", "samuel"}
    for username in ("koda", "blessings", "james", "samuel"):
        assert username.encode() in response.content
    assert b"No active users found." not in response.content

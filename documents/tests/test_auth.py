from django.contrib.auth import get_user_model
from django.urls import reverse
import pytest


@pytest.mark.django_db
def test_login_page_is_available(client):
    response = client.get(reverse("login"), {"next": "/documents/"})

    assert response.status_code == 200
    assert b'name="next" value="/documents/"' in response.content
    assert b'id="document-sidebar"' not in response.content
    assert b'data-drawer-toggle="document-sidebar"' not in response.content
    assert b'name="username"' in response.content
    assert b'name="password"' in response.content
    assert b"focus:ring-blue-500" in response.content


@pytest.mark.django_db
def test_login_page_hides_sidebar_for_authenticated_user(client):
    user = get_user_model().objects.create_user(
        username="login-page-user",
        password="test-password-123",
    )
    client.force_login(user)

    response = client.get(reverse("login"))

    assert response.status_code == 200
    assert b'id="document-sidebar"' not in response.content
    assert b'data-drawer-toggle="document-sidebar"' not in response.content
    assert b"sm:ml-64" not in response.content


@pytest.mark.django_db
def test_login_redirects_to_requested_documents_page(client):
    get_user_model().objects.create_user(
        username="document-user",
        password="test-password-123",
    )

    response = client.post(
        reverse("login"),
        {
            "username": "document-user",
            "password": "test-password-123",
            "next": "/documents/",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/documents/"


@pytest.mark.django_db
def test_authenticated_page_shows_logout_button(client):
    user = get_user_model().objects.create_user(
        username="logout-user",
        password="test-password",
    )
    client.force_login(user)

    response = client.get(reverse("documents:document_list"))

    assert response.status_code == 200
    assert b'action="/accounts/logout/"' in response.content
    assert b'name="csrfmiddlewaretoken"' in response.content
    assert b"Log out" in response.content


@pytest.mark.django_db
def test_terms_sidebar_link_is_only_shown_to_staff(client):
    user_model = get_user_model()
    ordinary_user = user_model.objects.create_user(
        username="terms-sidebar-user",
        password="test-password-123",
    )
    staff_user = user_model.objects.create_user(
        username="terms-sidebar-staff",
        password="test-password-123",
        is_staff=True,
    )
    staff_urls = {
        reverse("documents:terms_management").encode(),
        reverse("documents:terms_acceptance_records").encode(),
    }
    document_list_url = reverse("documents:document_list")

    client.force_login(ordinary_user)
    ordinary_response = client.get(document_list_url)
    for staff_url in staff_urls:
        assert staff_url not in ordinary_response.content

    client.force_login(staff_user)
    staff_response = client.get(document_list_url)
    for staff_url in staff_urls:
        assert staff_url in staff_response.content
    assert b">Acceptance Records</span>" in staff_response.content


@pytest.mark.django_db
def test_logout_button_logs_out_user(client):
    user = get_user_model().objects.create_user(
        username="logout-user",
        password="test-password",
    )
    client.force_login(user)

    response = client.post(reverse("logout"))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("login")
    assert "_auth_user_id" not in client.session

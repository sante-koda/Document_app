from django.contrib.auth import get_user_model
from django.urls import reverse
import pytest

from documents.models import Document, DocumentInteraction


@pytest.fixture
def activity_admin(db):
    return get_user_model().objects.create_user(
        username="history-admin",
        password="test-password",
        is_staff=True,
    )


@pytest.fixture
def activity_user(db):
    return get_user_model().objects.create_user(
        username="history-user",
        password="test-password",
    )


@pytest.fixture
def history_document(activity_user):
    return Document.objects.create(
        title="Quarterly history report",
        uploaded_by=activity_user,
    )


def create_activity(document, user, action):
    return DocumentInteraction.objects.create(
        document=document,
        user=user,
        action=action,
    )


def activity_history_url():
    return reverse("documents:activity_history")


@pytest.mark.django_db
def test_administrator_can_open_activity_history(
    client, activity_admin, history_document
):
    create_activity(
        history_document,
        activity_admin,
        DocumentInteraction.Action.UPLOAD,
    )
    client.force_login(activity_admin)

    response = client.get(activity_history_url())

    assert response.status_code == 200
    assert b"Activity History" in response.content
    assert b"Quarterly history report" in response.content


@pytest.mark.django_db
def test_ordinary_user_cannot_open_activity_history(client, activity_user):
    client.force_login(activity_user)
    client.raise_request_exception = False

    response = client.get(activity_history_url())

    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_sidebar_has_activity_and_archive_links(
    client, activity_admin
):
    client.force_login(activity_admin)

    response = client.get(reverse("documents:document_list"))

    assert response.status_code == 200
    assert b"Activity history" in response.content
    assert b"Archived documents" in response.content
    assert b"View archived documents" not in response.content
    assert b"Upload document" in response.content


@pytest.mark.django_db
def test_ordinary_user_sidebar_hides_admin_links(
    client, activity_user
):
    client.force_login(activity_user)

    response = client.get(reverse("documents:document_list"))

    assert response.status_code == 200
    assert b"Activity history" not in response.content
    assert b"Archived documents" not in response.content
    assert b"Upload document" in response.content


@pytest.mark.django_db
def test_activity_history_searches_by_document_title(
    client, activity_admin, history_document
):
    other_document = Document.objects.create(
        title="Unrelated policy",
        uploaded_by=history_document.uploaded_by,
    )
    matching = create_activity(
        history_document,
        activity_admin,
        DocumentInteraction.Action.UPLOAD,
    )
    create_activity(
        other_document,
        activity_admin,
        DocumentInteraction.Action.DOWNLOAD,
    )
    client.force_login(activity_admin)

    response = client.get(activity_history_url(), {"q": "Quarterly"})

    assert response.status_code == 200
    assert list(response.context["page_obj"].object_list) == [matching]
    assert b"Quarterly history report" in response.content
    assert b"Unrelated policy" not in response.content


@pytest.mark.django_db
def test_activity_history_searches_by_username(
    client, activity_admin, activity_user, history_document
):
    matching = create_activity(
        history_document,
        activity_user,
        DocumentInteraction.Action.UPLOAD,
    )
    create_activity(
        history_document,
        activity_admin,
        DocumentInteraction.Action.DOWNLOAD,
    )
    client.force_login(activity_admin)

    response = client.get(activity_history_url(), {"q": "history-user"})

    assert response.status_code == 200
    assert list(response.context["page_obj"].object_list) == [matching]
    assert b"history-user" in response.content


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action", "expected_action", "excluded_action"),
    [
        (
            DocumentInteraction.Action.UPLOAD,
            DocumentInteraction.Action.UPLOAD,
            DocumentInteraction.Action.DOWNLOAD,
        ),
        (
            DocumentInteraction.Action.DOWNLOAD,
            DocumentInteraction.Action.DOWNLOAD,
            DocumentInteraction.Action.UPLOAD,
        ),
    ],
)
def test_activity_history_filters_by_action(
    client,
    activity_admin,
    history_document,
    action,
    expected_action,
    excluded_action,
):
    create_activity(history_document, activity_admin, expected_action)
    create_activity(history_document, activity_admin, excluded_action)
    client.force_login(activity_admin)

    response = client.get(activity_history_url(), {"action": action})

    assert response.status_code == 200
    activities = list(response.context["page_obj"].object_list)
    assert len(activities) == 1
    assert activities[0].action == expected_action


@pytest.mark.django_db
def test_activity_history_paginates_more_than_25_records(
    client, activity_admin, history_document
):
    for _ in range(30):
        create_activity(
            history_document,
            activity_admin,
            DocumentInteraction.Action.DOWNLOAD,
        )
    client.force_login(activity_admin)

    first_page = client.get(activity_history_url())
    second_page = client.get(activity_history_url(), {"page": 2})

    assert first_page.status_code == 200
    assert first_page.context["page_obj"].number == 1
    assert len(first_page.context["page_obj"].object_list) == 25
    assert first_page.context["page_obj"].has_next()
    assert b"Page 1 of 2" in first_page.content

    assert second_page.status_code == 200
    assert second_page.context["page_obj"].number == 2
    assert len(second_page.context["page_obj"].object_list) == 5
    assert second_page.context["page_obj"].has_previous()


@pytest.mark.django_db
def test_activity_history_displays_empty_state_for_no_matches(
    client, activity_admin, history_document
):
    create_activity(
        history_document,
        activity_admin,
        DocumentInteraction.Action.UPLOAD,
    )
    client.force_login(activity_admin)

    response = client.get(activity_history_url(), {"q": "no-such-record"})

    assert response.status_code == 200
    assert b"No activity records found." in response.content
    assert response.context["page_obj"].paginator.count == 0

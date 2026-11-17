from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
import pytest

from documents.models import (
    Document,
    DocumentCategory,
    DocumentInteraction,
    DocumentVersion,
)
from documents.services import (
    archive_document,
    create_document,
    download_current_document,
    download_document_version,
    record_document_activity,
    replace_document_file,
    restore_document,
)
from documents.tests.helpers import make_pdf_file


@pytest.fixture
def activity_owner(db):
    return get_user_model().objects.create_user(
        username="automatic-activity-owner",
        password="test-password",
    )


@pytest.fixture
def activity_user(db):
    return get_user_model().objects.create_user(
        username="automatic-activity-user",
        password="test-password",
    )


@pytest.fixture
def activity_admin(db):
    return get_user_model().objects.create_user(
        username="automatic-activity-admin",
        password="test-password",
        is_staff=True,
    )


@pytest.fixture
def activity_category(db):
    return DocumentCategory.objects.create(name="Activity reports")


@pytest.fixture
def activity_document(activity_owner, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(
        title="Activity report",
        uploaded_by=activity_owner,
    )
    version_one = DocumentVersion.objects.create(
        document=document,
        version_number=1,
        file=make_pdf_file("report-v1.pdf", b"Version 1"),
        uploaded_by=activity_owner,
    )
    version_two = DocumentVersion.objects.create(
        document=document,
        version_number=2,
        file=make_pdf_file("report-v2.pdf", b"Version 2"),
        uploaded_by=activity_owner,
    )
    document.current_version = version_two
    document.save(update_fields=["current_version"])
    return document


def actions_for(document):
    return list(
        document.interactions.order_by("created_at", "pk").values_list(
            "action", flat=True
        )
    )


@pytest.mark.django_db
def test_upload_creates_upload_activity(
    activity_owner, activity_category, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path

    document = create_document(
        title="New activity report",
        description="",
        categories=[activity_category],
        uploaded_file=make_pdf_file("new-report.pdf", b"New report"),
        uploader=activity_owner,
    )

    activity = document.interactions.get()
    assert activity.action == DocumentInteraction.Action.UPLOAD
    assert activity.user == activity_owner
    assert activity.version == document.current_version


@pytest.mark.django_db
def test_failed_upload_does_not_create_upload_activity(
    activity_owner, activity_category
):
    with pytest.raises(ValidationError, match="Please upload a document file"):
        create_document(
            title="Failed activity report",
            description="",
            categories=[activity_category],
            uploaded_file=None,
            uploader=activity_owner,
        )

    assert not Document.objects.filter(title="Failed activity report").exists()
    assert not DocumentInteraction.objects.exists()


@pytest.mark.django_db
def test_upload_activity_failure_rolls_back_document_and_version(
    activity_owner, activity_category, settings, tmp_path, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path

    def fail_recording_activity(**kwargs):
        raise RuntimeError("Activity write failed")

    monkeypatch.setattr(
        "documents.services.record_document_activity",
        fail_recording_activity,
    )

    with pytest.raises(RuntimeError, match="Activity write failed"):
        create_document(
            title="Failed activity report",
            description="",
            categories=[activity_category],
            uploaded_file=make_pdf_file("new-report.pdf", b"New report"),
            uploader=activity_owner,
        )

    assert not Document.objects.filter(title="Failed activity report").exists()
    assert not DocumentVersion.objects.exists()
    assert not DocumentInteraction.objects.exists()


@pytest.mark.django_db
def test_replacing_file_creates_replace_activity(
    activity_document, activity_owner
):
    version = replace_document_file(
        document_id=activity_document.pk,
        uploaded_file=make_pdf_file("replacement.pdf", b"Replacement"),
        uploader=activity_owner,
    ).current_version

    activity = activity_document.interactions.get()
    assert activity.action == DocumentInteraction.Action.REPLACE
    assert activity.user == activity_owner
    assert activity.version == version


@pytest.mark.django_db
def test_replace_activity_failure_rolls_back_new_version(
    activity_document, activity_owner, monkeypatch
):
    original_version_id = activity_document.current_version_id

    def fail_recording_activity(**kwargs):
        raise RuntimeError("Activity write failed")

    monkeypatch.setattr(
        "documents.services.record_document_activity",
        fail_recording_activity,
    )

    with pytest.raises(RuntimeError, match="Activity write failed"):
        replace_document_file(
            document_id=activity_document.pk,
            uploaded_file=make_pdf_file(
                "replacement.pdf",
                b"Replacement",
            ),
            uploader=activity_owner,
        )

    activity_document.refresh_from_db()
    assert activity_document.current_version_id == original_version_id
    assert list(
        activity_document.versions.values_list("version_number", flat=True)
    ) == [1, 2]
    assert not activity_document.interactions.exists()


@pytest.mark.django_db
def test_current_and_previous_downloads_create_download_activities(
    activity_document, activity_user
):
    current_version = activity_document.current_version
    previous_version = activity_document.versions.get(version_number=1)

    assert download_current_document(
        document_id=activity_document.pk,
        user=activity_user,
    ).name == current_version.file.name
    assert download_document_version(
        document_id=activity_document.pk,
        version_id=previous_version.pk,
        user=activity_user,
    ).name == previous_version.file.name

    activities = list(activity_document.interactions.order_by("pk"))
    assert [activity.action for activity in activities] == [
        DocumentInteraction.Action.DOWNLOAD,
        DocumentInteraction.Action.DOWNLOAD,
    ]
    assert [activity.version for activity in activities] == [
        current_version,
        previous_version,
    ]


@pytest.mark.django_db
def test_editing_document_details_creates_edit_activity(
    client, activity_document, activity_owner, activity_category
):
    client.force_login(activity_owner)

    response = client.post(
        reverse("documents:document_edit", args=[activity_document.pk]),
        {
            "title": "Updated activity report",
            "description": "Updated details",
            "categories": [str(activity_category.pk)],
        },
    )

    assert response.status_code == 302
    activity = activity_document.interactions.get()
    assert activity.action == DocumentInteraction.Action.EDIT
    assert activity.user == activity_owner
    assert activity.version is None


@pytest.mark.django_db
def test_unauthorized_edit_does_not_create_edit_activity(
    client, activity_document, activity_user, activity_category
):
    client.force_login(activity_user)
    client.raise_request_exception = False

    response = client.post(
        reverse("documents:document_edit", args=[activity_document.pk]),
        {
            "title": "Unauthorized edit",
            "description": "",
            "categories": [str(activity_category.pk)],
        },
    )

    assert response.status_code == 403
    assert not activity_document.interactions.exists()


@pytest.mark.django_db
def test_edit_activity_failure_rolls_back_document_details(
    client,
    activity_document,
    activity_owner,
    activity_category,
    monkeypatch,
):
    client.force_login(activity_owner)

    def fail_recording_activity(**kwargs):
        raise RuntimeError("Activity write failed")

    monkeypatch.setattr(
        "documents.services.record_document_activity",
        fail_recording_activity,
    )

    with pytest.raises(RuntimeError, match="Activity write failed"):
        client.post(
            reverse("documents:document_edit", args=[activity_document.pk]),
            {
                "title": "Updated activity report",
                "description": "Updated details",
                "categories": [str(activity_category.pk)],
            },
        )

    activity_document.refresh_from_db()
    assert activity_document.title == "Activity report"
    assert not activity_document.interactions.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("download_previous", [False, True])
def test_unauthorized_download_does_not_create_download_activity(
    client, activity_document, activity_user, download_previous
):
    activity_document.is_archived = True
    activity_document.save(update_fields=["is_archived"])
    client.force_login(activity_user)
    if download_previous:
        version = activity_document.versions.get(version_number=1)
        url = reverse(
            "documents:download_version",
            args=[activity_document.pk, version.pk],
        )
    else:
        url = reverse(
            "documents:download_current",
            args=[activity_document.pk],
        )

    response = client.get(url)

    assert response.status_code == 403
    assert not activity_document.interactions.exists()


@pytest.mark.django_db
def test_archive_and_restore_create_activities(
    activity_document, activity_admin
):
    archive_document(
        document_id=activity_document.pk,
        user=activity_admin,
    )
    restore_document(
        document_id=activity_document.pk,
        user=activity_admin,
    )

    assert actions_for(activity_document) == [
        DocumentInteraction.Action.ARCHIVE,
        DocumentInteraction.Action.RESTORE,
    ]
    assert list(
        activity_document.interactions.values_list("user_id", flat=True)
    ) == [activity_admin.pk, activity_admin.pk]


@pytest.mark.django_db
def test_failed_archive_and_restore_do_not_create_activities(
    activity_document, activity_admin, activity_user
):
    with pytest.raises(PermissionDenied):
        archive_document(
            document_id=activity_document.pk,
            user=activity_user,
        )
    with pytest.raises(ValidationError, match="not archived"):
        restore_document(
            document_id=activity_document.pk,
            user=activity_admin,
        )
    archive_document(
        document_id=activity_document.pk,
        user=activity_admin,
    )
    with pytest.raises(ValidationError, match="already archived"):
        archive_document(
            document_id=activity_document.pk,
            user=activity_admin,
        )
    with pytest.raises(PermissionDenied):
        restore_document(
            document_id=activity_document.pk,
            user=activity_user,
        )

    assert actions_for(activity_document) == [
        DocumentInteraction.Action.ARCHIVE,
    ]


@pytest.mark.django_db
def test_archive_activity_failure_rolls_back_archiving(
    activity_document, activity_admin, monkeypatch
):
    def fail_recording_activity(**kwargs):
        raise RuntimeError("Activity write failed")

    monkeypatch.setattr(
        "documents.services.record_document_activity",
        fail_recording_activity,
    )

    with pytest.raises(RuntimeError, match="Activity write failed"):
        archive_document(
            document_id=activity_document.pk,
            user=activity_admin,
        )

    activity_document.refresh_from_db()
    assert not activity_document.is_archived
    assert not activity_document.interactions.exists()


@pytest.mark.django_db
def test_restore_activity_failure_rolls_back_restoring(
    activity_document, activity_admin, monkeypatch
):
    activity_document.is_archived = True
    activity_document.save(update_fields=["is_archived"])

    def fail_recording_activity(**kwargs):
        raise RuntimeError("Activity write failed")

    monkeypatch.setattr(
        "documents.services.record_document_activity",
        fail_recording_activity,
    )

    with pytest.raises(RuntimeError, match="Activity write failed"):
        restore_document(
            document_id=activity_document.pk,
            user=activity_admin,
        )

    activity_document.refresh_from_db()
    assert activity_document.is_archived
    assert not activity_document.interactions.exists()

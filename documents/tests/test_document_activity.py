from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.utils import timezone
import pytest

from documents.models import Document, DocumentInteraction, DocumentVersion
from documents.services import record_document_activity
from documents.tests.helpers import make_pdf_file


@pytest.fixture
def university_user(db):
    return get_user_model().objects.create_user(
        username="activity-document-owner",
        password="test-password",
    )


@pytest.fixture
def document(university_user):
    return Document.objects.create(
        title="Activity test document",
        uploaded_by=university_user,
    )


@pytest.mark.django_db
def test_document_activity_can_be_created_for_document_and_user(
    document, university_user
):
    activity = record_document_activity(
        document=document,
        user=university_user,
        action=DocumentInteraction.Action.DOWNLOAD,
    )

    assert activity.pk is not None
    assert activity.document == document
    assert activity.user == university_user


@pytest.mark.django_db
def test_document_activity_action_choices_are_available():
    assert DocumentInteraction.Action.choices == [
        ("upload", "Upload"),
        ("download", "Download"),
        ("edit", "Edit"),
        ("replace", "File replacement"),
        ("archive", "Archive"),
        ("restore", "Restore"),
    ]


@pytest.mark.django_db
def test_document_activity_can_record_optional_version(
    document, university_user
):
    activity_without_version = record_document_activity(
        document=document,
        user=university_user,
        action=DocumentInteraction.Action.EDIT,
    )
    version = DocumentVersion.objects.create(
        document=document,
        version_number=1,
        file=make_pdf_file(content=b"test file content"),
        uploaded_by=university_user,
    )
    activity_with_version = record_document_activity(
        document=document,
        user=university_user,
        action=DocumentInteraction.Action.REPLACE,
        version=version,
    )

    assert activity_without_version.version is None
    assert activity_with_version.version == version


@pytest.mark.django_db
def test_document_activities_are_ordered_newest_first(
    document, university_user
):
    older_activity = record_document_activity(
        document=document,
        user=university_user,
        action=DocumentInteraction.Action.UPLOAD,
    )
    newer_activity = record_document_activity(
        document=document,
        user=university_user,
        action=DocumentInteraction.Action.DOWNLOAD,
    )
    now = timezone.now()
    DocumentInteraction.objects.filter(pk=older_activity.pk).update(
        created_at=now - timedelta(minutes=1)
    )
    DocumentInteraction.objects.filter(pk=newer_activity.pk).update(
        created_at=now
    )

    assert list(
        DocumentInteraction.objects.values_list("pk", flat=True)
    ) == [newer_activity.pk, older_activity.pk]


@pytest.mark.django_db
def test_document_with_activity_cannot_be_deleted(document, university_user):
    record_document_activity(
        document=document,
        user=university_user,
        action=DocumentInteraction.Action.UPLOAD,
    )

    with pytest.raises(ProtectedError):
        document.delete()


@pytest.mark.django_db
def test_user_with_document_activity_cannot_be_deleted(
    document, university_user
):
    activity_user = get_user_model().objects.create_user(
        username="activity-user",
        password="test-password",
    )
    record_document_activity(
        document=document,
        user=activity_user,
        action=DocumentInteraction.Action.DOWNLOAD,
    )

    with pytest.raises(ProtectedError):
        activity_user.delete()

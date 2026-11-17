from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction, connection
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from unittest.mock import patch

from documents.models import (
    Document,
    DocumentCategory,
    DocumentTerms,
    DocumentTermsAcceptance,
    DocumentVersion,
)
from documents.services import (
    accept_document_terms,
    create_document,
    download_current_document,
    download_document_version,
    get_documents,
    has_accepted_document_terms,
    replace_document_file,
    edit_document,
)
from documents.tests.helpers import accept_terms, make_pdf_file

from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections
from threading import Barrier, Event
from time import sleep, monotonic
from queue import Queue
event = Event()

@pytest.fixture
def university_user(db):
    return get_user_model().objects.create_user(
        username="university-user",
        password="test-password",
    )


@pytest.fixture
def published_terms(db):
    return DocumentTerms.objects.create(
        version="1.0",
        content="Test document access terms.",
        is_published=True,
    )


@pytest.fixture
def document(university_user):
    return Document.objects.create(
        title="Financial Report",
        uploaded_by=university_user,
    )


@pytest.fixture(autouse=True)
def temporary_media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def create_document_version(document, university_user, version_number, version_notes=""):
    return DocumentVersion.objects.create(
        document=document,
        version_number=version_number,
        file=make_pdf_file(content=b"test file content"),
        version_notes=version_notes,
        uploaded_by=university_user,
    )

def hold_document_lock(
    document_id,
    lock_acquired,
    release_lock,
    thread_a_pid,
):
    close_old_connections()

    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                thread_a_pid.put(cursor.fetchone()[0])

            Document.objects.select_for_update().get(pk=document_id)

            lock_acquired.set()

            if not release_lock.wait(timeout=10):
                raise TimeoutError("Timed out waiting to release document lock")

    finally:
        close_old_connections()


def acquire_document_lock(
    document_id,
    attempting_lock,
    lock_acquired,
    thread_b_pid,
):
    close_old_connections()

    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                thread_b_pid.put(cursor.fetchone()[0])

            attempting_lock.set()

            Document.objects.select_for_update().get(pk=document_id)

            lock_acquired.set()

    finally:
        close_old_connections()

@pytest.mark.django_db
def test_creates_document_category():
    category = DocumentCategory.objects.create(
        name="Reports",
        description="Business reports",
    )

    saved_category = DocumentCategory.objects.get(pk=category.pk)
    assert saved_category.name == "Reports"
    assert saved_category.description == "Business reports"
    assert str(saved_category) == "Reports"
    
@pytest.mark.django_db
def test_optional_description():
    category = DocumentCategory.objects.create(
        name="Reports",
        description= ""
    )
    
    saved_category = DocumentCategory.objects.get(pk=category.pk)
    assert saved_category.description == ""
    assert str(saved_category) == "Reports"
    
@pytest.mark.django_db
def test_case_insensitive_duplicate_name_is_rejected():
    DocumentCategory.objects.create(name="Reports")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DocumentCategory.objects.create(name="REPORTS")


@pytest.mark.django_db
def test_categories_are_ordered_alphabetically_by_name():
    DocumentCategory.objects.create(name="Reports")
    DocumentCategory.objects.create(name="Invoices")
    DocumentCategory.objects.create(name="Contracts")

    assert list(DocumentCategory.objects.values_list("name", flat=True)) == [
        "Contracts",
        "Invoices",
        "Reports",
    ]
    
@pytest.mark.django_db
def test_category_name_is_required():
    category = DocumentCategory(name="")

    with pytest.raises(ValidationError):
        category.full_clean()
        
@pytest.mark.django_db
def test_category_timestamps_are_set():
    category = DocumentCategory.objects.create(name="Policies")

    assert category.created_at is not None
    assert category.updated_at is not None
    
@pytest.mark.django_db
def test_document_creation(university_user):
    document = Document.objects.create(
        title="Financial report",
        description="2026 financial report",
        uploaded_by=university_user,
    )

    saved_document = Document.objects.get(pk=document.pk)
    assert saved_document.title == "Financial report"
    assert saved_document.description == "2026 financial report"


@pytest.mark.django_db
def test_document_can_belong_to_multiple_categories(university_user):
    document = Document.objects.create(
        title="Financial report",
        uploaded_by=university_user,
    )
    reports = DocumentCategory.objects.create(name="Reports")
    finance = DocumentCategory.objects.create(name="Finance")
    document.categories.add(reports, finance)

    assert set(document.categories.all()) == {reports, finance}


@pytest.mark.django_db
def test_document_is_associated_with_its_original_uploader(university_user):
    document = Document.objects.create(
        title="Financial report",
        uploaded_by=university_user,
    )

    assert document.uploaded_by == university_user
    assert university_user.uploaded_documents.get(pk=document.pk) == document


@pytest.mark.django_db
def test_document_is_not_archived_by_default(university_user):
    document = Document.objects.create(
        title="Financial report",
        uploaded_by=university_user,
    )

    assert document.is_archived is False


@pytest.mark.django_db
def test_newer_documents_are_ordered_before_older_documents(university_user):
    older_document = Document.objects.create(
        title="Older report",
        uploaded_by=university_user,
    )
    newer_document = Document.objects.create(
        title="Newer report",
        uploaded_by=university_user,
    )
    now = timezone.now()
    Document.objects.filter(pk=older_document.pk).update(
        created_at=now - timedelta(days=1),
    )
    Document.objects.filter(pk=newer_document.pk).update(created_at=now)

    assert list(Document.objects.values_list("title", flat=True)) == [
        "Newer report",
        "Older report",
    ]


@pytest.mark.django_db
def test_document_string_representation_is_its_title(university_user):
    document = Document.objects.create(
        title="Financial report",
        uploaded_by=university_user,
    )

    assert str(document) == "Financial report"


@pytest.mark.django_db
def test_document_updated_at_changes_when_details_are_saved(university_user):
    document = Document.objects.create(
        title="Financial report",
        uploaded_by=university_user,
    )
    old_updated_at = timezone.now() - timedelta(days=1)
    Document.objects.filter(pk=document.pk).update(updated_at=old_updated_at)
    document.refresh_from_db()

    document.title = "Updated financial report"
    document.save()
    document.refresh_from_db()

    assert document.title == "Updated financial report"
    assert document.updated_at > old_updated_at


@pytest.mark.django_db
def test_document_version_is_associated_with_its_document(document, university_user):
    version = create_document_version(document, university_user, version_number=1)

    assert version.document == document
    assert document.versions.get(pk=version.pk) == version


@pytest.mark.django_db
def test_document_version_number_one_is_accepted(document, university_user):
    version = create_document_version(document, university_user, version_number=1)

    assert version.version_number == 1


@pytest.mark.django_db
def test_document_version_notes_can_be_empty(document, university_user):
    version = create_document_version(document, university_user, version_number=1)

    assert version.version_notes == ""


@pytest.mark.django_db
def test_document_version_is_associated_with_its_uploader(document, university_user):
    version = create_document_version(document, university_user, version_number=1)

    assert version.uploaded_by == university_user
    assert university_user.uploaded_document_versions.get(pk=version.pk) == version


@pytest.mark.django_db
def test_document_cannot_have_duplicate_version_numbers(document, university_user):
    create_document_version(document, university_user, version_number=1)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            create_document_version(document, university_user, version_number=1)


@pytest.mark.django_db
def test_document_can_set_its_current_version(university_user):
    document = Document.objects.create(
        title="Financial Report",
        uploaded_by=university_user,
    )
    version = create_document_version(document, university_user, version_number=1)

    document.current_version = version
    document.save()
    document.refresh_from_db()

    assert document.current_version == version


@pytest.mark.django_db
def test_new_document_has_no_current_version(university_user):
    document = Document.objects.create(
        title="Financial Report",
        uploaded_by=university_user,
    )

    assert document.current_version is None


@pytest.mark.django_db
def test_create_document_rejects_empty_title(university_user):
    category = DocumentCategory.objects.create(name="Reports")

    with pytest.raises(ValidationError, match="Document title cannot be empty"):
        create_document(
            title="",
            description="",
            categories=[category],
            uploaded_file=None,
            uploader=university_user,
        )


@pytest.mark.django_db
def test_create_document_rejects_whitespace_only_title(university_user):
    category = DocumentCategory.objects.create(name="Reports")

    with pytest.raises(ValidationError, match="Document title cannot be empty"):
        create_document(
            title="   ",
            description="",
            categories=[category],
            uploaded_file=None,
            uploader=university_user,
        )


@pytest.mark.django_db
def test_create_document_rejects_unauthenticated_uploader():
    category = DocumentCategory.objects.create(name="Reports")

    with pytest.raises(ValidationError, match="authenticated uploader is required"):
        create_document(
            title="Financial Report",
            description="",
            categories=[category],
            uploaded_file=None,
            uploader=AnonymousUser(),
        )


@pytest.mark.django_db
def test_create_document_rejects_empty_categories(university_user):
    with pytest.raises(ValidationError, match="category"):
        create_document(
            title="Financial Report",
            description="",
            categories=[],
            uploaded_file=None,
            uploader=university_user,
        )


@pytest.mark.django_db
def test_create_document_creates_initial_version_with_file_and_categories(
    university_user,
):
    reports = DocumentCategory.objects.create(name="Reports")
    finance = DocumentCategory.objects.create(name="Finance")
    uploaded_file = make_pdf_file(
        "financial-report.pdf",
        b"test PDF content",
    )

    document = create_document(
        title="Financial Report",
        description="Annual financial report",
        categories=[reports, finance],
        uploaded_file=uploaded_file,
        uploader=university_user,
    )
    document.refresh_from_db()
    version = document.versions.get(version_number=1)

    assert document.title == "Financial Report"
    assert set(document.categories.all()) == {reports, finance}
    assert version.version_number == 1
    with version.file.open("rb") as saved_file:
        assert saved_file.read() == b"%PDF-1.4\ntest PDF content\n%%EOF"
    assert version.uploaded_by == university_user
    assert document.current_version == version
    assert document.versions.count() == 1
    assert version.document == document


@pytest.mark.django_db
def test_create_document_rolls_back_if_version_creation_fails(university_user):
    category = DocumentCategory.objects.create(name="Reports")
    uploaded_file = make_pdf_file(
        "financial-report.pdf",
        b"test PDF content",
    )

    with patch(
        "documents.services.DocumentVersion.objects.create",
        side_effect=RuntimeError("Version creation failed"),
    ):
        with pytest.raises(RuntimeError, match="Version creation failed"):
            create_document(
                title="Financial Report",
                description="Annual financial report",
                categories=[category],
                uploaded_file=uploaded_file,
                uploader=university_user,
            )

    assert not Document.objects.filter(title="Financial Report").exists()
    assert category.documents.count() == 0


@pytest.mark.django_db
def test_create_document_deletes_uploaded_file_if_current_version_save_fails(
    university_user,
):
    category = DocumentCategory.objects.create(name="Reports")
    uploaded_file = make_pdf_file(
        "financial-report.pdf",
        b"test PDF content",
    )
    original_save = Document.save

    def fail_when_saving_current_version(document_instance, *args, **kwargs):
        if document_instance.current_version_id is not None:
            raise RuntimeError("Current version save failed")
        return original_save(document_instance, *args, **kwargs)

    with patch.object(
        Document,
        "save",
        autospec=True,
        side_effect=fail_when_saving_current_version,
    ):
        with pytest.raises(RuntimeError, match="Current version save failed"):
            create_document(
                title="Financial Report",
                description="Annual financial report",
                categories=[category],
                uploaded_file=uploaded_file,
                uploader=university_user,
            )

    assert not Document.objects.filter(title="Financial Report").exists()
    assert not default_storage.exists("documents/versions/financial-report.pdf")


@pytest.mark.django_db
def test_create_document_does_not_overwrite_existing_upload(
    university_user, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    category = DocumentCategory.objects.create(name="Reports")
    existing_file_name = default_storage.save(
        "documents/versions/financial-report.pdf",
        make_pdf_file("financial-report.pdf", b"existing file content"),
    )
    uploaded_file = make_pdf_file("financial-report.pdf", b"new file content")

    document = create_document(
        title="Financial Report",
        description="Annual financial report",
        categories=[category],
        uploaded_file=uploaded_file,
        uploader=university_user,
    )
    version = document.versions.get(version_number=1)

    assert version.file.name != existing_file_name
    assert default_storage.exists(existing_file_name)
    assert default_storage.exists(version.file.name)


@pytest.mark.django_db
def test_create_document_cleanup_preserves_existing_file(
    university_user, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    category = DocumentCategory.objects.create(name="Reports")
    existing_file_name = default_storage.save(
        "documents/versions/financial-report.pdf",
        make_pdf_file("financial-report.pdf", b"existing file content"),
    )
    uploaded_file = make_pdf_file("financial-report.pdf", b"new file content")
    original_save = Document.save

    def fail_when_saving_current_version(document_instance, *args, **kwargs):
        if document_instance.current_version_id is not None:
            raise RuntimeError("Current version save failed")
        return original_save(document_instance, *args, **kwargs)

    with patch.object(
        Document,
        "save",
        autospec=True,
        side_effect=fail_when_saving_current_version,
    ):
        with pytest.raises(RuntimeError, match="Current version save failed"):
            create_document(
                title="Financial Report",
                description="Annual financial report",
                categories=[category],
                uploaded_file=uploaded_file,
                uploader=university_user,
            )

    assert default_storage.exists(existing_file_name)
    with default_storage.open(existing_file_name, "rb") as saved_file:
        assert saved_file.read() == (
            b"%PDF-1.4\nexisting file content\n%%EOF"
        )
    _, stored_file_names = default_storage.listdir("documents/versions")
    assert len(stored_file_names) == 1
    assert stored_file_names[0] == existing_file_name.rsplit("/", 1)[-1]
    assert not Document.objects.filter(title="Financial Report").exists()


@pytest.mark.django_db
def test_edit_document_updates_metadata_without_creating_new_version(
    document, university_user
):
    original_category = DocumentCategory.objects.create(name="Reports")
    new_category = DocumentCategory.objects.create(name="Finance")
    document.categories.add(original_category)

    version_one = create_document_version(
        document, university_user, version_number=1
    )
    document.current_version = version_one
    document.save(update_fields=["current_version"])

    updated_document = edit_document(
        document_id=document.pk,
        title="Updated Financial Report",
        description="Updated description",
        categories=[new_category],
        uploader=university_user,
    )

    updated_document.refresh_from_db()

    assert updated_document.title == "Updated Financial Report"
    assert updated_document.description == "Updated description"
    assert list(updated_document.categories.all()) == [new_category]
    assert updated_document.versions.count() == 1
    assert updated_document.current_version == version_one


@pytest.mark.django_db
def test_edit_document_replacement_creates_new_version(
    document, university_user
):
    category = DocumentCategory.objects.create(name="Reports")
    document.categories.add(category)

    version_one = create_document_version(
        document, university_user, version_number=1
    )
    document.current_version = version_one
    document.save(update_fields=["current_version"])

    replacement_file = make_pdf_file(
        "replacement.pdf",
        b"Version 2 contents",
    )

    updated_document = edit_document(
        document_id=document.pk,
        title="Updated Financial Report",
        description="Updated description",
        categories=[category],
        uploader=university_user,
        uploaded_file=replacement_file,
        version_notes="Updated report",
    )

    updated_document.refresh_from_db()
    version_two = updated_document.current_version

    assert updated_document.title == "Updated Financial Report"
    assert updated_document.versions.count() == 2
    assert version_two.version_number == 2
    assert version_two.uploaded_by == university_user
    assert version_two.version_notes == "Updated report"
    assert version_two != version_one

    with version_two.file.open("rb") as saved_file:
        assert saved_file.read() == b"%PDF-1.4\nVersion 2 contents\n%%EOF"


@pytest.mark.django_db
def test_another_ordinary_user_cannot_edit_document(
    document, university_user
):
    category = DocumentCategory.objects.create(name="Reports")
    document.categories.add(category)

    another_user = get_user_model().objects.create_user(
        username="unauthorized-editor",
        password="test-password",
    )

    with pytest.raises(PermissionDenied):
        edit_document(
            document_id=document.pk,
            title="Unauthorized update",
            description="Unauthorized description",
            categories=[category],
            uploader=another_user,
        )

    document.refresh_from_db()

    assert document.title == "Financial Report"
    assert document.description == ""
    assert document.versions.count() == 0


@pytest.mark.django_db
def test_edit_document_rolls_back_and_deletes_new_file_if_save_fails(
    document, university_user, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path

    category = DocumentCategory.objects.create(name="Reports")
    document.categories.add(category)

    version_one = create_document_version(
        document, university_user, version_number=1
    )
    document.current_version = version_one
    document.save(update_fields=["current_version"])

    original_file_name = version_one.file.name

    replacement_file = make_pdf_file(
        "replacement.pdf",
        b"Version 2 contents",
    )

    original_save = Document.save

    def fail_when_saving_new_version(document_instance, *args, **kwargs):
        if document_instance.current_version_id != version_one.pk:
            raise RuntimeError("Document save failed")

        return original_save(document_instance, *args, **kwargs)

    with patch.object(
        Document,
        "save",
        autospec=True,
        side_effect=fail_when_saving_new_version,
    ):
        with pytest.raises(RuntimeError, match="Document save failed"):
            edit_document(
                document_id=document.pk,
                title="Changed title",
                description="Changed description",
                categories=[category],
                uploader=university_user,
                uploaded_file=replacement_file,
            )

    document.refresh_from_db()

    assert document.title == "Financial Report"
    assert document.description == ""
    assert document.current_version == version_one
    assert document.versions.count() == 1

    assert default_storage.exists(original_file_name)

    stored_files = default_storage.listdir("documents/versions")[1]
    assert stored_files == [
        original_file_name.rsplit("/", 1)[-1]
    ]


@pytest.mark.django_db
def test_replace_document_file_creates_new_version(document, university_user):
    version_one = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    replacing_user = get_user_model().objects.create_user(
        username="replacement-user",
        password="test-password",
        is_staff=True,
    )
    replacement_file = make_pdf_file(
        "replacement-report.pdf",
        b"replacement file content",
    )

    updated_document = replace_document_file(
        document_id=document.pk,
        uploaded_file=replacement_file,
        uploader=replacing_user,
    )
    version_two = updated_document.versions.get(version_number=2)
    updated_document.refresh_from_db()

    assert updated_document.versions.count() == 2
    assert version_two.version_number == 2
    assert version_two.uploaded_by == replacing_user
    assert updated_document.current_version == version_two
    assert updated_document.versions.filter(pk=version_one.pk).exists()

def replace_document_concurrently(
    user,
    document_id,
    filename,
    barrier,
):
    close_old_connections()

    try:
        uploaded_file = make_pdf_file(
            filename,
            b"replacement file content",
        )

        # Wait until both workers are ready
        barrier.wait(timeout=10)

        return replace_document_file(
            document_id=document_id,
            uploaded_file=uploaded_file,
            uploader=user,
        )

    finally:
        close_old_connections()

@pytest.mark.django_db(transaction=True)
def test_concurrent_document_replacements(
    document,
    university_user,
):
    # Create Version 1
    version_one = create_document_version(
        document,
        university_user,
        version_number=1,
    )

    document.current_version = version_one
    document.save(update_fields=["current_version"])

    # Create two authorised users
    user_one = get_user_model().objects.create_user(
        username="staff-one",
        password="test-password",
        is_staff=True,
    )

    user_two = get_user_model().objects.create_user(
        username="staff-two",
        password="test-password",
        is_staff=True,
    )

    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_one = executor.submit(
            replace_document_concurrently,
            user_one,
            document.pk,
            "replacement-one.pdf",
            barrier,
        )

        future_two = executor.submit(
            replace_document_concurrently,
            user_two,
            document.pk,
            "replacement-two.pdf",
            barrier,
        )

        future_one.result(timeout=30)
        future_two.result(timeout=30)
        
    # Refresh the document from the database
    document.refresh_from_db()

    # Retrieve all document versions
    versions = document.versions.order_by("version_number")

    # Verify version creation
    assert versions.count() == 3
    assert list(versions.values_list("version_number", flat=True)) == [1, 2, 3]

    # Verify the current version
    assert document.current_version.version_number == 3

    # Verify the original uploader remains unchanged
    assert document.uploaded_by == university_user

    # Verify both replacement users uploaded a version
    assert set(
        versions.filter(version_number__in=[2, 3])
        .values_list("uploaded_by_id", flat=True)
    ) == {user_one.pk, user_two.pk}

    # Verify both replacement files exist
    assert all(version.file.storage.exists(version.file.name) for version in versions)

@pytest.mark.django_db(transaction=True)
def test_postgresql_blocks_second_thread_until_lock_is_released(document):
    lock_a_acquired = Event()
    release_lock_a = Event()
    attempting_lock_b = Event()
    lock_b_acquired = Event()

    thread_a_pid = Queue()
    thread_b_pid = Queue()

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(
            hold_document_lock,
            document.pk,
            lock_a_acquired,
            release_lock_a,
            thread_a_pid,
        )

        try:
            if not lock_a_acquired.wait(timeout=5):
                future_a.result(timeout=1)
                pytest.fail("Thread A did not acquire the document lock")

            pid_a = thread_a_pid.get(timeout=5)

            future_b = executor.submit(
                acquire_document_lock,
                document.pk,
                attempting_lock_b,
                lock_b_acquired,
                thread_b_pid,
            )

            assert attempting_lock_b.wait(timeout=5)
            pid_b = thread_b_pid.get(timeout=5)

            # Wait until PostgreSQL confirms Thread B is blocked by Thread A.
            deadline = monotonic() + 5
            blocking_pids = []

            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_blocking_pids(%s)",
                        [pid_b],
                    )
                    blocking_pids = cursor.fetchone()[0]

                if pid_a in blocking_pids:
                    break

                sleep(0.05)

            assert pid_a in blocking_pids
            assert not lock_b_acquired.is_set()

        finally:
            # Always release Thread A, even if an assertion fails.
            release_lock_a.set()

        assert lock_b_acquired.wait(timeout=5)

        future_a.result(timeout=5)
        future_b.result(timeout=5)

@pytest.mark.django_db
def test_original_uploader_can_replace_their_document(document, university_user):
    create_document_version(document, university_user, version_number=1)
    replacement_file = make_pdf_file(
        "original-replacement.pdf",
        b"new content",
    )

    updated_document = replace_document_file(
        document_id=document.pk,
        uploaded_file=replacement_file,
        uploader=university_user,
    )

    assert updated_document.versions.count() == 2
    assert updated_document.current_version.version_number == 2
    assert updated_document.current_version.uploaded_by == university_user


@pytest.mark.django_db
def test_another_ordinary_user_cannot_replace_document(document, university_user):
    create_document_version(document, university_user, version_number=1)
    another_user = get_user_model().objects.create_user(
        username="another-ordinary-user",
        password="test-password",
        is_staff=False,
    )
    replacement_file = make_pdf_file(
        "denied-replacement.pdf",
        b"new content",
    )

    with pytest.raises(PermissionDenied):
        replace_document_file(
            document_id=document.pk,
            uploaded_file=replacement_file,
            uploader=another_user,
        )

    document.refresh_from_db()
    assert document.versions.count() == 1
    assert document.current_version is None


@pytest.mark.django_db
def test_administrator_can_replace_another_users_document(document, university_user):
    create_document_version(document, university_user, version_number=1)
    administrator = get_user_model().objects.create_user(
        username="document-administrator",
        password="test-password",
        is_staff=True,
    )
    replacement_file = make_pdf_file(
        "admin-replacement.pdf",
        b"new content",
    )

    updated_document = replace_document_file(
        document_id=document.pk,
        uploaded_file=replacement_file,
        uploader=administrator,
    )

    assert updated_document.versions.count() == 2
    assert updated_document.current_version.version_number == 2
    assert updated_document.current_version.uploaded_by == administrator


@pytest.mark.django_db
def test_archived_document_cannot_be_replaced(document, university_user):
    create_document_version(document, university_user, version_number=1)
    document.is_archived = True
    document.save()
    replacement_file = make_pdf_file("replacement.pdf", b"new content")

    with pytest.raises(ValidationError):
        replace_document_file(
            document_id=document.pk,
            uploaded_file=replacement_file,
            uploader=university_user,
        )
    assert document.versions.count() == 1
    assert document.versions.count() == 1


@pytest.mark.django_db
def test_replace_document_file_deletes_new_file_if_document_save_fails(
    document, university_user, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    version_one = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    document.current_version = version_one
    document.save()
    existing_file_name = version_one.file.name
    assert default_storage.exists(existing_file_name)

    replacement_file = make_pdf_file(
        "replacement-report.pdf",
        b"replacement file content",
    )
    original_save = Document.save

    def fail_when_saving_new_current_version(document_instance, *args, **kwargs):
        if document_instance.current_version_id != version_one.pk:
            raise RuntimeError("Current version save failed")
        return original_save(document_instance, *args, **kwargs)

    with patch.object(
        Document,
        "save",
        autospec=True,
        side_effect=fail_when_saving_new_current_version,
    ):
        with pytest.raises(RuntimeError, match="Current version save failed"):
            replace_document_file(
                document_id=document.pk,
                uploaded_file=replacement_file,
                uploader=university_user,
            )

    document.refresh_from_db()
    assert document.current_version == version_one
    assert document.versions.count() == 1
    assert default_storage.exists(existing_file_name)
    _, stored_file_names = default_storage.listdir("documents/versions")
    assert stored_file_names == [existing_file_name.rsplit("/", 1)[-1]]


@pytest.mark.django_db
def test_replace_document_file_cleans_up_if_new_version_save_fails(
    document, university_user, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    version_one = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    document.current_version = version_one
    document.save()
    existing_file_name = version_one.file.name
    replacement_file = make_pdf_file(
        "replacement-report.pdf",
        b"replacement file content",
    )
    original_save = DocumentVersion.save

    def fail_when_saving_new_version_file(version, *args, **kwargs):
        if (
            version.version_number == 2
            and kwargs.get("update_fields") == ["file"]
        ):
            raise RuntimeError("New version file save failed")
        return original_save(version, *args, **kwargs)

    with patch.object(
        DocumentVersion,
        "save",
        autospec=True,
        side_effect=fail_when_saving_new_version_file,
    ):
        with pytest.raises(RuntimeError, match="New version file save failed"):
            replace_document_file(
                document_id=document.pk,
                uploaded_file=replacement_file,
                uploader=university_user,
            )

    document.refresh_from_db()
    assert document.current_version == version_one
    assert document.versions.count() == 1
    assert default_storage.exists(existing_file_name)
    _, stored_file_names = default_storage.listdir("documents/versions")
    assert stored_file_names == [existing_file_name.rsplit("/", 1)[-1]]


@pytest.mark.django_db
def test_authenticated_user_can_retrieve_current_document_file(
    document, university_user
):
    current_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    document.current_version = current_version
    document.save(update_fields=["current_version"])

    retrieved_file = download_current_document(
        document_id=document.pk,
        user=university_user,
    )

    assert retrieved_file.name == current_version.file.name
    assert retrieved_file.storage.exists(retrieved_file.name)


@pytest.mark.django_db
def test_unauthenticated_user_cannot_retrieve_document_file(document):
    with pytest.raises(PermissionDenied):
        download_current_document(
            document_id=document.pk,
            user=AnonymousUser(),
        )


@pytest.mark.django_db
def test_ordinary_user_cannot_retrieve_archived_document_file(
    document, university_user
):
    current_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    document.current_version = current_version
    document.is_archived = True
    document.save(update_fields=["current_version", "is_archived"])

    with pytest.raises(PermissionDenied):
        download_current_document(
            document_id=document.pk,
            user=university_user,
        )


@pytest.mark.django_db
def test_administrator_can_retrieve_archived_document_file(
    document, university_user
):
    current_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    document.current_version = current_version
    document.is_archived = True
    document.save(update_fields=["current_version", "is_archived"])
    administrator = get_user_model().objects.create_user(
        username="document-administrator",
        password="admin-password",
        is_staff=True,
    )

    retrieved_file = download_current_document(
        document_id=document.pk,
        user=administrator,
    )

    assert retrieved_file.name == current_version.file.name
    assert retrieved_file.storage.exists(retrieved_file.name)


@pytest.mark.django_db
def test_authenticated_user_can_retrieve_older_document_version(
    document, university_user
):
    older_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    current_version = create_document_version(
        document,
        university_user,
        version_number=2,
    )
    document.current_version = current_version
    document.save(update_fields=["current_version"])

    retrieved_file = download_document_version(
        document_id=document.pk,
        version_id=older_version.pk,
        user=university_user,
    )

    assert retrieved_file.name == older_version.file.name
    assert retrieved_file.storage.exists(retrieved_file.name)


@pytest.mark.django_db
def test_unauthenticated_user_cannot_retrieve_document_version(document):
    version = create_document_version(
        document,
        document.uploaded_by,
        version_number=1,
    )

    with pytest.raises(PermissionDenied):
        download_document_version(
            document_id=document.pk,
            version_id=version.pk,
            user=AnonymousUser(),
        )


@pytest.mark.django_db
def test_user_cannot_retrieve_version_from_another_document(
    document, university_user
):
    other_document = Document.objects.create(
        title="Another Report",
        uploaded_by=university_user,
    )
    other_version = create_document_version(
        other_document,
        university_user,
        version_number=1,
    )

    with pytest.raises(ValidationError):
        download_document_version(
            document_id=document.pk,
            version_id=other_version.pk,
            user=university_user,
        )


@pytest.mark.django_db
def test_ordinary_user_cannot_retrieve_archived_document_previous_version(
    document, university_user
):
    previous_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    current_version = create_document_version(
        document,
        university_user,
        version_number=2,
    )
    document.current_version = current_version
    document.is_archived = True
    document.save(update_fields=["current_version", "is_archived"])

    with pytest.raises(PermissionDenied):
        download_document_version(
            document_id=document.pk,
            version_id=previous_version.pk,
            user=university_user,
        )


@pytest.mark.django_db
def test_administrator_can_retrieve_archived_document_previous_version(
    document, university_user
):
    previous_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    current_version = create_document_version(
        document,
        university_user,
        version_number=2,
    )
    document.current_version = current_version
    document.is_archived = True
    document.save(update_fields=["current_version", "is_archived"])
    administrator = get_user_model().objects.create_user(
        username="version-administrator",
        password="admin-password",
        is_staff=True,
    )

    retrieved_file = download_document_version(
        document_id=document.pk,
        version_id=previous_version.pk,
        user=administrator,
    )

    assert retrieved_file.name == previous_version.file.name
    assert retrieved_file.storage.exists(retrieved_file.name)


@pytest.mark.django_db
def test_http_download_current_document_version(
    client, document, university_user
):
    current_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    document.current_version = current_version
    document.save(update_fields=["current_version"])
    client.force_login(university_user)
    accept_terms(university_user)

    response = client.get(
        reverse("documents:download_current", args=[document.pk])
    )

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]


@pytest.mark.django_db
def test_http_download_previous_document_version(
    client, document, university_user
):
    previous_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    current_version = create_document_version(
        document,
        university_user,
        version_number=2,
    )
    document.current_version = current_version
    document.save(update_fields=["current_version"])
    client.force_login(university_user)
    accept_terms(university_user)

    response = client.get(
        reverse(
            "documents:download_version",
            args=[document.pk, previous_version.pk],
        )
    )

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]


@pytest.mark.django_db
def test_http_unauthenticated_download_redirects_to_login(
    client, document, university_user
):
    current_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    document.current_version = current_version
    document.save(update_fields=["current_version"])

    response = client.get(
        reverse("documents:download_current", args=[document.pk])
    )

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_http_ordinary_user_cannot_download_archived_document(
    client, document, university_user
):
    current_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    document.current_version = current_version
    document.is_archived = True
    document.save(update_fields=["current_version", "is_archived"])
    client.force_login(university_user)

    response = client.get(
        reverse("documents:download_current", args=[document.pk])
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_http_administrator_can_download_archived_document(
    client, document, university_user
):
    current_version = create_document_version(
        document,
        university_user,
        version_number=1,
    )
    document.current_version = current_version
    document.is_archived = True
    document.save(update_fields=["current_version", "is_archived"])
    administrator = get_user_model().objects.create_user(
        username="http-download-administrator",
        password="admin-password",
        is_staff=True,
    )
    client.force_login(administrator)
    accept_terms(administrator)

    response = client.get(
        reverse("documents:download_current", args=[document.pk])
    )

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]


@pytest.mark.django_db
def test_http_download_nonexistent_document_returns_404(
    client, university_user
):
    client.force_login(university_user)
    nonexistent_document_id = (
        Document.objects.order_by("-pk")
        .values_list("pk", flat=True)
        .first()
        or 0
    ) + 1

    response = client.get(
        reverse("documents:download_current", args=[nonexistent_document_id])
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_get_documents_lists_active_documents(document, university_user):
    results = get_documents(user=university_user)

    assert list(results.values_list("pk", flat=True)) == [document.pk]


@pytest.mark.django_db
def test_get_documents_searches_by_title(document, university_user):
    document.title = "Employee Handbook"
    document.save(update_fields=["title"])
    Document.objects.create(
        title="Annual Summary",
        description="Employee details",
        uploaded_by=university_user,
    )

    results = get_documents(user=university_user, search="handbook")

    assert list(results.values_list("pk", flat=True)) == [document.pk]


@pytest.mark.django_db
def test_get_documents_searches_by_description(document, university_user):
    document.description = "Guidance for new employees"
    document.save(update_fields=["description"])
    Document.objects.create(
        title="Annual Summary",
        description="Financial results",
        uploaded_by=university_user,
    )

    results = get_documents(user=university_user, search="new employees")

    assert list(results.values_list("pk", flat=True)) == [document.pk]


@pytest.mark.django_db
def test_get_documents_filters_by_category(document, university_user):
    requested_category = DocumentCategory.objects.create(name="Policies")
    other_category = DocumentCategory.objects.create(name="Reports")
    document.categories.add(requested_category)
    other_document = Document.objects.create(
        title="Annual Summary",
        uploaded_by=university_user,
    )
    other_document.categories.add(other_category)

    results = get_documents(
        user=university_user,
        category_id=requested_category.pk,
    )

    assert list(results.values_list("pk", flat=True)) == [document.pk]


@pytest.mark.django_db
def test_get_documents_excludes_archived_documents_by_default(
    document, university_user
):
    document.is_archived = True
    document.save(update_fields=["is_archived"])
    active_document = Document.objects.create(
        title="Active Report",
        uploaded_by=university_user,
    )

    results = get_documents(user=university_user)

    assert list(results.values_list("pk", flat=True)) == [active_document.pk]


@pytest.mark.django_db
def test_ordinary_user_cannot_request_archived_documents(
    document, university_user
):
    document.is_archived = True
    document.save(update_fields=["is_archived"])

    with pytest.raises(PermissionDenied):
        get_documents(user=university_user, include_archived=True)


def test_user_has_not_accepted_terms(university_user):
    assert has_accepted_document_terms(university_user) is False


def test_user_can_accept_terms(university_user, published_terms):
    acceptance = accept_document_terms(university_user)

    assert acceptance.user == university_user
    assert acceptance.terms_version == published_terms.version
    assert has_accepted_document_terms(university_user) is True


def test_accepting_terms_twice_does_not_create_duplicates(
    university_user, published_terms
):
    accept_document_terms(university_user)
    accept_document_terms(university_user)

    assert DocumentTermsAcceptance.objects.filter(
        user=university_user,
        terms_version=published_terms.version,
    ).count() == 1

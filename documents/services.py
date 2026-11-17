import logging

from django.db import transaction
from documents.models import Document, DocumentInteraction, DocumentTerms, DocumentTermsAcceptance, DocumentVersion
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Max
from django.shortcuts import get_object_or_404
from documents.validators import validate_document_file


logger = logging.getLogger(__name__)


def create_document(
    *,
    title,
    description,
    categories,
    uploaded_file,
    uploader,
    version_notes="",
):
    saved_file_name = None
    storage = None
    
    if not uploader or not uploader.is_authenticated:
        raise ValidationError("An authenticated uploader is required.")
        
    if not title or not title.strip():
        raise ValidationError("Document title cannot be empty")
    
    if not categories:
        raise ValidationError("Document must have at least one category")
    
    if uploaded_file is None:
        raise ValidationError("Please upload a document file.")

    validate_document_file(uploaded_file)
    
    try:
        with transaction.atomic():
            document = Document.objects.create(
                title=title,
                description=description,
                uploaded_by=uploader,
            )
            
            document.categories.set(categories)
            
            version = DocumentVersion.objects.create(
                document=document,
                version_number=1,
                uploaded_by= uploader,
                version_notes= version_notes,
            )
            
            file_field = DocumentVersion._meta.get_field("file")
            storage = file_field.storage

            file_name = file_field.generate_filename(
                version,
                uploaded_file.name,
            )

            saved_file_name = storage.save(
                file_name,
                uploaded_file,
            )

            version.file = saved_file_name
            version.save(update_fields=["file"])
            
            document.current_version = version
            document.save()
            record_document_activity(
                document=document,
                user=uploader,
                action=DocumentInteraction.Action.UPLOAD,                        version=version,
            )
            
    except Exception:
        if saved_file_name and storage:
            storage.delete(saved_file_name)
            
        raise
        
        
        
    return document

def replace_document_file(
    *,
    document_id,
    uploaded_file,
    uploader,
    version_notes="",
):
    saved_file_name = None
    storage = None
    
    try:
        with transaction.atomic():
            
            if not uploader or not uploader.is_authenticated:
                        raise ValidationError("you have to be authenticated in order to upload a file")

            try:
                document = Document.objects.select_for_update().get(pk=document_id)
                
            except Document.DoesNotExist:

                raise ValidationError("The document does not exist") from None
            
            if document.is_archived:
                raise ValidationError("The document is archived and therefore cannot be replaced")

            if document.uploaded_by != uploader and not uploader.is_staff:
                raise PermissionDenied("you are not authorized to replace this document")
            
            if uploaded_file is None:
                raise ValidationError("please upload the new file")
            
            validate_document_file(uploaded_file)
            
            latest_version = document.versions.aggregate( Max("version_number"))
            next_version_number = (
                latest_version["version_number__max"] or 0
            ) + 1
            
            version = DocumentVersion.objects.create(
                document = document,
                version_number = next_version_number,
                version_notes = version_notes,
                uploaded_by = uploader,
            )
            
            document.current_version = version
            
            file_field = DocumentVersion._meta.get_field("file")
            storage = file_field.storage

            file_name = file_field.generate_filename(
                version,
                uploaded_file.name,
            )

            saved_file_name = storage.save(file_name, uploaded_file)

            version.file = saved_file_name
            version.save(update_fields=["file"])
            

            document.save()
            record_document_activity(
                document=document,
                user=uploader,
                action=DocumentInteraction.Action.REPLACE,
                version=version,
            )
            
            return document
        
    except Exception:
        if saved_file_name and storage:
            try:
                storage.delete(saved_file_name)
            except Exception:
                logger.exception(
                    "Failed to clean up uploaded file: %s",
                    saved_file_name,
                )
        raise
    

def download_current_document(*, document_id, user):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication is required.")

    try:
        document = Document.objects.select_related(
            "current_version"
        ).get(pk=document_id)
    except Document.DoesNotExist:
        raise ValidationError("Document does not exist.") from None

    if document.is_archived and not user.is_staff:
        raise PermissionDenied("You cannot access archived documents.")

    if not document.current_version:
        raise ValidationError("This document has no available version.")

    record_document_activity(
        document=document,
        user=user,
        action=DocumentInteraction.Action.DOWNLOAD,
        version=document.current_version,
    )
    return document.current_version.file


def download_document_version(*, document_id, version_id, user):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication is required.")

    try:
        document = Document.objects.get(pk=document_id)
    except Document.DoesNotExist:
        raise ValidationError("Document does not exist.") from None

    if document.is_archived and not user.is_staff:
        raise PermissionDenied("You cannot access archived documents.")

    try:
        version = DocumentVersion.objects.get(
            pk=version_id,
            document=document,
        )
    except DocumentVersion.DoesNotExist:
        raise ValidationError(
            "The requested version does not belong to this document."
        ) from None

    record_document_activity(
        document=document,
        user=user,
        action=DocumentInteraction.Action.DOWNLOAD,
        version=version,
    )
    
    return version.file

def get_documents(*, user, search=None, category_id=None, include_archived=False):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication is required.")

    documents = Document.objects.select_related(
        "uploaded_by",
        "current_version",
    ).prefetch_related("categories")

    if include_archived:
        if not user.is_staff:
            raise PermissionDenied("Only administrators can view archived documents.")
        documents = documents.filter(is_archived=True)
    else:
        documents = documents.filter(is_archived=False)

    if search:
        documents = documents.filter(
            Q(title__icontains=search) |
            Q(description__icontains=search)
        )

    if category_id:
        documents = documents.filter(categories__id=category_id)

    return documents.distinct()


def archive_document(*, document_id, user):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication is required.")

    if not user.is_staff:
        raise PermissionDenied("Only administrators can archive documents.")

    with transaction.atomic():
        try:
            document = Document.objects.get(pk=document_id)
        except Document.DoesNotExist:
            raise ValidationError("Document does not exist.") from None

        if document.is_archived:
            raise ValidationError("This document is already archived.")

        document.is_archived = True
        document.save(update_fields=["is_archived", "updated_at"])
        record_document_activity(
            document=document,
            user=user,
            action=DocumentInteraction.Action.ARCHIVE,
        )

    return document


def restore_document(*, document_id, user):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication is required.")

    if not user.is_staff:
        raise PermissionDenied("Only administrators can restore documents.")

    with transaction.atomic():
        try:
            document = Document.objects.get(pk=document_id)
        except Document.DoesNotExist:
            raise ValidationError("Document does not exist.") from None

        if not document.is_archived:
            raise ValidationError("This document is not archived.")

        document.is_archived = False
        document.save(update_fields=["is_archived", "updated_at"])
        record_document_activity(
            document=document,
            user=user,
            action=DocumentInteraction.Action.RESTORE,
        )

    return document

def record_document_activity(
    *,
    document,
    user,
    action,
    version=None,
):
    return DocumentInteraction.objects.create(
        document=document,
        user=user,
        action=action,
        version=version,
    )


def get_activity_history(*, user, search=None, action=None):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication is required.")

    if not user.is_staff:
        raise PermissionDenied(
            "Only administrators can view activity history."
        )

    activities = DocumentInteraction.objects.select_related(
        "document",
        "user",
        "version",
    )

    if search:
        activities = activities.filter(
            document__title__icontains=search
        ) | activities.filter(
            user__username__icontains=search
        )

    if action:
        activities = activities.filter(action=action)

    return activities.order_by("-created_at")


def edit_document(
    *,
    document_id,
    title,
    description,
    categories,
    uploader,
    uploaded_file=None,
    version_notes="",
):
    saved_file_name = None
    storage = None

    try:
        with transaction.atomic():

            # Check authentication.
            if not uploader or not uploader.is_authenticated:
                raise ValidationError(
                    "You must be authenticated to edit a document."
                )

            # Lock the document to prevent concurrent updates.
            try:
                document = Document.objects.select_for_update().get(
                    pk=document_id
                )
            except Document.DoesNotExist:
                raise ValidationError("The document does not exist.") from None

            # Check document status and permissions.
            if document.is_archived:
                raise ValidationError(
                    "Archived documents cannot be edited."
                )

            if document.uploaded_by != uploader and not uploader.is_staff:
                raise PermissionDenied(
                    "You are not authorized to edit this document."
                )

            # Validate document information.
            if not title or not title.strip():
                raise ValidationError("Document title cannot be empty.")

            if not categories:
                raise ValidationError(
                    "A document must have at least one category."
                )

            # Update document information.
            document.title = title.strip()
            document.description = description

            # Replace the file only if a new one was provided.
            if uploaded_file is not None:
                validate_document_file(uploaded_file)

                latest_version = document.versions.aggregate(
                    Max("version_number")
                )
                next_version_number = (
                    latest_version["version_number__max"] or 0
                ) + 1

                version = DocumentVersion.objects.create(
                    document=document,
                    version_number=next_version_number,
                    version_notes=version_notes,
                    uploaded_by=uploader,
                )

                file_field = DocumentVersion._meta.get_field("file")
                storage = file_field.storage

                file_name = file_field.generate_filename(
                    version,
                    uploaded_file.name,
                )

                saved_file_name = storage.save(
                    file_name,
                    uploaded_file,
                )

                version.file = saved_file_name
                version.save(update_fields=["file"])

                document.current_version = version

                record_document_activity(
                    document=document,
                    user=uploader,
                    action=DocumentInteraction.Action.REPLACE,
                    version=version,
                )

            # Save document and categories.
            document.save()
            document.categories.set(categories)

            # Record the metadata edit.
            record_document_activity(
                document=document,
                user=uploader,
                action=DocumentInteraction.Action.EDIT,
            )

        return document

    except Exception:
        # Database transactions cannot undo files saved to storage.
        if saved_file_name and storage:
            try:
                storage.delete(saved_file_name)
            except Exception:
                logger.exception(
                    "Failed to clean up uploaded file: %s",
                    saved_file_name,
                )

        raise



def get_current_document_terms():
    return DocumentTerms.objects.filter(
        is_published=True
    ).first()


def has_accepted_document_terms(user):
    if not user or not user.is_authenticated:
        return False

    current_terms = get_current_document_terms()

    if not current_terms:
        return False

    return DocumentTermsAcceptance.objects.filter(
        user=user,
        terms_version=current_terms.version,
    ).exists()


def accept_document_terms(user):
    if not user or not user.is_authenticated:
        raise ValidationError(
            "You must be logged in to accept the terms."
        )

    current_terms = get_current_document_terms()

    if not current_terms:
        raise ValidationError(
            "There are currently no published terms to accept."
        )

    acceptance, created = DocumentTermsAcceptance.objects.get_or_create(
        user=user,
        terms_version=current_terms.version,
    )

    return acceptance

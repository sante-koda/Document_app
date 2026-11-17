from django.core.files.uploadedfile import SimpleUploadedFile

from documents.models import DocumentTerms
from documents.services import accept_document_terms, get_current_document_terms


def accept_terms(user):
    if not get_current_document_terms():
        DocumentTerms.objects.create(
            version="1.0",
            content="Test document access terms.",
            is_published=True,
        )

    return accept_document_terms(user)


def make_pdf_file(name="report.pdf", content=b"Test PDF content"):
    return SimpleUploadedFile(
        name,
        b"%PDF-1.4\n" + content + b"\n%%EOF",
        content_type="application/pdf",
    )

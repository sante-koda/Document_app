
from io import BytesIO
from zipfile import ZipFile

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from documents.tests.helpers import make_pdf_file
from documents.validators import (
    MAX_FILE_SIZE,
    OLE_SIGNATURE,
    OOXML_CONTENT,
    validate_document_file,
)


def make_supported_file(filename):
    extension = filename.rsplit(".", 1)[-1].lower()

    if extension == "pdf":
        return make_pdf_file(filename)

    if extension in {"doc", "xls", "ppt"}:
        contents = OLE_SIGNATURE + b"test content"
    else:
        contents_buffer = BytesIO()
        with ZipFile(contents_buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "test content")
            archive.writestr(OOXML_CONTENT[f".{extension}"], "test content")
        contents = contents_buffer.getvalue()

    return SimpleUploadedFile(filename, contents)


@pytest.mark.parametrize(
    "filename",
    [
        "report.pdf",
        "report.doc",
        "report.docx",
        "report.xls",
        "report.xlsx",
        "report.ppt",
        "report.pptx",
        "REPORT.PDF",
    ],
)
def test_accepts_supported_extensions(filename):
    validate_document_file(make_supported_file(filename))


@pytest.mark.parametrize(
    "filename",
    [
        "image.png",
        "archive.zip",
        "script.exe",
        "document.txt",
    ],
)
def test_rejects_unsupported_extensions(filename):
    uploaded_file = SimpleUploadedFile(
        filename,
        b"test content",
    )

    with pytest.raises(ValidationError):
        validate_document_file(uploaded_file)


def test_rejects_files_larger_than_20_mb():
    uploaded_file = make_pdf_file(
        "large.pdf",
        b"x" * (
            MAX_FILE_SIZE + 1 - len(b"%PDF-1.4\n") - len(b"\n%%EOF")
        ),
    )

    with pytest.raises(ValidationError):
        validate_document_file(uploaded_file)


def test_accepts_file_of_exactly_20_mb():
    uploaded_file = make_pdf_file(
        "report.pdf",
        b"x" * (
            MAX_FILE_SIZE - len(b"%PDF-1.4\n") - len(b"\n%%EOF")
        ),
    )

    validate_document_file(uploaded_file)

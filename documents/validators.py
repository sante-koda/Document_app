
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from django.core.exceptions import ValidationError


ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

PDF_SIGNATURE = b"%PDF-"
OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")

OOXML_CONTENT = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
    ".pptx": "ppt/presentation.xml",
}


def has_valid_file_signature(uploaded_file, extension):
    """
    Check whether the uploaded file's contents match its extension.
    """
    uploaded_file.seek(0)

    try:
        header = uploaded_file.read(8)
        uploaded_file.seek(0)

        if extension == ".pdf":
            return header.startswith(PDF_SIGNATURE)

        if extension in {".doc", ".xls", ".ppt"}:
            return header.startswith(OLE_SIGNATURE)

        if extension in OOXML_CONTENT:
            try:
                with ZipFile(uploaded_file) as archive:
                    contents = archive.namelist()

                    return (
                        "[Content_Types].xml" in contents
                        and OOXML_CONTENT[extension] in contents
                    )
            except BadZipFile:
                return False

        return False

    finally:
        uploaded_file.seek(0)


def validate_document_file(uploaded_file):
    extension = Path(uploaded_file.name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "Unsupported file format. "
            "Allowed formats are PDF, DOC, DOCX, XLS, XLSX, PPT and PPTX."
        )

    if uploaded_file.size > MAX_FILE_SIZE:
        raise ValidationError(
            "File size cannot exceed 20 MB."
        )

    if not has_valid_file_signature(uploaded_file, extension):
        raise ValidationError(
            "The file contents do not match the selected file format."
        )

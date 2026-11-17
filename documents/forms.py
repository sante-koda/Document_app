
from django import forms
from documents.models import DocumentCategory, DocumentTerms
from documents.validators import validate_document_file


class DocumentUploadForm(forms.Form):
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "block w-full rounded-lg border border-gray-300 bg-gray-50 p-2.5 text-sm text-gray-900 focus:border-blue-500 focus:ring-blue-500",
            "placeholder": "Enter document title",
        })
    )

    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "block w-full rounded-lg border border-gray-300 bg-gray-50 p-2.5 text-sm text-gray-900 focus:border-blue-500 focus:ring-blue-500",
            "rows": 4,
            "placeholder": "Describe the document",
        })
    )

    categories = forms.ModelMultipleChoiceField(
        queryset=DocumentCategory.objects.all(),
        widget=forms.CheckboxSelectMultiple(),
        required=True,
    )

    uploaded_file = forms.FileField(
        validators=[validate_document_file],
        widget=forms.ClearableFileInput(attrs={
            "class": "block w-full cursor-pointer rounded-lg border border-gray-300 bg-gray-50 text-sm text-gray-900 focus:outline-none",
            "accept": ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx",
        })
    )

    version_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "block w-full rounded-lg border border-gray-300 bg-gray-50 p-2.5 text-sm text-gray-900 focus:border-blue-500 focus:ring-blue-500",
            "rows": 2,
            "placeholder": "Optional notes about this version",
        })
    )


class DocumentEditForm(forms.Form):
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "block w-full rounded-lg border border-gray-300 bg-gray-50 p-2.5 text-sm text-gray-900 focus:border-blue-500 focus:ring-blue-500",
        })
    )

    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "block w-full rounded-lg border border-gray-300 bg-gray-50 p-2.5 text-sm text-gray-900 focus:border-blue-500 focus:ring-blue-500",
            "rows": 4,
        })
    )

    categories = forms.ModelMultipleChoiceField(
        queryset=DocumentCategory.objects.all(),
        widget=forms.CheckboxSelectMultiple(),
        required=True,
    )

    uploaded_file = forms.FileField(
        required=False,
        validators=[validate_document_file],
        widget=forms.ClearableFileInput(attrs={
            "class": "block w-full cursor-pointer rounded-lg border border-gray-300 bg-gray-50 text-sm text-gray-900 focus:outline-none",
            "accept": ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx",
        })
    )

    version_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "block w-full rounded-lg border border-gray-300 bg-gray-50 p-2.5 text-sm text-gray-900 focus:border-blue-500 focus:ring-blue-500",
            "rows": 2,
        })
    )

class DocumentTermsForm(forms.ModelForm):
    class Meta:
        model = DocumentTerms
        fields = [
            "title",
            "version",
            "content",
        ]

        widgets = {
            "title": forms.TextInput(attrs={
                "class": (
                    "w-full p-2.5 text-sm border border-gray-300 "
                    "rounded-lg focus:ring-blue-500 focus:border-blue-500"
                ),
                "placeholder": "Enter terms title",
            }),

            "version": forms.TextInput(attrs={
                "class": (
                    "w-full p-2.5 text-sm border border-gray-300 "
                    "rounded-lg focus:ring-blue-500 focus:border-blue-500"
                ),
                "placeholder": "e.g. 1.0",
            }),

            "content": forms.Textarea(attrs={
                "rows": 12,
                "class": (
                    "w-full p-2.5 text-sm border border-gray-300 "
                    "rounded-lg focus:ring-blue-500 focus:border-blue-500"
                ),
                "placeholder": "Write the Terms and Conditions here...",
            }),
        }

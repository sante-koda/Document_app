from django import forms
import pytest

from documents.forms import DocumentEditForm, DocumentUploadForm
from documents.models import DocumentCategory


@pytest.mark.django_db
@pytest.mark.parametrize("form_class", [DocumentUploadForm, DocumentEditForm])
def test_category_widget_is_a_multi_select_dropdown(form_class):
    DocumentCategory.objects.create(name="Reports")
    DocumentCategory.objects.create(name="Policies")
    form = form_class()

    assert isinstance(
        form.fields["categories"].widget,
        forms.CheckboxSelectMultiple,
    )
    assert 'type="checkbox"' in form.as_p()
    assert form.as_p().count('type="checkbox"') == 2
    assert 'id="id_categories" class="h-4 w-4' not in form.as_p()


@pytest.mark.django_db
def test_category_widget_accepts_multiple_selected_categories():
    reports = DocumentCategory.objects.create(name="Reports")
    policies = DocumentCategory.objects.create(name="Policies")
    field = DocumentUploadForm.base_fields["categories"]

    assert set(field.clean([str(reports.pk), str(policies.pk)])) == {
        reports,
        policies,
    }

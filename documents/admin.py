from django.contrib import admin
from django import forms
from django.db import transaction
from django.utils import timezone
from documents.models import DocumentCategory, DocumentInteraction, DocumentTerms


@admin.register(DocumentCategory)
class DocumentCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at", "updated_at")
    search_fields = ("name",)
    ordering = ("name",)

@admin.register(DocumentInteraction)
class DocumentInteractionAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "user",
        "action",
        "version",
        "created_at",
    )

    list_filter = ("action", "created_at")
    search_fields = (
        "document__title",
        "user__username",
    )

    readonly_fields = (
        "document",
        "user",
        "version",
        "action",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(DocumentTerms)
class DocumentTermsAdmin(admin.ModelAdmin):
    class TermsForm(forms.ModelForm):
        publish = forms.BooleanField(
            required=False,
            label="Publish this version",
        )

        class Meta:
            model = DocumentTerms
            fields = ("title", "version", "content", "created_by")

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if not self.is_bound:
                self.fields["publish"].initial = self.instance.is_published

    list_display = (
        "title",
        "version",
        "is_published",
        "created_by",
        "created_at",
        "published_at",
    )

    list_filter = ("is_published", "created_at")

    search_fields = ("title", "version", "content")

    readonly_fields = (
        "created_at",
        "published_at",
    )

    fields = (
        "title",
        "version",
        "content",
        "publish",
        "created_by",
        "created_at",
        "published_at",
    )
    form = TermsForm

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by:
            obj.created_by = request.user

        obj.is_published = form.cleaned_data["publish"]

        if obj.is_published:
            DocumentTerms.objects.exclude(
                pk=obj.pk
            ).filter(
                is_published=True
            ).update(is_published=False)

            if not obj.published_at:
                obj.published_at = timezone.now()

        super().save_model(request, obj, form, change)

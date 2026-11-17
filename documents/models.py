from django.db import models
from django.db.models.functions import Lower
from django.conf import settings
from django.core.validators import MinValueValidator


class DocumentCategory(models.Model):
    name = models.CharField(max_length=150, unique=False)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                name="unique_document_category_name_ci",
            )
            ]
        ordering = ["name"]
        
    def __str__(self):
        return self.name

class Document(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    categories = models.ManyToManyField(
            'DocumentCategory',
            related_name= 'documents'
        )
    
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete = models.PROTECT,
        related_name="uploaded_documents"
        
    )
    
    current_version = models.ForeignKey(
        "DocumentVersion",
        on_delete=models.PROTECT,
        related_name="current_for_documents",
        null=True,
        blank=True,
)
    
    class Meta:
        ordering = ['-created_at']
        
    def __str__(self):
        return self.title


class DocumentInteraction(models.Model):

    class Action(models.TextChoices):
        UPLOAD = "upload", "Upload"
        DOWNLOAD = "download", "Download"
        EDIT = "edit", "Edit"
        REPLACE = "replace", "File replacement"
        ARCHIVE = "archive", "Archive"
        RESTORE = "restore", "Restore"

    document = models.ForeignKey(
        "Document",
        on_delete=models.PROTECT,
        related_name="interactions",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="document_interactions",
    )

    version = models.ForeignKey(
        "DocumentVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interactions",
    )

    action = models.CharField(
        max_length=20,
        choices=Action.choices,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["document", "-created_at"],
                name="doc_interaction_history_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.get_action_display()} - {self.document}"

class DocumentVersion(models.Model):
    document = models.ForeignKey(
        "Document",
        on_delete = models.CASCADE,
        related_name = "versions"
    )
    version_number = models.PositiveIntegerField( validators=[MinValueValidator(1)])
    file = models.FileField(upload_to="documents/versions/")
    version_notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_document_versions"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        constraints = [ models.UniqueConstraint(
            fields=["document", "version_number"],
            name="unique_document_version_number"
        )]
        
        ordering = ["version_number"]
    
    def __str__(self):
        return f"{self.document.title} - Version {self.version_number}"
    

class DocumentTermsAcceptance(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="document_terms_acceptances",
    )

    terms_version = models.CharField(
        max_length=20,
        default="1.0",
    )

    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "terms_version"],
                name="unique_user_terms_acceptance",
            )
        ]
        ordering = ["-accepted_at"]

    def __str__(self):
        return f"{self.user} - Terms {self.terms_version}"


class DocumentTerms(models.Model):
    version = models.CharField(
        max_length=20,
        unique=True,
    )

    title = models.CharField(
        max_length=255,
        default="Document Access Terms and Conditions",
    )

    content = models.TextField()

    is_published = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_document_terms",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    published_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_published"],
                condition=models.Q(is_published=True),
                name="only_one_published_document_terms",
            )
        ]

    def __str__(self):
        return f"{self.title} - Version {self.version}"

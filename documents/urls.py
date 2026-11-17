from django.urls import path

from documents import views

app_name = "documents"

urlpatterns = [
    path("terms/accept/", views.accept_terms, name="accept_terms"),
    path("terms/acceptance-records/", views.terms_acceptance_records, name="terms_acceptance_records",),
    path( "terms/manage/", views.terms_management, name="terms_management",),
    path( "terms/create/", views.terms_create, name="terms_create", ),
    path( "terms/<int:terms_id>/edit/", views.terms_edit, name="terms_edit", ),
    path( "terms/<int:terms_id>/publish/", views.terms_publish, name="terms_publish",),

    path("<int:document_id>/download/", views.download_current_document_view, name="download_current"),
    path("<int:document_id>/versions/<int:version_id>/download/", views.download_document_version_view, name="download_version"),

    path("", views.document_list, name="document_list"),
    path("upload/", views.document_upload, name="document_upload"),
    path("activity/", views.activity_history, name="activity_history"),

    path("<int:document_id>/edit/", views.document_edit, name="document_edit"),
    path("<int:document_id>/archive/", views.document_archive, name="document_archive"),
    path("<int:document_id>/restore/", views.document_restore, name="document_restore"),

    path("<int:document_id>/", views.document_detail, name="document_detail"),
    
    
]
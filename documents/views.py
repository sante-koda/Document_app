from django.utils import timezone

from django.contrib import messages
from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from documents.forms import DocumentTermsForm, DocumentUploadForm, DocumentEditForm

from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.core.exceptions import ValidationError, PermissionDenied
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth import get_user_model


from documents.models import (
    Document,
    DocumentCategory,
    DocumentInteraction,
    DocumentTerms,
    DocumentTermsAcceptance,
    DocumentVersion,
)
from documents.services import (
    accept_document_terms,
    download_current_document,
    download_document_version,
    edit_document,
    get_current_document_terms,
    get_documents,
    create_document,
    has_accepted_document_terms,
    restore_document,
    archive_document,
    get_activity_history
)


def terms_acceptance_required(request):
    if has_accepted_document_terms(request.user):
        return None

    terms_url = reverse("documents:accept_terms")
    return redirect(f"{terms_url}?next={request.get_full_path()}")



@login_required
def terms_acceptance_records(request):
    if not request.user.is_staff:
        raise PermissionDenied

    User = get_user_model()
    current_terms = get_current_document_terms()

    users = User.objects.filter(is_active=True).order_by(
        "username"
    )

    acceptance_by_user = {}

    if current_terms:
        acceptances = (
            DocumentTermsAcceptance.objects
            .filter(terms_version=current_terms.version)
            .select_related("user")
        )

        acceptance_by_user = {
            acceptance.user_id: acceptance
            for acceptance in acceptances
        }

    records = []

    for user in users:
        acceptance = acceptance_by_user.get(user.pk)

        records.append({
            "user": user,
            "acceptance": acceptance,
            "has_accepted": acceptance is not None,
        })

    accepted_count = len(acceptance_by_user)
    total_users = len(records)

    return render(
        request,
        "documents/terms_acceptance_records.html",
        {
            "records": records,
            "current_terms": current_terms,
            "total_users": total_users,
            "accepted_count": accepted_count,
            "pending_count": total_users - accepted_count,
        },
    )


@login_required
def download_current_document_view(request, document_id):
    document = get_object_or_404(Document, pk=document_id)
    if document.is_archived and not request.user.is_staff:
        raise PermissionDenied("You cannot access archived documents.")

    terms_redirect = terms_acceptance_required(request)
    if terms_redirect:
        return terms_redirect

    try:
        file = download_current_document(
            document_id=document_id,
            user=request.user,
        )

    except ValidationError as exc:
        raise Http404(exc.message[0]) from exc

    return FileResponse(
        file.open("rb"),
        as_attachment=True,
        filename=Path(file.name).name,
    )


@login_required
def download_document_version_view(request, document_id, version_id):
    document = get_object_or_404(Document, pk=document_id)
    if document.is_archived and not request.user.is_staff:
        raise PermissionDenied("You cannot access archived documents.")
    get_object_or_404(
        DocumentVersion,
        pk=version_id,
        document_id=document_id,
    )

    terms_redirect = terms_acceptance_required(request)
    if terms_redirect:
        return terms_redirect

    try:
        file = download_document_version(
            document_id=document_id,
            version_id=version_id,
            user=request.user,
        )

    except ValidationError as exc:
        raise Http404(exc.message[0]) from exc

    return FileResponse(
        file.open("rb"),
        as_attachment=True,
        filename=Path(file.name).name,
    )

@login_required
def document_list(request):
    search = request.GET.get("q", "")
    category_id = request.GET.get("category", "")
    show_archived = (
        request.user.is_staff
        and request.GET.get("archived") == "1"
    )

    documents = get_documents(
        user=request.user,
        search=search,
        category_id=category_id or None,
        include_archived=show_archived,
    )

    categories = DocumentCategory.objects.all()

    return render(
        request,
        "documents/document_list.html",
        {
            "documents": documents,
            "categories": categories,
            "search": search,
            "selected_category": category_id,
            "show_archived": show_archived,
        },
    )
    
@login_required
def document_detail(request, document_id):
    document = get_object_or_404(
        Document.objects.select_related(
            "uploaded_by",
            "current_version",
        ).prefetch_related(
            "categories",
            "versions__uploaded_by",
        ),
        pk=document_id,
    )

    if document.is_archived and not request.user.is_staff:
        raise Http404("Document not found.")

    terms_redirect = terms_acceptance_required(request)
    if terms_redirect:
        return terms_redirect

    return render(
        request,
        "documents/document_detail.html",
        {
            "document": document,
            "versions": document.versions.all(),
        },
    )
    

@login_required
def document_upload(request):
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)

        if form.is_valid():
            try:
                document = create_document(
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    categories=form.cleaned_data["categories"],
                    uploaded_file=form.cleaned_data["uploaded_file"],
                    uploader=request.user,
                    version_notes=form.cleaned_data["version_notes"],
                )

                return redirect(
                    "documents:document_detail",
                    document_id=document.id,
                )

            except ValidationError as exc:
                form.add_error(None, exc)

    else:
        form = DocumentUploadForm()

    return render(
        request,
        "documents/document_upload.html",
        {"form": form},
    )


@login_required
def document_edit(request, document_id):
    document = get_object_or_404(Document, pk=document_id)

    if document.is_archived:
        raise Http404("Document not found.")

    if document.uploaded_by != request.user and not request.user.is_staff:
        raise PermissionDenied("You cannot edit this document.")

    if request.method == "POST":
        form = DocumentEditForm(request.POST, request.FILES)

        if form.is_valid():
            try:
                document = edit_document(
                    document_id=document.id,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    categories=form.cleaned_data["categories"],
                    uploader=request.user,
                    uploaded_file=form.cleaned_data.get("uploaded_file"),
                    version_notes=form.cleaned_data.get("version_notes", ""),
                )

                return redirect(
                    "documents:document_detail",
                    document_id=document.id,
                )

            except ValidationError as exc:
                form.add_error(None, exc)

    else:
        form = DocumentEditForm(initial={
            "title": document.title,
            "description": document.description,
            "categories": document.categories.all(),
        })

    return render(
        request,
        "documents/document_edit.html",
        {
            "form": form,
            "document": document,
        },
    )



@login_required
@require_POST
def document_archive(request, document_id):
    try:
        document = archive_document(
            document_id=document_id,
            user=request.user,
        )
    except ValidationError as exc:
        raise Http404(exc.messages[0]) from exc

    return redirect("documents:document_list")


@login_required
@require_POST
def document_restore(request, document_id):
    try:
        document = restore_document(
            document_id=document_id,
            user=request.user,
        )
    except ValidationError as exc:
        raise Http404(exc.messages[0]) from exc

    return redirect("documents:document_detail", document_id=document.id)


@login_required
def activity_history(request):
    if not request.user.is_staff:
        raise PermissionDenied(
            "Only administrators can view activity history."
        )

    search = request.GET.get("q", "")
    action = request.GET.get("action", "")

    activities = get_activity_history(
        user=request.user,
        search=search,
        action=action,
    )

    paginator = Paginator(activities, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "documents/activity_history.html",
        {
            "page_obj": page_obj,
            "search": search,
            "selected_action": action,
            "action_choices": DocumentInteraction.Action.choices,
        },
    )


@login_required
def accept_terms(request):
    next_url = (
        request.POST.get("next")
        if request.method == "POST"
        else request.GET.get("next")
    )

    if not next_url or not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("documents:document_list")

    if has_accepted_document_terms(request.user):
        return redirect(next_url)

    current_terms = get_current_document_terms()

    if not current_terms:
        messages.error(
            request,
            "Document access terms have not yet been published. "
            "Please contact the administrator."
        )
        return redirect("documents:document_list")

    if request.method == "POST":
        if request.POST.get("accept_terms") == "yes":
            accept_document_terms(request.user)
            return redirect(next_url)

    return render(
        request,
        "documents/accept_terms.html",
        {
            "next": next_url,
            "terms": current_terms,
        },
    )


@login_required
def terms_management(request):
    if not request.user.is_staff:
        raise PermissionDenied

    terms_list = DocumentTerms.objects.all()

    current_terms = get_current_document_terms()

    return render(
        request,
        "documents/terms_management.html",
        {
            "terms_list": terms_list,
            "current_terms": current_terms,
        },
    )


@login_required
def terms_create(request):
    if not request.user.is_staff:
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentTermsForm(request.POST)

        if form.is_valid():
            terms = form.save(commit=False)
            terms.created_by = request.user
            terms.is_published = False
            terms.save()

            messages.success(
                request,
                "Terms and Conditions draft created successfully."
            )

            return redirect("documents:terms_management")
    else:
        form = DocumentTermsForm()

    return render(
        request,
        "documents/terms_form.html",
        {
            "form": form,
            "page_title": "Create Terms and Conditions",
        },
    )


@login_required
def terms_edit(request, terms_id):
    if not request.user.is_staff:
        raise PermissionDenied

    terms = get_object_or_404(DocumentTerms, pk=terms_id)

    if terms.is_published:
        messages.error(
            request,
            "Published terms cannot be edited. Create a new version instead."
        )
        return redirect("documents:terms_management")

    if request.method == "POST":
        form = DocumentTermsForm(request.POST, instance=terms)

        if form.is_valid():
            form.save()

            messages.success(
                request,
                "Terms and Conditions draft updated successfully."
            )

            return redirect("documents:terms_management")
    else:
        form = DocumentTermsForm(instance=terms)

    return render(
        request,
        "documents/terms_form.html",
        {
            "form": form,
            "page_title": "Edit Terms and Conditions",
        },
    )


@login_required
@transaction.atomic
def terms_publish(request, terms_id):
    if not request.user.is_staff:
        raise PermissionDenied

    if request.method != "POST":
        return redirect("documents:terms_management")

    terms = get_object_or_404(
        DocumentTerms.objects.select_for_update(),
        pk=terms_id,
    )

    if terms.is_published:
        messages.info(
            request,
            "These terms are already published."
        )
        return redirect("documents:terms_management")

    # Unpublish the previously published version.
    DocumentTerms.objects.filter(
        is_published=True
    ).exclude(
        pk=terms.pk
    ).update(is_published=False)

    # Publish the selected version.
    terms.is_published = True
    terms.published_at = timezone.now()
    terms.save(
        update_fields=["is_published", "published_at"]
    )

    messages.success(
        request,
        f"Terms version {terms.version} published successfully."
    )

    return redirect("documents:terms_management")

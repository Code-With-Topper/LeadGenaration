from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from leads.models import Lead

from .models import Activity

# How many cards to show per pipeline column. A column is a preview, not a
# place to render 10,000 leads.
CARDS_PER_COLUMN = 12


@login_required
def pipeline(request):
    """
    The pipeline board, one column per status the client uses.

    Each column shows its true total but only the first few cards, with a link
    to the full filtered list. That keeps the board instant at any database
    size.
    """
    from django.db.models import Count

    totals = dict(Lead.objects.values_list('status').annotate(n=Count('id')))

    columns = []
    for status, label in Lead.STATUS_CHOICES:
        columns.append({
            'status': status,
            'label': label,
            'total': totals.get(status, 0),
            # Each card shows the company's location, which reads a plant:
            # without the prefetch that is one query per card.
            'leads': Lead.objects.select_related('company')
                                 .prefetch_related('company__plants')
                                 .filter(status=status)[:CARDS_PER_COLUMN],
            'more': max(totals.get(status, 0) - CARDS_PER_COLUMN, 0),
        })

    return render(request, 'crm/pipeline.html', {
        'columns': columns,
        'status_choices': Lead.STATUS_CHOICES,
        'total': sum(totals.values()),
    })


@login_required
@require_POST
def update_status(request, lead_id):
    """
    Move a lead to another status.

    Moving to Profile Sent goes through `mark_profile_sent`, so the follow-up
    reminder is scheduled by the same code every time rather than being set by
    hand and forgotten.
    """
    lead = get_object_or_404(Lead, id=lead_id)
    new_status = request.POST.get('status')

    if new_status not in dict(Lead.STATUS_CHOICES):
        return _respond(request, False, 'That is not a valid status.', lead)

    old_status = lead.get_status_display()
    if new_status == lead.status:
        return _respond(request, True, 'No change.', lead)

    if new_status == Lead.PROFILE_SENT:
        lead.mark_profile_sent()
    else:
        lead.status = new_status
        # Closing a lead clears its reminder: nobody should be chased after
        # they have converted or said no.
        if new_status in Lead.CLOSED_STATUSES:
            lead.follow_up_date = None
        lead.save(update_fields=['status', 'follow_up_date', 'updated_at'])

    Activity.objects.create(
        lead=lead, action='Status changed',
        description=f'{old_status} → {lead.get_status_display()}',
        performed_by=request.user if request.user.is_authenticated else None)

    from reports.utils import log_audit
    log_audit('Status Changed', 'Lead', lead.id,
              f'{old_status} → {lead.get_status_display()}', request.user)

    return _respond(request, True,
                    f'Moved to {lead.get_status_display()}.', lead)


@login_required
@require_POST
def add_note(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    note = (request.POST.get('note') or '').strip()
    if not note:
        messages.error(request, 'Write something first.')
    else:
        Activity.objects.create(
            lead=lead, action='Note', description=note,
            performed_by=request.user if request.user.is_authenticated else None)
        messages.success(request, 'Note added.')
    return redirect('leads:detail', lead_id=lead.id)


@login_required
def activity_log(request):
    activities = Activity.objects.select_related('lead__company', 'performed_by')
    page = Paginator(activities, settings.PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'crm/activity_log.html', {
        'page_obj': page, 'activities': page.object_list,
    })


def _respond(request, ok, message, lead):
    """Answer JSON for the board's drag-and-drop, HTML for a normal form post."""
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'success': ok, 'message': message,
            'status': lead.status, 'status_display': lead.get_status_display(),
            'follow_up_date': lead.follow_up_date.isoformat()
                              if lead.follow_up_date else None,
        })
    (messages.success if ok else messages.error)(request, message)
    return redirect(request.META.get('HTTP_REFERER') or 'crm:pipeline')

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from leads.models import Lead

from .models import FollowUp


@login_required
def dashboard(request):
    """
    Everything waiting on the client today.

    Two sources: the automatic reminders on leads (the 7/10-day rule) and the
    tasks scheduled by hand. Both are shown, because a client who has to look
    in two places will miss one of them.
    """
    today = timezone.localdate()

    due_leads = Lead.objects.select_related('company').filter(
        follow_up_date__isnull=False,
    ).exclude(status__in=Lead.CLOSED_STATUSES)

    overdue_leads = due_leads.filter(follow_up_date__lt=today)
    today_leads = due_leads.filter(follow_up_date=today)
    upcoming_leads = due_leads.filter(
        follow_up_date__gt=today,
        follow_up_date__lte=today + timedelta(days=14))

    tasks = FollowUp.objects.select_related('lead__company', 'assigned_to')

    return render(request, 'followups/dashboard.html', {
        'today': today,
        'overdue_leads': overdue_leads[:50],
        'overdue_count': overdue_leads.count(),
        'today_leads': today_leads[:50],
        'today_count': today_leads.count(),
        'upcoming_leads': upcoming_leads[:50],
        'upcoming_count': upcoming_leads.count(),
        'overdue_tasks': tasks.filter(status='PENDING', date__lt=today)[:25],
        'today_tasks': tasks.filter(status='PENDING', date=today)[:25],
        'upcoming_tasks': tasks.filter(status='PENDING', date__gt=today)[:25],
        'done_tasks': tasks.filter(status='COMPLETED')[:15],
        'delay_days': settings.FOLLOW_UP_DELAY_DAYS,
    })


@login_required
@require_POST
def create(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)

    # Parse the date rather than handing the string straight to the model:
    # an unparsed string reaches the database intact but breaks any code that
    # then tries to format or compare it.
    date = parse_date((request.POST.get('date') or '').strip())
    if date is None:
        messages.error(request, 'Pick a valid date for the follow-up.')
        return redirect('leads:detail', lead_id=lead.id)

    task = FollowUp.objects.create(
        lead=lead,
        date=date,
        time=(request.POST.get('time') or None),
        follow_up_type=request.POST.get('type') or 'CALL',
        notes=request.POST.get('notes') or '',
        assigned_to=request.user if request.user.is_authenticated else None,
    )

    # Keep the lead's own reminder aligned with the earliest open task.
    if not lead.follow_up_date or task.date < lead.follow_up_date:
        lead.follow_up_date = task.date
        lead.save(update_fields=['follow_up_date', 'updated_at'])

    from reports.utils import log_audit
    log_audit('Follow-up Scheduled', 'FollowUp', task.id,
              f'{task.get_follow_up_type_display()} on {date}', request.user)

    messages.success(request, f'Follow-up scheduled for {task.date:%d %b %Y}.')
    return redirect('leads:detail', lead_id=lead.id)


@login_required
@require_POST
def complete(request, followup_id):
    task = get_object_or_404(FollowUp, id=followup_id)
    task.status = 'COMPLETED'
    task.completed_at = timezone.now()
    task.save(update_fields=['status', 'completed_at'])
    messages.success(request, 'Follow-up marked done.')
    return redirect(request.META.get('HTTP_REFERER') or 'followups:dashboard')


@login_required
@require_POST
def snooze(request, lead_id):
    """
    Push a lead's reminder further out.

    Without this the only way to clear a Follow-up Due lead is to change its
    status, which loses the fact that it still needs chasing later.
    """
    lead = get_object_or_404(Lead, id=lead_id)
    try:
        days = max(1, min(int(request.POST.get('days') or 7), 365))
    except ValueError:
        days = settings.FOLLOW_UP_DELAY_DAYS

    lead.follow_up_date = timezone.localdate() + timedelta(days=days)
    lead.follow_up_count += 1

    if lead.follow_up_count >= settings.FOLLOW_UP_MAX_ROUNDS:
        lead.remarks = (lead.remarks + f'\nChased {lead.follow_up_count} times '
                                       f'with no reply.').strip()

    # Leaving it as Follow-up Due would mean it shows as overdue tomorrow.
    if lead.status == Lead.FOLLOW_UP_DUE:
        lead.status = Lead.PROFILE_SENT if lead.profile_sent else Lead.CALLED

    lead.save(update_fields=['follow_up_date', 'follow_up_count', 'status',
                             'remarks', 'updated_at'])
    messages.success(request,
                     f'Reminder moved to {lead.follow_up_date:%d %b %Y}.')
    return redirect(request.META.get('HTTP_REFERER') or 'followups:dashboard')


@login_required
def history(request):
    tasks = FollowUp.objects.select_related('lead__company').exclude(status='PENDING')
    page = Paginator(tasks, settings.PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'followups/history.html', {
        'page_obj': page, 'tasks': page.object_list,
    })

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from leads.models import Lead, Suppression

from .models import EmailLog, EmailQuota, EmailTemplate
from .services import check_can_send, send_to_lead


@login_required
def compose(request, lead_id):
    """Write and send one email to one lead."""
    lead = get_object_or_404(
        Lead.objects.select_related('company', 'contact'), id=lead_id)
    allowed, reason = check_can_send(lead)

    if request.method == 'POST':
        if not allowed:
            messages.error(request, reason)
            return redirect('leads:detail', lead_id=lead.id)

        template_id = request.POST.get('template')
        template = (EmailTemplate.objects.filter(id=template_id).first()
                    if template_id else None)

        result = send_to_lead(
            lead,
            subject=(request.POST.get('subject') or '').strip(),
            body=request.POST.get('message') or '',
            attachment=request.FILES.get('attachment'),
            template=template,
            user=request.user,
            mark_profile_sent=request.POST.get('mark_profile_sent') == 'on',
        )

        if result.ok:
            from reports.utils import log_audit
            log_audit('Email Sent', 'EmailLog', result.log.id,
                      f"To {result.log.recipient}: {result.log.subject}",
                      request.user)
            messages.success(request, result.message)
        else:
            messages.error(request, result.message)
        return redirect('leads:detail', lead_id=lead.id)

    templates = EmailTemplate.objects.all()
    selected = templates.filter(is_default=True).first() or templates.first()
    subject = body = ''
    if selected:
        subject, body = selected.render(lead)

    return render(request, 'emails/compose.html', {
        'lead': lead,
        'templates': templates,
        'selected_template': selected,
        'subject': subject,
        'body': body,
        'can_send': allowed,
        'reason': reason,
        'quota_used': EmailQuota.used_today(),
        'quota_limit': EmailQuota.limit(),
        'quota_left': EmailQuota.remaining_today(),
    })


@login_required
def template_preview(request, lead_id, template_id):
    """Fill a template for this lead — used when the user switches template."""
    from django.http import JsonResponse
    lead = get_object_or_404(Lead, id=lead_id)
    template = get_object_or_404(EmailTemplate, id=template_id)
    subject, body = template.render(lead)
    return JsonResponse({'subject': subject, 'body': body})


@login_required
def log_list(request):
    """Every email ever attempted, newest first."""
    from django.core.paginator import Paginator

    logs = EmailLog.objects.select_related('lead__company').all()
    status = request.GET.get('status')
    if status in dict(EmailLog.STATUS_CHOICES):
        logs = logs.filter(status=status)

    page = Paginator(logs, settings.PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'emails/log_list.html', {
        'page_obj': page,
        'logs': page.object_list,
        'status': status or '',
        'status_choices': EmailLog.STATUS_CHOICES,
        'quota_used': EmailQuota.used_today(),
        'quota_limit': EmailQuota.limit(),
        'quota_left': EmailQuota.remaining_today(),
        'total_sent': EmailLog.objects.filter(status='SENT').count(),
    })


@login_required
def template_list(request):
    templates = EmailTemplate.objects.all()
    return render(request, 'emails/template_list.html', {'templates': templates})


@login_required
def template_edit(request, template_id=None):
    template = (get_object_or_404(EmailTemplate, id=template_id)
                if template_id else None)

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        subject = (request.POST.get('subject') or '').strip()
        body = request.POST.get('body') or ''
        if not (name and subject and body):
            messages.error(request, 'Name, subject and message are all required.')
        else:
            is_default = request.POST.get('is_default') == 'on'
            if template is None:
                template = EmailTemplate(); 
            template.name, template.subject, template.body = name, subject, body
            template.is_default = is_default
            template.save()
            if is_default:
                EmailTemplate.objects.exclude(id=template.id).update(is_default=False)
            messages.success(request, f"Template '{name}' saved.")
            return redirect('emails:template_list')

    return render(request, 'emails/template_form.html', {
        'template': template,
        'placeholders': EmailTemplate.PLACEHOLDERS,
    })


@login_required
@require_POST
def template_delete(request, template_id):
    template = get_object_or_404(EmailTemplate, id=template_id)
    name = template.name
    template.delete()
    messages.success(request, f"Template '{name}' deleted.")
    return redirect('emails:template_list')


@login_required
def suppression_list(request):
    """The do-not-contact list, so the client can see and manage it."""
    from django.core.paginator import Paginator

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip()
        if Suppression.add_email(email, reason='MANUAL', note='Added by user'):
            messages.success(request, f"{email} will no longer be emailed.")
        else:
            messages.error(request, 'Enter a valid email address.')
        return redirect('emails:suppression_list')

    entries = Suppression.objects.all()
    page = Paginator(entries, settings.PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'emails/suppression_list.html', {
        'page_obj': page, 'entries': page.object_list,
        'total': entries.count(),
    })


def unsubscribe(request, token):
    """
    A working unsubscribe.

    The address is written to the suppression list, which `check_can_send`
    consults before every send — so this actually stops future email rather
    than only showing a confirmation page.
    """
    try:
        data = signing.loads(token, salt='unsubscribe', max_age=60 * 60 * 24 * 365 * 5)
        email = data['email']
    except signing.BadSignature:
        return render(request, 'emails/unsubscribed.html',
                      {'ok': False,
                       'message': 'This unsubscribe link is not valid.'})

    Suppression.add_email(email, reason='UNSUBSCRIBED',
                          note='Unsubscribed via email link')

    # Close any open lead using this address, so it also leaves the pipeline.
    closed = 0
    for lead in Lead.objects.filter(company__normalized_email=email) \
                            .exclude(status__in=Lead.CLOSED_STATUSES):
        lead.status = Lead.NOT_RELEVANT
        lead.remarks = (lead.remarks + '\nUnsubscribed by recipient.').strip()
        lead.follow_up_date = None
        lead.save(update_fields=['status', 'remarks', 'follow_up_date', 'updated_at'])
        closed += 1

    return render(request, 'emails/unsubscribed.html', {
        'ok': True,
        'email': email,
        'company_name': settings.COMPANY_NAME,
        'message': f"{email} has been removed. You will not receive any more "
                   f"emails from us.",
        'closed': closed,
    })

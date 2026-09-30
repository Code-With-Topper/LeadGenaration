"""
Numbers the top bar and sidebar show on every page.

Kept to three cheap COUNT queries: these badges are what make the client
notice work that is waiting, so they have to be on every screen, and they have
to stay fast at 10,000 leads.
"""
from django.conf import settings
from django.utils import timezone


def crm(request):
    base = {'company_name': getattr(settings, 'COMPANY_NAME', '')}

    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return base

    from emails.models import EmailQuota
    from leads.models import DuplicateReview, Lead

    today = timezone.localdate()
    base.update({
        'nav_pending_reviews': DuplicateReview.objects.filter(
            status='PENDING').count(),
        'nav_followups_due': Lead.objects.filter(
            follow_up_date__lte=today, follow_up_date__isnull=False,
        ).exclude(status__in=Lead.CLOSED_STATUSES).count(),
        'nav_email_left': EmailQuota.remaining_today(),
        'nav_email_limit': EmailQuota.limit(),
    })
    return base

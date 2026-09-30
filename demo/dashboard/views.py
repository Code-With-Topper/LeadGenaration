from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from leads.models import DuplicateReview, Lead


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard-index')

    if request.method == 'POST':
        user = authenticate(request,
                            username=(request.POST.get('username') or '').strip(),
                            password=request.POST.get('password') or '')
        if user is not None:
            login(request, user)
            return redirect(request.GET.get('next') or 'dashboard-index')
        messages.error(request, 'Wrong username or password.')

    return render(request, 'dashboard/login.html')


def logout_view(request):
    logout(request)
    messages.info(request, 'You have been signed out.')
    return redirect('login')


@login_required
def index(request):
    """
    The dashboard, showing exactly the tiles the client listed — no more.

    Every count is a single aggregate query, so the page is as fast with
    10,000 leads as with ten.
    """
    counts = dict(
        Lead.objects.values_list('status').annotate(n=Count('id'))
    )
    total = sum(counts.values())

    tiles = [
        {'key': 'total', 'label': 'Total Leads', 'count': total,
         'url': '/leads/', 'colour': 'primary', 'icon': 'database'},
    ]
    palette = {
        Lead.NEW: ('info', 'user-plus'),
        Lead.CALLED: ('secondary', 'phone-call'),
        Lead.PROFILE_SENT: ('warning', 'send'),
        Lead.FOLLOW_UP_DUE: ('danger', 'bell'),
        Lead.REQUIREMENT_RECEIVED: ('primary', 'file-text'),
        Lead.CONVERTED: ('success', 'check-circle'),
        Lead.NOT_RELEVANT: ('dark', 'x-circle'),
    }
    for status, label in Lead.STATUS_CHOICES:
        colour, icon = palette[status]
        tiles.append({
            'key': status, 'label': label, 'count': counts.get(status, 0),
            'url': f'/leads/?status={status}', 'colour': colour, 'icon': icon,
        })

    today = timezone.localdate()
    from emails.models import EmailLog, EmailQuota

    # Follow-ups that are actually late, not just scheduled.
    overdue = Lead.objects.filter(
        follow_up_date__lt=today, follow_up_date__isnull=False
    ).exclude(status__in=Lead.CLOSED_STATUSES).count()

    due_this_week = Lead.objects.filter(
        follow_up_date__gte=today,
        follow_up_date__lte=today + timedelta(days=7),
    ).exclude(status__in=Lead.CLOSED_STATUSES).count()

    return render(request, 'dashboard/index.html', {
        'tiles': tiles,
        'total_leads': total,
        'overdue_followups': overdue,
        'due_this_week': due_this_week,
        'pending_reviews': DuplicateReview.objects.filter(status='PENDING').count(),
        'quota_used': EmailQuota.used_today(),
        'quota_limit': EmailQuota.limit(),
        'quota_left': EmailQuota.remaining_today(),
        'emails_total': EmailLog.objects.filter(status='SENT').count(),
        'recent_emails': EmailLog.objects.select_related('lead__company')
                                         .filter(status='SENT')[:5],
        'attention_leads': Lead.objects.select_related('company').filter(
            follow_up_date__lte=today, follow_up_date__isnull=False
        ).exclude(status__in=Lead.CLOSED_STATUSES)[:8],
        'weak_leads': Lead.objects.select_related('company').filter(
            company__data_quality_score__lt=settings.MIN_QUALITY_TO_CONTACT
        ).count(),
    })


def public_home(request):
    if request.user.is_authenticated:
        return redirect('dashboard-index')
    return render(request, 'dashboard/public/home.html')


def privacy_policy(request):
    return render(request, 'dashboard/public/privacy.html')


def terms(request):
    return render(request, 'dashboard/public/terms.html')


def robots(request):
    """
    Keep the CRM out of search results.

    Everything behind the login is private business data; only the public
    pages should ever be indexed.
    """
    lines = [
        'User-agent: *',
        'Disallow: /dashboard/',
        'Disallow: /leads/',
        'Disallow: /crm/',
        'Disallow: /emails/',
        'Disallow: /imports/',
        'Disallow: /reports/',
        'Disallow: /followups/',
        'Disallow: /quotations/',
        'Disallow: /lead-generation/',
        'Disallow: /admin/',
        'Allow: /$',
        f'Sitemap: {settings.SITE_URL}/sitemap.xml',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain')


def sitemap(request):
    base = settings.SITE_URL
    paths = ['/', '/privacy-policy/', '/terms/']
    urls = ''.join(f'<url><loc>{base}{path}</loc></url>' for path in paths)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'{urls}</urlset>')
    return HttpResponse(xml, content_type='application/xml')

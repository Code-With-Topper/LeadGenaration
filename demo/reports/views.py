from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q
from django.shortcuts import render

from emails.models import EmailLog, EmailQuota
from leads.models import Company, DuplicateReview, Lead, Plant, Suppression

from .models import AuditLog


@login_required
def dashboard(request):
    """
    Where the data came from and how good it is.

    Every number here is queried, not hardcoded — the previous version showed
    a duplicate count of zero regardless of reality, which is worse than
    showing nothing.
    """
    total_leads = Lead.objects.count()
    total_companies = Company.objects.count()

    quality_buckets = {
        'A': Company.objects.filter(data_quality_score__gte=80).count(),
        'B': Company.objects.filter(data_quality_score__gte=60,
                                    data_quality_score__lt=80).count(),
        'C': Company.objects.filter(data_quality_score__gte=40,
                                    data_quality_score__lt=60).count(),
        'D': Company.objects.filter(data_quality_score__lt=40).count(),
    }

    reachable = Company.objects.exclude(
        Q(normalized_email='') & Q(normalized_phone='')).count()

    return render(request, 'reports/dashboard.html', {
        'lead_stats': {
            'leads': total_leads,
            'companies': total_companies,
            'reachable': reachable,
            'unreachable': total_companies - reachable,
            'mx_verified': Company.objects.filter(email_status='MX_OK').count(),
            'with_email': Company.objects.exclude(normalized_email='').count(),
            'with_phone': Company.objects.exclude(normalized_phone='').count(),
            'with_statutory': Company.objects.exclude(
                Q(cin='') & Q(gstin='')).count(),
            'average_score': round(
                Company.objects.aggregate(a=Avg('data_quality_score'))['a'] or 0),
        },
        'quality_buckets': quality_buckets,
        'duplicate_stats': {
            'auto_merged': Company.objects.annotate(
                n=Count('source_history')).filter(leads__isnull=False).count(),
            'pending_review': DuplicateReview.objects.filter(
                status='PENDING').count(),
            'resolved': DuplicateReview.objects.exclude(status='PENDING').count(),
            'merged': DuplicateReview.objects.filter(status='MERGED').count(),
            'not_duplicate': DuplicateReview.objects.filter(
                status='NOT_DUPLICATE').count(),
        },
        'status_data': [
            {'label': label,
             'count': Lead.objects.filter(status=status).count()}
            for status, label in Lead.STATUS_CHOICES
        ],
        'industry_data': Company.objects.exclude(industry='')
                                        .values('industry')
                                        .annotate(count=Count('id'))
                                        .order_by('-count')[:15],
        'district_data': Plant.objects.exclude(district='')
                                      .values('district')
                                      .annotate(count=Count('company', distinct=True))
                                      .order_by('-count')[:20],
        'source_data': Lead.objects.exclude(lead_source='')
                                   .values('lead_source')
                                   .annotate(count=Count('id'))
                                   .order_by('-count'),
        'email_stats': {
            'sent': EmailLog.objects.filter(status='SENT').count(),
            'failed': EmailLog.objects.filter(status='FAILED').count(),
            'blocked': EmailLog.objects.filter(status='BLOCKED').count(),
            'today': EmailQuota.used_today(),
            'limit': EmailQuota.limit(),
            'unsubscribed': Suppression.objects.count(),
        },
    })


@login_required
def audit_logs(request):
    logs = AuditLog.objects.select_related('performed_by')

    action = (request.GET.get('action') or '').strip()
    if action:
        logs = logs.filter(action__icontains=action)

    page = Paginator(logs, settings.PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'reports/audit_logs.html', {
        'page_obj': page,
        'logs': page.object_list,
        'action': action,
        'actions': AuditLog.objects.values_list('action', flat=True).distinct(),
    })

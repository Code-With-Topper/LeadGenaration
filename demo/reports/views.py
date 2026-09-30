from django.shortcuts import render
from django.db.models import Count, Q
from leads.models import Lead, Company
from emails.models import EmailLog, EmailCampaign

def dashboard(request):
    # 1. Lead Report
    total_leads = Lead.objects.count()
    valid_leads = Lead.objects.filter(data_verified=True).count()
    incomplete_leads = total_leads - valid_leads
    # Temporary fallback: since we merged duplicates on import, explicit duplicate count is 0
    duplicate_leads = 0
    
    # 2. Industry Breakdown
    industry_data = Company.objects.values('industry').annotate(count=Count('id')).exclude(industry='').order_by('-count')
    
    # 3. Location Breakdown (via Plants)
    state_data = Company.objects.values('plants__state').annotate(count=Count('id', distinct=True)).exclude(plants__state='').exclude(plants__state__isnull=True).order_by('-count')
    # Rename 'plants__state' key to 'state' for the template
    state_data = [{'state': item['plants__state'], 'count': item['count']} for item in state_data]

    city_data = Company.objects.values('plants__city').annotate(count=Count('id', distinct=True)).exclude(plants__city='').exclude(plants__city__isnull=True).order_by('-count')
    city_data = [{'city': item['plants__city'], 'count': item['count']} for item in city_data]
    
    # 4. CRM Pipeline
    pipeline_data = Lead.objects.values('status').annotate(count=Count('id')).order_by('-count')
    # Let's map it explicitly for the template
    crm_stats = {
        'NEW': 0, 'CONTACTED': 0, 'INTERESTED': 0, 
        'REQUIREMENT_RECEIVED': 0, 'QUOTATION_SENT': 0, 
        'NEGOTIATION': 0, 'CONVERTED': 0, 'LOST': 0
    }
    for p in pipeline_data:
        if p['status'] in ['NOT_INTERESTED', 'WRONG_CONTACT', 'INVALID', 'CLOSED_LOST']:
            crm_stats['LOST'] += p['count']
        elif p['status'] in crm_stats:
            crm_stats[p['status']] = p['count']
            
    # 5. Email Performance
    manual_emails = EmailLog.objects.filter(campaign__isnull=True).count()
    auto_emails = EmailLog.objects.filter(campaign__isnull=False).count()
    sent_emails = EmailLog.objects.filter(status='SENT').count()
    failed_emails = EmailLog.objects.filter(status='FAILED').count()
    total_campaigns = EmailCampaign.objects.count()
    
    context = {
        'lead_stats': {
            'total': total_leads,
            'valid': valid_leads,
            'incomplete': incomplete_leads,
            'duplicates': duplicate_leads
        },
        'industry_data': industry_data,
        'state_data': state_data,
        'city_data': city_data,
        'crm_stats': crm_stats,
        'email_stats': {
            'manual': manual_emails,
            'auto': auto_emails,
            'sent': sent_emails,
            'failed': failed_emails,
            'campaigns': total_campaigns
        }
    }
    return render(request, 'reports/dashboard.html', context)

def audit_logs(request):
    from .models import AuditLog
    logs = AuditLog.objects.all().order_by('-created_at')[:500]
    return render(request, 'reports/audit_logs.html', {'logs': logs})

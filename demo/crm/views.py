from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from leads.models import Lead

def pipeline_view(request):
    # Fetch all leads and group by status
    leads = Lead.objects.select_related('company').all()
    
    pipeline = {
        'NEW': leads.filter(status='NEW'),
        'CONTACTED': leads.filter(status='CONTACTED'),
        'INTERESTED': leads.filter(status='INTERESTED'),
        'REQUIREMENT_RECEIVED': leads.filter(status='REQUIREMENT_RECEIVED'),
        'QUOTATION_SENT': leads.filter(status='QUOTATION_SENT'),
        'NEGOTIATION': leads.filter(status='NEGOTIATION'),
        'CONVERTED': leads.filter(status='CONVERTED'),
        'LOST_OR_INVALID': leads.filter(status__in=['NOT_INTERESTED', 'WRONG_CONTACT', 'INVALID', 'CLOSED_LOST'])
    }
    
    return render(request, 'crm/pipeline.html', {'pipeline': pipeline})

def update_lead_status(request, lead_id):
    if request.method == 'POST':
        lead = get_object_or_404(Lead, id=lead_id)
        old_status = lead.status
        new_status = request.POST.get('status')
        if new_status in dict(Lead.STATUS_CHOICES):
            lead.status = new_status
            lead.save()
            
            # Audit log
            from reports.utils import log_audit
            log_audit(
                action='Status Changed',
                model_name='Lead',
                object_id=lead.id,
                changes=f"Changed status from {old_status} to {new_status}",
                user=request.user if request.user.is_authenticated else None
            )
            
            return JsonResponse({'success': True, 'status': new_status})
        return JsonResponse({'success': False, 'error': 'Invalid status'})
    return JsonResponse({'success': False, 'error': 'POST required'})

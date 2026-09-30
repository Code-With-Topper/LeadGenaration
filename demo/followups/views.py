from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from .models import FollowUp
from leads.models import Lead

def dashboard(request):
    today_date = timezone.now().date()
    
    # Calculate metrics based on `date`
    followups = FollowUp.objects.all().select_related('lead', 'company', 'assigned_to')
    
    today = followups.filter(date=today_date, status='PENDING')
    overdue = followups.filter(date__lt=today_date, status='PENDING')
    upcoming = followups.filter(date__gt=today_date, status='PENDING')
    completed = followups.filter(status='COMPLETED').order_by('-completed_at')[:50]
    
    context = {
        'today_followups': today,
        'overdue_followups': overdue,
        'upcoming_followups': upcoming,
        'completed_followups': completed,
    }
    return render(request, 'followups/dashboard.html', context)

def create_followup(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    if request.method == 'POST':
        date = request.POST.get('date')
        time = request.POST.get('time') or None
        type = request.POST.get('type')
        notes = request.POST.get('notes')
        
        f = FollowUp.objects.create(
            lead=lead,
            company=lead.company,
            date=date,
            time=time,
            follow_up_type=type,
            notes=notes,
            status='PENDING'
        )
        
        from reports.utils import log_audit
        log_audit(
            action='Follow-up Created',
            model_name='FollowUp',
            object_id=f.id,
            changes=f"Scheduled for {date} {time}",
            user=request.user if request.user.is_authenticated else None
        )
        
        # Advance status if it's currently lower
        if lead.status in ['NEW', 'CONTACTED']:
            lead.status = 'FOLLOW_UP'
            lead.save()
            
        messages.success(request, f"Follow-up scheduled for {date}.")
        return redirect('leads:detail', lead_id=lead.id)
    return redirect('leads:detail', lead_id=lead.id)

def complete_followup(request, followup_id):
    followup = get_object_or_404(FollowUp, id=followup_id)
    followup.status = 'COMPLETED'
    followup.completed_at = timezone.now()
    followup.save()
    messages.success(request, "Follow-up marked as completed.")
    return redirect(request.META.get('HTTP_REFERER', 'followups:dashboard'))

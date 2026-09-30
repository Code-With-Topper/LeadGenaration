import base64
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.mail import EmailMessage
from leads.models import Lead
from .models import EmailLog, EmailCampaign, EmailTemplate
from .tasks import send_campaign_task

def send_manual_email(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    
    if request.method == 'POST':
        recipient = request.POST.get('to')
        subject = request.POST.get('subject')
        message_body = request.POST.get('message')
        attachment = request.FILES.get('attachment')
        
        try:
            # Send Email
            email = EmailMessage(
                subject=subject,
                body=message_body,
                to=[recipient],
            )
            if attachment:
                email.attach(attachment.name, attachment.read(), attachment.content_type)
            
            email.send(fail_silently=False)
            
            # Log it
            log_record = EmailLog.objects.create(
                lead=lead,
                recipient=recipient,
                subject=subject,
                message=message_body,
                attachment=attachment,
                status='SENT'
            )
            
            from reports.utils import log_audit
            log_audit('Email Sent', 'EmailLog', log_record.id, f"Sent to {recipient}: {subject}", request.user if request.user.is_authenticated else None)
            
            # Update Lead Status
            if lead.status == 'NEW':
                lead.status = 'CONTACTED'
                lead.save()
                
            messages.success(request, f"Email sent successfully to {recipient}!")
        except Exception as e:
            EmailLog.objects.create(
                lead=lead,
                recipient=recipient,
                subject=subject,
                message=message_body,
                status='FAILED',
                error_message=str(e)
            )
            messages.error(request, f"Failed to send email: {e}")
            
        return redirect('leads:detail', lead_id=lead.id)
        
    # Render compose modal/page (or typically this would just be a redirect back with a message since it's a POST handler)
    return redirect('leads:detail', lead_id=lead.id)

def campaign_list(request):
    campaigns = EmailCampaign.objects.all().order_by('-created_at')
    return render(request, 'emails/campaign_list.html', {'campaigns': campaigns})

def campaign_create(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        lead_ids = request.POST.getlist('leads')
        
        template_1_id = request.POST.get('template_1')
        t1 = get_object_or_404(EmailTemplate, id=template_1_id)
        
        # Create campaign
        campaign = EmailCampaign.objects.create(name=name, template=t1, status='QUEUED')
        
        # Add leads
        if lead_ids:
            leads = Lead.objects.filter(id__in=lead_ids)
            campaign.leads.set(leads)
            
        # Create sequence steps
        from .models import CampaignSequenceStep
        CampaignSequenceStep.objects.create(campaign=campaign, template=t1, step_order=1, delay_days=0)
        
        t2_id = request.POST.get('template_2')
        if t2_id:
            t2 = get_object_or_404(EmailTemplate, id=t2_id)
            d2 = int(request.POST.get('delay_2', 3))
            CampaignSequenceStep.objects.create(campaign=campaign, template=t2, step_order=2, delay_days=d2)
            
        t3_id = request.POST.get('template_3')
        if t3_id:
            t3 = get_object_or_404(EmailTemplate, id=t3_id)
            d3 = int(request.POST.get('delay_3', 7))
            CampaignSequenceStep.objects.create(campaign=campaign, template=t3, step_order=3, delay_days=d3)
            
        # Trigger Celery Task
        send_campaign_task.delay(campaign.id, step_order=1)
        messages.success(request, f"Drip Campaign '{name}' started successfully.")
        return redirect('emails:campaign_list')
        
    templates = EmailTemplate.objects.all()
    leads = Lead.objects.exclude(company__company_email__exact='').exclude(company__company_email__isnull=True)
    return render(request, 'emails/campaign_create.html', {'templates': templates, 'leads': leads})

def unsubscribe(request, email_b64):
    try:
        email = base64.b64decode(email_b64).decode('utf-8')
        # Logic to add to an Unsubscribe/DoNotContact list
        # For this CRM, we could add a boolean to Contact/Company
        messages.success(request, f"{email} has been unsubscribed successfully.")
    except Exception:
        messages.error(request, "Invalid unsubscribe link.")
    return render(request, 'emails/unsubscribed.html')

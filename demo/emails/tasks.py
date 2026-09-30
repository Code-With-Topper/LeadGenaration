import time
import base64
from celery import shared_task
from django.core.mail import EmailMessage
from django.conf import settings
from .models import EmailCampaign, EmailLog, CampaignSequenceStep

@shared_task
def send_campaign_task(campaign_id, step_order=1):
    try:
        campaign = EmailCampaign.objects.get(id=campaign_id)
    except EmailCampaign.DoesNotExist:
        return

    # If the campaign is manually stopped, abort
    if campaign.status == 'STOPPED':
        return

    campaign.status = 'RUNNING'
    campaign.save()
    
    # Get the current step
    step = campaign.steps.filter(step_order=step_order).first()
    
    # If no step exists, we might be using the legacy single-template campaign
    if not step:
        if step_order == 1 and campaign.template:
            template = campaign.template
        else:
            # End of campaign
            campaign.status = 'COMPLETED'
            campaign.save()
            return f"Campaign {campaign.name} complete."
    else:
        template = step.template

    leads = campaign.leads.all()
    success_count = 0
    fail_count = 0

    for lead in leads:
        # AUTOMATION STOP CONDITIONS
        # Do not send if lead is CONVERTED, LOST, or opted out (invalid)
        if lead.status in ['CONVERTED', 'LOST', 'INVALID', 'CLOSED_LOST', 'NOT_INTERESTED', 'WRONG_CONTACT']:
            continue
            
        recipient = lead.company.company_email
        if not recipient or recipient == "NOT FOUND":
            continue
            
        try:
            # Generate unsubscribe link
            b64_email = base64.b64encode(recipient.encode('utf-8')).decode('utf-8')
            unsub_link = f"http://127.0.0.1:8000/emails/unsubscribe/{b64_email}/"
            
            # Fetch latest quotation if exists
            quotation = lead.company.quotations.order_by('-created_at').first() if hasattr(lead.company, 'quotations') else None
            q_number = quotation.quotation_number if quotation else "N/A"
            
            # Fetch primary contact name if exists
            contact = lead.company.contacts.first()
            c_name = contact.name if contact and contact.name != 'NOT FOUND' else "Valued Client"
            
            # Personalize subject and body
            subject = template.subject
            body = template.body
            
            for text in [subject, body]:
                if text == subject:
                    subject = subject.replace("{{company_name}}", lead.company.company_name)
                    subject = subject.replace("{{contact_name}}", c_name)
                    subject = subject.replace("{{industry}}", lead.company.industry or "your industry")
                    subject = subject.replace("{{quotation_number}}", str(q_number))
                    subject = subject.replace("{{sender_name}}", campaign.created_by.get_full_name() if campaign.created_by else "Sales Team")
                else:
                    body = body.replace("{{company_name}}", lead.company.company_name)
                    body = body.replace("{{contact_name}}", c_name)
                    body = body.replace("{{industry}}", lead.company.industry or "your industry")
                    body = body.replace("{{quotation_number}}", str(q_number))
                    body = body.replace("{{sender_name}}", campaign.created_by.get_full_name() if campaign.created_by else "Sales Team")
                    body = body.replace("{{unsubscribe_link}}", unsub_link)
            
            email = EmailMessage(
                subject=subject,
                body=body,
                to=[recipient],
            )
            email.send(fail_silently=False)
            
            EmailLog.objects.create(
                lead=lead,
                campaign=campaign,
                step=step,
                recipient=recipient,
                subject=subject,
                message=body,
                status='SENT'
            )
            success_count += 1
            
            # Anti-spam compliance: wait 2 seconds between emails
            time.sleep(2)
            
        except Exception as e:
            EmailLog.objects.create(
                lead=lead,
                campaign=campaign,
                step=step,
                recipient=recipient,
                subject=subject,
                message=template.body,
                status='FAILED',
                error_message=str(e)
            )
            fail_count += 1

    # Check for next step
    next_step = campaign.steps.filter(step_order=step_order + 1).first()
    if next_step:
        # Schedule the next step execution
        delay_seconds = next_step.delay_days * 86400
        # For demonstration purposes, if delay_days is 0, we can run it shortly. 
        # But normally delay_days > 0 for a drip campaign.
        send_campaign_task.apply_async(args=[campaign.id, next_step.step_order], countdown=delay_seconds)
    else:
        campaign.status = 'COMPLETED'
        campaign.save()

    return f"Campaign {campaign.name} Step {step_order} complete. Success: {success_count}, Failed: {fail_count}"

from django.db import models
from leads.models import Lead
from django.contrib.auth.models import User

class EmailTemplate(models.Model):
    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    body = models.TextField(help_text="Use {{ company_name }} or {{ unsubscribe_link }} as placeholders.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class EmailCampaign(models.Model):
    name = models.CharField(max_length=255)
    template = models.ForeignKey(EmailTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    leads = models.ManyToManyField(Lead, related_name='campaigns')
    status = models.CharField(max_length=50, default='DRAFT')
    scheduled_for = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class CampaignSequenceStep(models.Model):
    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name='steps')
    template = models.ForeignKey(EmailTemplate, on_delete=models.CASCADE)
    step_order = models.IntegerField()
    delay_days = models.IntegerField(default=0, help_text="Days to wait before sending this step")
    
    class Meta:
        ordering = ['step_order']

    def __str__(self):
        return f"{self.campaign.name} - Step {self.step_order}"

class EmailLog(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='email_logs')
    campaign = models.ForeignKey(EmailCampaign, on_delete=models.SET_NULL, null=True, blank=True, related_name='logs')
    step = models.ForeignKey(CampaignSequenceStep, on_delete=models.SET_NULL, null=True, blank=True)
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    message = models.TextField()
    attachment = models.FileField(upload_to='emails/', blank=True, null=True)
    status = models.CharField(max_length=50, default='SENT')
    provider_id = models.CharField(max_length=255, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Email to {self.recipient}: {self.subject}"

from django.conf import settings
from django.db import models
from django.utils import timezone


class EmailTemplate(models.Model):
    """
    A reusable message. Placeholders are filled in at send time.

    Manual sending with a good template is as fast as automation at ten
    messages a day, and carries none of the deliverability risk.
    """
    PLACEHOLDERS = (
        '{{company_name}}', '{{contact_name}}', '{{industry}}', '{{city}}',
        '{{sender_name}}', '{{company}}', '{{unsubscribe_link}}',
    )

    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    body = models.TextField(
        help_text='Placeholders: ' + ', '.join(PLACEHOLDERS))
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_default', 'name']

    def __str__(self):
        return self.name

    def render(self, lead, request=None):
        """Fill the placeholders for one lead. Returns (subject, body)."""
        company = lead.company
        contact = lead.contact or company.contacts.exclude(name='').first()
        plant = company.primary_plant

        values = {
            '{{company_name}}': company.company_name,
            '{{contact_name}}': (contact.name if contact and contact.name
                                 else 'Sir/Madam'),
            '{{industry}}': company.industry or 'your industry',
            '{{city}}': (plant.city if plant and plant.city else 'your city'),
            '{{sender_name}}': getattr(settings, 'COMPANY_NAME', ''),
            '{{company}}': getattr(settings, 'COMPANY_NAME', ''),
            '{{unsubscribe_link}}': unsubscribe_url(lead.best_email),
        }
        subject, body = self.subject, self.body
        for token, value in values.items():
            subject = subject.replace(token, str(value))
            body = body.replace(token, str(value))
        return subject, body


class EmailLog(models.Model):
    """
    Every send attempt, successful or not.

    The client asked for "a record of emails sent"; a log that only holds
    successes is not a record.
    """
    STATUS_CHOICES = [
        ('SENT', 'Sent'),
        ('FAILED', 'Failed'),
        ('BLOCKED', 'Blocked'),      # suppressed, over quota, or closed lead
    ]

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE,
                             related_name='email_logs')
    template = models.ForeignKey(EmailTemplate, on_delete=models.SET_NULL,
                                 null=True, blank=True)
    recipient = models.CharField(max_length=254)
    subject = models.CharField(max_length=255)
    message = models.TextField()
    attachment = models.FileField(upload_to='emails/%Y/%m/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SENT')
    error_message = models.TextField(blank=True, default='')
    sent_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"{self.get_status_display()}: {self.recipient} — {self.subject}"


class EmailQuota(models.Model):
    """
    One row per day, holding how many messages have gone out.

    This is the client's "about 10 emails a day" turned into something the
    system enforces rather than something the user has to remember.
    """
    date = models.DateField(unique=True, default=timezone.localdate)
    sent_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.date}: {self.sent_count} sent"

    @classmethod
    def limit(cls):
        return getattr(settings, 'EMAIL_DAILY_LIMIT', 10)

    @classmethod
    def today(cls):
        obj, _ = cls.objects.get_or_create(date=timezone.localdate())
        return obj

    @classmethod
    def used_today(cls):
        return cls.today().sent_count

    @classmethod
    def remaining_today(cls):
        return max(cls.limit() - cls.used_today(), 0)

    @classmethod
    def can_send(cls):
        return cls.remaining_today() > 0

    @classmethod
    def record_send(cls, count=1):
        """
        Increment atomically, so two browser tabs cannot both slip through
        the last slot.
        """
        from django.db.models import F
        today = timezone.localdate()
        cls.objects.get_or_create(date=today)
        cls.objects.filter(date=today).update(sent_count=F('sent_count') + count)


def unsubscribe_url(email):
    """Absolute, signed unsubscribe link for an address."""
    from django.urls import reverse
    from django.core import signing

    if not email:
        return ''
    token = signing.dumps({'email': email}, salt='unsubscribe')
    path = reverse('emails:unsubscribe', kwargs={'token': token})
    return f"{settings.SITE_URL}{path}"

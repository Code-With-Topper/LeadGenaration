from django.db import models


class FollowUp(models.Model):
    """
    A dated task against a lead.

    The automatic 7/10-day reminder lives on the Lead itself
    (`Lead.follow_up_date`, swept daily by the worker). This model is for the
    extra ones the client schedules by hand — a call back on Tuesday, a site
    visit next month.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Done'),
        ('CANCELLED', 'Cancelled'),
    ]
    TYPE_CHOICES = [
        ('CALL', 'Phone call'),
        ('EMAIL', 'Email'),
        ('MEETING', 'Meeting'),
        ('VISIT', 'Site visit'),
        ('OTHER', 'Other'),
    ]

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE,
                             related_name='followups')
    date = models.DateField(db_index=True)
    time = models.TimeField(blank=True, null=True)
    follow_up_type = models.CharField(max_length=20, choices=TYPE_CHOICES,
                                      default='CALL')
    notes = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default='PENDING', db_index=True)
    assigned_to = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                    null=True, blank=True,
                                    related_name='assigned_followups')
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'time']
        indexes = [models.Index(fields=['status', 'date'])]

    def __str__(self):
        return f'{self.get_follow_up_type_display()} — {self.lead} on {self.date}'

    @property
    def company_name(self):
        return self.lead.company.company_name

    @property
    def is_overdue(self):
        from django.utils import timezone
        return self.status == 'PENDING' and self.date < timezone.localdate()

from django.db import models


class Activity(models.Model):
    """A dated trail of what happened on a lead, and who did it."""

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE,
                             related_name='activities')
    action = models.CharField(max_length=100)
    description = models.TextField(blank=True, default='')
    performed_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                     null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'activities'

    def __str__(self):
        return f'{self.action} — {self.lead.company.company_name}'

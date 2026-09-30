from django.db import models
from leads.models import Lead, Company
from django.contrib.auth.models import User

class FollowUp(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='followups', null=True, blank=True)
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='followups')
    
    date = models.DateField()
    time = models.TimeField(blank=True, null=True)
    follow_up_type = models.CharField(max_length=50, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='PENDING')
    
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_followups')
    next_follow_up = models.DateTimeField(blank=True, null=True)
    
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Follow up for {self.lead} on {self.date}"

from django.db import models
from leads.models import Company

class GenerationJob(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('RUNNING', 'Running'),
        ('PAUSED', 'Paused'),
        ('STOPPED', 'Stopped'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    name = models.CharField(max_length=255, blank=True, null=True)
    state = models.CharField(max_length=100)
    district = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    industry = models.CharField(max_length=100)
    keywords = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    is_headless = models.BooleanField(default=True)
    
    started_at = models.DateTimeField(blank=True, null=True)
    paused_at = models.DateTimeField(blank=True, null=True)
    stopped_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    
    current_query = models.CharField(max_length=255, blank=True, null=True)
    current_website = models.URLField(max_length=500, blank=True, null=True)
    
    websites_found = models.IntegerField(default=0)
    leads_found = models.IntegerField(default=0)
    valid_leads = models.IntegerField(default=0)
    duplicate_leads = models.IntegerField(default=0)
    invalid_leads = models.IntegerField(default=0)
    needs_review = models.IntegerField(default=0)
    
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Job #{self.id} - {self.city}, {self.industry} ({self.status})"

class Industry(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class SearchKeyword(models.Model):
    industry = models.ForeignKey(Industry, on_delete=models.CASCADE, related_name='keywords')
    keyword = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.industry.name} - {self.keyword}"

class DuplicateResolution(models.Model):
    RESOLUTION_CHOICES = [
        ('PENDING', 'Pending'),
        ('SKIPPED', 'Skipped'),
        ('UPDATED', 'Updated'),
        ('MERGED', 'Merged'),
    ]

    job = models.ForeignKey(GenerationJob, on_delete=models.CASCADE, related_name='duplicates')
    existing_company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='duplicate_resolutions')
    scraped_data = models.JSONField()
    status = models.CharField(max_length=20, choices=RESOLUTION_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Duplicate Review for Job #{self.job_id} vs Company {self.existing_company.company_name}"

from django.db import models

class Company(models.Model):
    company_name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, blank=True, null=True)
    industry = models.CharField(max_length=100, blank=True, null=True)
    company_type = models.CharField(max_length=100, blank=True, null=True)
    website = models.URLField(max_length=255, blank=True, null=True)
    normalized_domain = models.CharField(max_length=255, blank=True, null=True)
    company_email = models.EmailField(blank=True, null=True)
    company_phone = models.CharField(max_length=50, blank=True, null=True)
    cin = models.CharField(max_length=50, blank=True, null=True)
    gstin = models.CharField(max_length=50, blank=True, null=True)
    linkedin_url = models.URLField(blank=True, null=True)
    google_maps_url = models.TextField(blank=True, null=True)
    verification_status = models.CharField(max_length=50, default='NEW')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.company_name

class Plant(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='plants')
    plant_name = models.CharField(max_length=255, blank=True, null=True)
    plant_address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    district = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    pin_code = models.CharField(max_length=20, blank=True, null=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.plant_name or 'Plant'} - {self.company.company_name}"

class Contact(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=255, blank=True, null=True)
    designation = models.CharField(max_length=100, blank=True, null=True)
    department = models.CharField(max_length=100, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    mobile = models.CharField(max_length=50, blank=True, null=True)
    direct_phone = models.CharField(max_length=50, blank=True, null=True)
    linkedin_url = models.URLField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    data_status = models.CharField(max_length=50, default='FOUND')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or str(self.email) or 'Unknown Contact'

class Lead(models.Model):
    STATUS_CHOICES = [
        ('NEW', 'New'),
        ('CONTACTED', 'Contacted'),
        ('INTERESTED', 'Interested'),
        ('REQUIREMENT_RECEIVED', 'Requirement Received'),
        ('QUOTATION_SENT', 'Quotation Sent'),
        ('NEGOTIATION', 'Negotiation'),
        ('CONVERTED', 'Converted'),
        ('NOT_INTERESTED', 'Not Interested'),
        ('WRONG_CONTACT', 'Wrong Contact'),
        ('INVALID', 'Invalid'),
        ('CLOSED_LOST', 'Closed / Lost'),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='leads')
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name='leads')
    lead_source = models.CharField(max_length=100, blank=True, null=True)
    source_query = models.TextField(blank=True, null=True)
    source_url = models.URLField(max_length=500, blank=True, null=True)
    generation_job_id = models.IntegerField(blank=True, null=True) # Will link to GenerationJob manually to avoid circular dependencies
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='NEW')
    data_verified = models.BooleanField(default=False)
    completeness_percentage = models.IntegerField(default=0)
    first_contact_date = models.DateField(blank=True, null=True)
    profile_sent = models.BooleanField(default=False)
    follow_up_date = models.DateField(blank=True, null=True)
    requirement = models.TextField(blank=True, null=True)
    quotation = models.TextField(blank=True, null=True)
    remarks = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Lead: {self.company.company_name} ({self.status})"

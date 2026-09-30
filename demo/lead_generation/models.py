from django.db import models


class GenerationJob(models.Model):
    """
    One lead-collection run.

    State lives in the database rather than in the worker process, so a
    restart cannot lose a job and the user can always see and control it.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Waiting to start'),
        ('RUNNING', 'Running'),
        ('PAUSED', 'Paused'),
        ('STOPPED', 'Stopped'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]
    ACTIVE_STATUSES = ('PENDING', 'RUNNING', 'PAUSED')
    FINISHED_STATUSES = ('COMPLETED', 'FAILED', 'STOPPED')

    name = models.CharField(max_length=255, blank=True, default='')
    state = models.CharField(max_length=100)
    district = models.CharField(max_length=100, blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    industry = models.CharField(max_length=100)
    keywords = models.TextField(help_text='Comma separated search keywords')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default='PENDING', db_index=True)
    # Upper bound on websites to visit, so a run always ends.
    max_websites = models.PositiveIntegerField(default=50)

    started_at = models.DateTimeField(blank=True, null=True)
    paused_at = models.DateTimeField(blank=True, null=True)
    stopped_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    # Updated as the worker runs; a stale heartbeat means the worker died.
    heartbeat_at = models.DateTimeField(blank=True, null=True)

    current_query = models.CharField(max_length=255, blank=True, default='')
    current_website = models.CharField(max_length=500, blank=True, default='')

    websites_found = models.IntegerField(default=0)
    leads_found = models.IntegerField(default=0)
    valid_leads = models.IntegerField(default=0)
    duplicate_leads = models.IntegerField(default=0)
    rejected_leads = models.IntegerField(default=0)
    needs_review = models.IntegerField(default=0)

    error_message = models.TextField(blank=True, default='')
    log = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        where = self.city or self.district or self.state
        return f"Job #{self.id} - {self.industry} in {where} ({self.status})"

    @property
    def keyword_list(self):
        return [k.strip() for k in (self.keywords or '').split(',') if k.strip()]

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def scope(self):
        """Human description of how wide this run searches."""
        if self.city:
            return f"{self.city}, {self.district or self.state}"
        if self.district:
            return f"all of {self.district} district"
        return f"all districts of {self.state}"

    @property
    def progress_percent(self):
        if not self.max_websites:
            return 0
        return min(int(self.websites_found / self.max_websites * 100), 100)

    def note(self, message):
        """Append a line to the job log the user can read on the page."""
        from django.utils import timezone
        stamp = timezone.localtime().strftime('%H:%M:%S')
        lines = (self.log or '').splitlines()
        lines.append(f"[{stamp}] {message}")
        self.log = "\n".join(lines[-200:])


class Industry(models.Model):
    """
    Search vocabulary, editable by the user instead of hardcoded in a template.

    This is what lets the client add a new industry or keyword themselves
    rather than asking for a code change.
    """
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'industries'

    def __str__(self):
        return self.name


class SearchKeyword(models.Model):
    industry = models.ForeignKey(Industry, on_delete=models.CASCADE,
                                 related_name='keywords')
    keyword = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['keyword']
        unique_together = [('industry', 'keyword')]

    def __str__(self):
        return f"{self.industry.name} - {self.keyword}"


class District(models.Model):
    """
    Every district we can search, loaded from a fixture.

    The client asked for all of West Bengal. Hardcoding two districts in a
    template made that impossible, so the list lives in the database and
    covers all 23.
    """
    state = models.CharField(max_length=100, db_index=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['state', 'name']
        unique_together = [('state', 'name')]

    def __str__(self):
        return f"{self.name}, {self.state}"


class City(models.Model):
    """
    Known industrial towns inside a district.

    Optional: a run can target a whole district, so the client is never
    blocked by a town missing from this list.
    """
    district = models.ForeignKey(District, on_delete=models.CASCADE,
                                 related_name='cities')
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        unique_together = [('district', 'name')]
        verbose_name_plural = 'cities'

    def __str__(self):
        return f"{self.name}, {self.district.name}"

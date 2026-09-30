from django.db import models
from django.utils import timezone


class Company(models.Model):
    """
    The golden record: one row per real company.

    Raw values are kept in the display fields. Matching only ever runs on the
    normalized_* columns, which is why they are indexed and, where they can
    identify a company on their own, unique.
    """

    IDENTITY_CHOICES = [
        ('UNVERIFIED', 'Unverified'),
        ('TITLE_GUESS', 'Name guessed from page title'),
        ('SITE_CONFIRMED', 'Name confirmed on company website'),
        ('GST_MATCHED', 'Matched to GSTIN'),
        ('ROC_MATCHED', 'Matched to MCA/ROC record'),
    ]
    EMAIL_STATUS_CHOICES = [
        ('UNKNOWN', 'Not checked'),
        ('SYNTAX_OK', 'Format valid'),
        ('MX_OK', 'Domain accepts mail'),
        ('NO_MX', 'Domain rejects mail'),
        ('BOUNCED', 'Bounced'),
    ]
    PHONE_STATUS_CHOICES = [
        ('UNKNOWN', 'Not checked'),
        ('PLAUSIBLE', 'Valid format'),
        ('MOBILE_OK', 'Valid mobile'),
        ('LANDLINE_OK', 'Valid landline'),
        ('TOLLFREE_OK', 'Toll free'),
        ('INTL_OK', 'Valid international'),
        ('WRONG', 'Wrong number'),
    ]

    # --- display values -------------------------------------------------
    company_name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True, default='')
    industry = models.CharField(max_length=100, blank=True, default='')
    company_type = models.CharField(max_length=100, blank=True, default='')
    website = models.URLField(max_length=500, blank=True, default='')
    company_email = models.EmailField(max_length=254, blank=True, default='')
    company_phone = models.CharField(max_length=50, blank=True, default='')
    cin = models.CharField(max_length=21, blank=True, default='', db_index=True)
    gstin = models.CharField(max_length=15, blank=True, default='', db_index=True)
    linkedin_url = models.URLField(max_length=500, blank=True, default='')
    google_maps_url = models.TextField(blank=True, default='')

    # --- matching keys --------------------------------------------------
    normalized_name = models.CharField(max_length=255, blank=True, default='',
                                       db_index=True)
    normalized_domain = models.CharField(max_length=255, blank=True, default='',
                                         db_index=True)
    normalized_email = models.CharField(max_length=254, blank=True, default='',
                                        db_index=True)
    normalized_phone = models.CharField(max_length=20, blank=True, default='',
                                        db_index=True)

    # --- what we actually know -----------------------------------------
    identity_status = models.CharField(max_length=20, choices=IDENTITY_CHOICES,
                                       default='UNVERIFIED')
    email_status = models.CharField(max_length=20, choices=EMAIL_STATUS_CHOICES,
                                    default='UNKNOWN')
    phone_status = models.CharField(max_length=20, choices=PHONE_STATUS_CHOICES,
                                    default='UNKNOWN')
    data_quality_score = models.PositiveSmallIntegerField(default=0, db_index=True)

    # --- provenance -----------------------------------------------------
    source = models.CharField(max_length=100, blank=True, default='')
    source_history = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'companies'
        ordering = ['-created_at']
        constraints = [
            # The database is the last line of defence: even a bug in the
            # dedupe code cannot create two companies with the same identity.
            models.UniqueConstraint(
                fields=['cin'], condition=~models.Q(cin=''),
                name='uniq_company_cin'),
            models.UniqueConstraint(
                fields=['gstin'], condition=~models.Q(gstin=''),
                name='uniq_company_gstin'),
            models.UniqueConstraint(
                fields=['normalized_domain'], condition=~models.Q(normalized_domain=''),
                name='uniq_company_domain'),
            models.UniqueConstraint(
                fields=['normalized_email'], condition=~models.Q(normalized_email=''),
                name='uniq_company_email'),
            models.UniqueConstraint(
                fields=['normalized_phone'], condition=~models.Q(normalized_phone=''),
                name='uniq_company_phone'),
        ]

    def __str__(self):
        return self.company_name

    @property
    def quality_band(self):
        """('A', 'Ready to contact') — read from the stored score, no query."""
        from core.quality import band
        return band(self.data_quality_score)

    @property
    def primary_plant(self):
        """
        The main plant, reading the prefetch cache when there is one.

        `plants.first()` issues its own query even after prefetch_related, so
        on a 50-row list page that is 50 extra queries. Checking the cache
        first keeps a list page at a fixed query count.
        """
        cached = getattr(self, '_prefetched_objects_cache', {}).get('plants')
        if cached is not None:
            return cached[0] if cached else None
        return self.plants.first()

    @property
    def primary_contact(self):
        """The best-named contact, again preferring the prefetch cache."""
        cached = getattr(self, '_prefetched_objects_cache', {}).get('contacts')
        if cached is not None:
            named = [c for c in cached if c.name]
            return named[0] if named else (cached[0] if cached else None)
        return (self.contacts.exclude(name='').first()
                or self.contacts.first())

    @property
    def location(self):
        plant = self.primary_plant
        if not plant:
            return ''
        parts = [plant.city, plant.district, plant.state]
        return ', '.join(p for p in parts if p)

    @property
    def open_lead(self):
        """The lead a list row should link to, from the prefetch cache."""
        cached = getattr(self, '_prefetched_objects_cache', {}).get('leads')
        if cached is not None:
            return cached[0] if cached else None
        return self.leads.first()

    def add_source(self, label, url=''):
        """Append to the provenance trail without losing earlier sources."""
        entry = {'source': label, 'url': url,
                 'seen': timezone.now().strftime('%Y-%m-%d %H:%M')}
        history = list(self.source_history or [])
        if not any(h.get('url') == url and h.get('source') == label
                   for h in history):
            history.append(entry)
            self.source_history = history[-20:]


class Plant(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE,
                                related_name='plants')
    plant_name = models.CharField(max_length=255, blank=True, default='')
    plant_address = models.TextField(blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='', db_index=True)
    district = models.CharField(max_length=100, blank=True, default='',
                                db_index=True)
    state = models.CharField(max_length=100, blank=True, default='', db_index=True)
    pin_code = models.CharField(max_length=6, blank=True, default='', db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True,
                                   null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True,
                                    null=True)
    is_primary = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_primary', 'id']

    def __str__(self):
        return f"{self.plant_name or 'Plant'} - {self.company.company_name}"

    @property
    def full_address(self):
        parts = [self.plant_address, self.city, self.district, self.state,
                 self.pin_code]
        return ', '.join(p for p in parts if p)


class Contact(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE,
                                related_name='contacts')
    name = models.CharField(max_length=255, blank=True, default='')
    designation = models.CharField(max_length=100, blank=True, default='')
    department = models.CharField(max_length=100, blank=True, default='')
    email = models.EmailField(max_length=254, blank=True, default='')
    normalized_email = models.CharField(max_length=254, blank=True, default='',
                                        db_index=True)
    mobile = models.CharField(max_length=20, blank=True, default='')
    direct_phone = models.CharField(max_length=20, blank=True, default='')
    linkedin_url = models.URLField(max_length=500, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_primary', 'id']

    def __str__(self):
        return self.name or self.email or self.mobile or 'Unknown contact'


class Lead(models.Model):
    """
    The client's own workflow, in the client's own words.

    These seven statuses are exactly the list given in the requirement, in the
    order the client works through them. Nothing extra: an unused status is a
    button the client has to think about for no reason.
    """

    NEW = 'NEW'
    CALLED = 'CALLED'
    PROFILE_SENT = 'PROFILE_SENT'
    FOLLOW_UP_DUE = 'FOLLOW_UP_DUE'
    REQUIREMENT_RECEIVED = 'REQUIREMENT_RECEIVED'
    CONVERTED = 'CONVERTED'
    NOT_RELEVANT = 'NOT_RELEVANT'

    STATUS_CHOICES = [
        (NEW, 'New Lead'),
        (CALLED, 'Called'),
        (PROFILE_SENT, 'Profile Sent'),
        (FOLLOW_UP_DUE, 'Follow-up Due'),
        (REQUIREMENT_RECEIVED, 'Requirement Received'),
        (CONVERTED, 'Converted'),
        (NOT_RELEVANT, 'Not Relevant'),
    ]

    # Statuses that end the conversation — never email these again.
    CLOSED_STATUSES = (CONVERTED, NOT_RELEVANT)

    company = models.ForeignKey(Company, on_delete=models.CASCADE,
                                related_name='leads')
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='leads')

    status = models.CharField(max_length=30, choices=STATUS_CHOICES,
                              default=NEW, db_index=True)

    lead_source = models.CharField(max_length=100, blank=True, default='')
    source_query = models.TextField(blank=True, default='')
    source_url = models.URLField(max_length=500, blank=True, default='')
    generation_job_id = models.IntegerField(blank=True, null=True, db_index=True)
    import_job_id = models.IntegerField(blank=True, null=True, db_index=True)

    # Workflow dates the client asked for.
    first_contact_date = models.DateField(blank=True, null=True)
    profile_sent = models.BooleanField(default=False)
    profile_sent_at = models.DateField(blank=True, null=True)
    follow_up_date = models.DateField(blank=True, null=True, db_index=True)
    follow_up_count = models.PositiveSmallIntegerField(default=0)
    last_contacted_at = models.DateTimeField(blank=True, null=True)

    requirement = models.TextField(blank=True, default='')
    quotation = models.TextField(blank=True, default='')
    remarks = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['follow_up_date', 'status']),
        ]

    def __str__(self):
        return f"Lead: {self.company.company_name} ({self.get_status_display()})"

    @property
    def is_closed(self):
        return self.status in self.CLOSED_STATUSES

    @property
    def best_email(self):
        """Where an email to this lead should actually go."""
        if self.contact and self.contact.normalized_email:
            return self.contact.normalized_email
        return self.company.normalized_email

    @property
    def is_follow_up_overdue(self):
        from django.utils import timezone as tz
        return bool(
            self.follow_up_date
            and not self.is_closed
            and self.follow_up_date <= tz.localdate()
        )

    def mark_profile_sent(self, when=None, delay_days=None):
        """
        Record that the company profile went out, and schedule the follow-up.

        This is the client's "7 or 10 day reminder": the date is set here, and
        a daily job flips the lead to Follow-up Due when it arrives.
        """
        from datetime import timedelta
        from django.conf import settings

        when = when or timezone.localdate()
        if delay_days is None:
            delay_days = getattr(settings, 'FOLLOW_UP_DELAY_DAYS', 7)

        self.profile_sent = True
        self.profile_sent_at = when
        self.follow_up_date = when + timedelta(days=delay_days)
        if not self.first_contact_date:
            self.first_contact_date = when
        if self.status in (self.NEW, self.CALLED):
            self.status = self.PROFILE_SENT
        self.save(update_fields=[
            'profile_sent', 'profile_sent_at', 'follow_up_date',
            'first_contact_date', 'status', 'updated_at',
        ])


class Suppression(models.Model):
    """
    Do-not-contact list.

    An unsubscribe has to survive re-imports and re-scrapes, so it is stored
    against the address itself rather than against a lead. Checked before
    every single send.
    """
    REASON_CHOICES = [
        ('UNSUBSCRIBED', 'Unsubscribed'),
        ('BOUNCED', 'Hard bounced'),
        ('COMPLAINED', 'Marked as spam'),
        ('MANUAL', 'Added manually'),
    ]

    email = models.CharField(max_length=254, blank=True, default='', db_index=True)
    phone = models.CharField(max_length=20, blank=True, default='', db_index=True)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES,
                              default='UNSUBSCRIBED')
    note = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['email'], condition=~models.Q(email=''),
                                    name='uniq_suppression_email'),
            models.UniqueConstraint(fields=['phone'], condition=~models.Q(phone=''),
                                    name='uniq_suppression_phone'),
        ]

    def __str__(self):
        return f"{self.email or self.phone} ({self.get_reason_display()})"

    @classmethod
    def blocks_email(cls, email):
        from core.normalize import normalize_email
        value = normalize_email(email)
        return bool(value) and cls.objects.filter(email=value).exists()

    @classmethod
    def add_email(cls, email, reason='UNSUBSCRIBED', note=''):
        from core.normalize import normalize_email
        value = normalize_email(email)
        if not value:
            return None
        obj, _ = cls.objects.get_or_create(
            email=value, defaults={'reason': reason, 'note': note})
        return obj


class DuplicateReview(models.Model):
    """
    A pair the system is not confident enough to merge on its own.

    Detection without resolution is not a feature, so every row here is
    actionable from a mobile-friendly screen: keep, replace, or merge.
    Lives in `leads` rather than in one of the two ingest apps because both
    the scraper and the importer raise these.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Waiting for review'),
        ('KEPT_EXISTING', 'Kept existing record'),
        ('USED_NEW', 'Replaced with new data'),
        ('MERGED', 'Merged'),
        ('NOT_DUPLICATE', 'Saved as a separate company'),
    ]

    existing_company = models.ForeignKey(Company, on_delete=models.CASCADE,
                                         related_name='duplicate_reviews')
    incoming = models.JSONField()
    match_score = models.PositiveSmallIntegerField(default=0)
    match_reason = models.CharField(max_length=255, blank=True, default='')
    origin = models.CharField(max_length=50, blank=True, default='')
    generation_job_id = models.IntegerField(blank=True, null=True, db_index=True)
    import_job_id = models.IntegerField(blank=True, null=True, db_index=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default='PENDING', db_index=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolved_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                    null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return (f"Review: {self.incoming.get('company_name', '?')} vs "
                f"{self.existing_company.company_name} ({self.match_score}%)")

    @property
    def is_pending(self):
        return self.status == 'PENDING'

    def field_comparison(self):
        """
        Rows for the side-by-side review screen.

        Only fields where the two records actually disagree are shown — the
        client should not have to read 30 identical rows to find the one that
        differs.
        """
        company = self.existing_company
        plant = company.primary_plant
        pairs = [
            ('Company Name', company.company_name, self.incoming.get('company_name', '')),
            ('Industry', company.industry, self.incoming.get('industry', '')),
            ('Website', company.website, self.incoming.get('website', '')),
            ('Email', company.company_email, self.incoming.get('company_email', '')),
            ('Phone', company.company_phone, self.incoming.get('company_phone', '')),
            ('CIN', company.cin, self.incoming.get('cin', '')),
            ('GSTIN', company.gstin, self.incoming.get('gstin', '')),
            ('Address', plant.plant_address if plant else '',
             self.incoming.get('plant_address', '')),
            ('City', plant.city if plant else '', self.incoming.get('city', '')),
            ('District', plant.district if plant else '',
             self.incoming.get('district', '')),
            ('State', plant.state if plant else '', self.incoming.get('state', '')),
            ('PIN', plant.pin_code if plant else '', self.incoming.get('pin_code', '')),
            ('Contact Person', company.contacts.first().name if company.contacts.exists() else '',
             self.incoming.get('contact_name', '')),
        ]
        rows = []
        for label, existing, incoming in pairs:
            existing, incoming = existing or '', incoming or ''
            if not existing and not incoming:
                continue
            rows.append({
                'label': label,
                'existing': existing,
                'incoming': incoming,
                'differs': existing.strip().lower() != incoming.strip().lower(),
                'only_in_new': bool(incoming) and not existing,
            })
        return rows

"""
The only code that writes a lead into the database.

Both the scraper and the importer call `ingest()`. That is the whole point:
one write path means one set of rules for validation, duplicate handling and
merging, and no way for the two entry points to disagree.

Merge rule, applied everywhere:
    a validated value is never overwritten by an unvalidated one.
Blanks are filled, unverified values are upgraded to verified, and the new
source is appended to the company's history. Nothing good is ever lost.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import dedupe, quality
from .record import CleanRecord, build_record

CREATED, MERGED, REVIEW, REJECTED = "CREATED", "MERGED", "REVIEW", "REJECTED"

# An email domain proven to accept mail beats one we only pattern-matched.
EMAIL_RANK = {"UNKNOWN": 0, "NO_MX": 0, "BOUNCED": 0, "SYNTAX_OK": 1, "MX_OK": 2}
PHONE_RANK = {"UNKNOWN": 0, "WRONG": 0, "FORMAT_BAD": 0, "PLAUSIBLE": 1,
              "INTL_OK": 1, "TOLLFREE_OK": 1, "LANDLINE_OK": 2, "MOBILE_OK": 3}
IDENTITY_RANK = {"UNVERIFIED": 0, "TITLE_GUESS": 1, "SITE_CONFIRMED": 2,
                 "GST_MATCHED": 3, "ROC_MATCHED": 4}


@dataclass
class IngestOutcome:
    action: str                     # CREATED | MERGED | REVIEW | REJECTED
    company = None
    lead = None
    review = None
    reason: str = ""
    score: int = 0
    warnings: list = field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return self.action in (MERGED, REVIEW)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def ingest(raw: dict, *, origin: str, generation_job_id: int | None = None,
           import_job_id: int | None = None, check_mx: bool = False,
           require_reachability: bool = True,
           identity_status: str = "TITLE_GUESS",
           batch_index: dedupe.BatchIndex | None = None,
           row_number: int = 0) -> IngestOutcome:
    """
    Validate, de-duplicate and store one raw lead.

    `batch_index`, when given, also de-duplicates the record against earlier
    rows in the same file — without it a CSV holding the same company three
    times would create three companies, since none is in the database yet.
    """
    record = build_record(raw, check_mx=check_mx,
                          require_reachability=require_reachability)
    if record.rejected:
        return IngestOutcome(action=REJECTED, reason=record.reject_reason,
                             warnings=record.warnings)

    if batch_index is not None:
        seen, first_row, reason = batch_index.seen(record)
        if seen:
            return IngestOutcome(action=MERGED, reason=reason,
                                 warnings=record.warnings)
        batch_index.add(record, row_number)

    match = dedupe.decide(record)

    if match.decision == dedupe.NEW:
        return _create(record, origin=origin, identity_status=identity_status,
                       generation_job_id=generation_job_id,
                       import_job_id=import_job_id)

    if match.decision == dedupe.MERGE:
        return _merge(record, match.company, origin=origin,
                      identity_status=identity_status, reason=match.reason,
                      score=match.score)

    return _queue_review(record, match.company, origin=origin,
                         reason=match.reason, score=match.score,
                         generation_job_id=generation_job_id,
                         import_job_id=import_job_id)


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------

def _create(record: CleanRecord, *, origin: str, identity_status: str,
            generation_job_id, import_job_id) -> IngestOutcome:
    from leads.models import Company

    try:
        with transaction.atomic():
            company = Company(
                company_name=record.company_name,
                normalized_name=record.normalized_name,
                industry=record.industry,
                company_type=record.company_type,
                website=record.website,
                normalized_domain=record.normalized_domain,
                company_email=record.company_email,
                normalized_email=record.company_email,
                company_phone=record.company_phone,
                normalized_phone=record.company_phone,
                cin=record.cin,
                gstin=record.gstin,
                linkedin_url=record.linkedin_url,
                google_maps_url=record.google_maps_url,
                email_status=record.email_status if record.company_email else 'UNKNOWN',
                phone_status=record.phone_status if record.company_phone else 'UNKNOWN',
                identity_status=('GST_MATCHED' if record.gstin else
                                 'ROC_MATCHED' if record.cin else identity_status),
                source=origin,
            )
            company.add_source(origin, record.source_url)
            company.save()

            _write_plant(company, record)
            contact = _write_contact(company, record)
            _write_extra_contacts(company, record)
            lead = _write_lead(company, contact, record, origin=origin,
                               generation_job_id=generation_job_id,
                               import_job_id=import_job_id)
            quality.refresh(company)
    except IntegrityError as exc:
        # A unique constraint fired, which means another writer created this
        # company between our lookup and our insert. Treat it as the duplicate
        # it is rather than losing the row.
        existing, reason = dedupe.find_by_hard_key(record)
        if existing:
            return _merge(record, existing, origin=origin,
                          identity_status=identity_status,
                          reason=reason or 'unique key conflict', score=100)
        return IngestOutcome(action=REJECTED,
                             reason=f'could not save: {exc}',
                             warnings=record.warnings)

    outcome = IngestOutcome(action=CREATED, reason='new company',
                            warnings=record.warnings)
    outcome.company, outcome.lead = company, lead
    return outcome


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

def _merge(record: CleanRecord, company, *, origin: str, identity_status: str,
           reason: str, score: int) -> IngestOutcome:
    """Enrich an existing company. Never downgrades a value."""
    from leads.models import Company

    changed: list[str] = []

    def fill(field_name, value):
        """Set a field only when it is currently empty."""
        if value and not getattr(company, field_name):
            setattr(company, field_name, value)
            changed.append(field_name)

    with transaction.atomic():
        company = Company.objects.select_for_update().get(pk=company.pk)

        fill('industry', record.industry)
        fill('company_type', record.company_type)
        fill('linkedin_url', record.linkedin_url)
        fill('google_maps_url', record.google_maps_url)
        fill('legal_name', record.company_name
             if record.company_name != company.company_name else '')

        # Statutory identifiers are the strongest identity we can hold, but
        # only claim them when nobody else already does.
        for field_name, value in (('cin', record.cin), ('gstin', record.gstin)):
            if value and not getattr(company, field_name):
                clash = Company.objects.filter(**{field_name: value}) \
                                       .exclude(pk=company.pk).exists()
                if not clash:
                    setattr(company, field_name, value)
                    changed.append(field_name)

        # Website: fill a blank, or upgrade when we have a real domain.
        if record.normalized_domain and not company.normalized_domain:
            if not Company.objects.filter(normalized_domain=record.normalized_domain) \
                                  .exclude(pk=company.pk).exists():
                company.website = record.website
                company.normalized_domain = record.normalized_domain
                changed += ['website', 'normalized_domain']

        # Email: take it when we have none, or when ours is better verified.
        if record.company_email:
            incoming_rank = EMAIL_RANK.get(record.email_status, 0)
            current_rank = EMAIL_RANK.get(company.email_status, 0)
            better = incoming_rank > current_rank
            if not company.normalized_email or better:
                taken = Company.objects.filter(normalized_email=record.company_email) \
                                       .exclude(pk=company.pk).exists()
                if not taken:
                    company.company_email = record.company_email
                    company.normalized_email = record.company_email
                    company.email_status = record.email_status
                    changed += ['company_email', 'normalized_email', 'email_status']

        if record.company_phone:
            incoming_rank = PHONE_RANK.get(record.phone_status, 0)
            current_rank = PHONE_RANK.get(company.phone_status, 0)
            if not company.normalized_phone or incoming_rank > current_rank:
                taken = Company.objects.filter(normalized_phone=record.company_phone) \
                                       .exclude(pk=company.pk).exists()
                if not taken:
                    company.company_phone = record.company_phone
                    company.normalized_phone = record.company_phone
                    company.phone_status = record.phone_status
                    changed += ['company_phone', 'normalized_phone', 'phone_status']

        # Identity only ever moves up.
        new_identity = ('GST_MATCHED' if record.gstin else
                        'ROC_MATCHED' if record.cin else identity_status)
        if IDENTITY_RANK.get(new_identity, 0) > IDENTITY_RANK.get(company.identity_status, 0):
            company.identity_status = new_identity
            changed.append('identity_status')

        company.add_source(origin, record.source_url)
        company.save()

        _write_plant(company, record)
        contact = _write_contact(company, record)
        _write_extra_contacts(company, record)

        # Keep one open lead per company rather than piling up duplicates.
        lead = company.leads.exclude(status__in=('CONVERTED', 'NOT_RELEVANT')) \
                            .order_by('id').first()
        if lead is None:
            lead = company.leads.order_by('id').first()
        if lead is None:
            lead = _write_lead(company, contact, record, origin=origin,
                               generation_job_id=None, import_job_id=None)
        elif contact and not lead.contact:
            lead.contact = contact
            lead.save(update_fields=['contact', 'updated_at'])

        quality.refresh(company)

    detail = f"{reason}; enriched: {', '.join(sorted(set(changed))) or 'nothing new'}"
    outcome = IngestOutcome(action=MERGED, reason=detail, score=score,
                            warnings=record.warnings)
    outcome.company, outcome.lead = company, lead
    return outcome


# --------------------------------------------------------------------------
# Review queue
# --------------------------------------------------------------------------

def _queue_review(record: CleanRecord, company, *, origin: str, reason: str,
                  score: int, generation_job_id, import_job_id) -> IngestOutcome:
    from leads.models import DuplicateReview

    payload = {
        key: value for key, value in record.__dict__.items()
        if key not in ('rejected', 'reject_reason', 'warnings')
    }

    existing = DuplicateReview.objects.filter(
        existing_company=company, status='PENDING',
        incoming__company_name=record.company_name).first()
    if existing:
        outcome = IngestOutcome(action=REVIEW, score=score,
                                reason='already queued for review',
                                warnings=record.warnings)
        outcome.company, outcome.review = company, existing
        return outcome

    review = DuplicateReview.objects.create(
        existing_company=company,
        incoming=payload,
        match_score=score,
        match_reason=reason,
        origin=origin,
        generation_job_id=generation_job_id,
        import_job_id=import_job_id,
    )
    outcome = IngestOutcome(action=REVIEW, score=score,
                            reason=f'{reason} — needs your confirmation',
                            warnings=record.warnings)
    outcome.company, outcome.review = company, review
    return outcome


def apply_review(review, decision: str, user=None) -> str:
    """
    Carry out what the user chose on the duplicate review screen.

        merge     enrich the existing company with the new data
        existing  discard the new data
        new       overwrite the existing company's fields with the new data
        separate  save the incoming record as its own company
    """
    from leads.models import Company

    record = CleanRecord(**{
        key: value for key, value in review.incoming.items()
        if key in CleanRecord.__dataclass_fields__
    })

    if decision == 'merge':
        _merge(record, review.existing_company, origin=review.origin or 'review',
               identity_status='SITE_CONFIRMED', reason='merged by user', score=100)
        review.status = 'MERGED'

    elif decision == 'new':
        company = Company.objects.get(pk=review.existing_company_id)
        for attribute, value in (
            ('company_name', record.company_name),
            ('normalized_name', record.normalized_name),
            ('industry', record.industry),
            ('website', record.website),
            ('normalized_domain', record.normalized_domain),
            ('company_email', record.company_email),
            ('normalized_email', record.company_email),
            ('company_phone', record.company_phone),
            ('normalized_phone', record.company_phone),
        ):
            if value:
                setattr(company, attribute, value)
        company.email_status = record.email_status
        company.phone_status = record.phone_status
        company.save()
        _write_plant(company, record)
        _write_contact(company, record)
        quality.refresh(company)
        review.status = 'USED_NEW'

    elif decision == 'separate':
        _create(record, origin=review.origin or 'review',
                identity_status='SITE_CONFIRMED',
                generation_job_id=review.generation_job_id,
                import_job_id=review.import_job_id)
        review.status = 'NOT_DUPLICATE'

    else:
        review.status = 'KEPT_EXISTING'

    review.resolved_at = timezone.now()
    review.resolved_by = user if user and user.is_authenticated else None
    review.save(update_fields=['status', 'resolved_at', 'resolved_by'])
    return review.status


# --------------------------------------------------------------------------
# Related rows
# --------------------------------------------------------------------------

def _write_plant(company, record: CleanRecord):
    """One plant per (city, pin). Fills blanks on an existing row."""
    from leads.models import Plant

    if not any((record.city, record.district, record.state, record.pin_code,
                record.plant_address)):
        return None

    plant = None
    if record.pin_code:
        plant = company.plants.filter(pin_code=record.pin_code).first()
    if plant is None and record.city:
        plant = company.plants.filter(city__iexact=record.city).first()
    if plant is None and not company.plants.exists():
        plant = Plant(company=company, is_primary=True)

    if plant is None:
        plant = Plant(company=company, is_primary=False)

    for attribute, value in (
        ('plant_name', record.plant_name), ('plant_address', record.plant_address),
        ('city', record.city), ('district', record.district),
        ('state', record.state), ('pin_code', record.pin_code),
    ):
        if value and not getattr(plant, attribute):
            setattr(plant, attribute, value)

    if not plant.plant_name and plant.city:
        plant.plant_name = f"Main Plant - {plant.city}"
    plant.save()
    return plant


def _write_contact(company, record: CleanRecord):
    """
    The named contact person, if the record has one.

    Matched on email first, then mobile, then name, so re-importing the same
    sheet does not create a second copy of the same person.
    """
    from leads.models import Contact

    if not any((record.contact_name, record.contact_email, record.contact_mobile,
                record.contact_direct_phone)):
        return None

    contact = None
    if record.contact_email:
        contact = company.contacts.filter(normalized_email=record.contact_email).first()
    if contact is None and record.contact_mobile:
        contact = company.contacts.filter(mobile=record.contact_mobile).first()
    if contact is None and record.contact_name:
        contact = company.contacts.filter(name__iexact=record.contact_name).first()
    if contact is None:
        contact = Contact(company=company,
                          is_primary=not company.contacts.exists())

    for attribute, value in (
        ('name', record.contact_name), ('designation', record.designation),
        ('department', record.department), ('mobile', record.contact_mobile),
        ('direct_phone', record.contact_direct_phone),
    ):
        if value and not getattr(contact, attribute):
            setattr(contact, attribute, value)

    if record.contact_email and not contact.normalized_email:
        contact.email = record.contact_email
        contact.normalized_email = record.contact_email

    contact.save()
    return contact


def _write_extra_contacts(company, record: CleanRecord):
    """
    Store additional addresses and numbers found on the site.

    They have no name attached, so they are kept as unnamed contacts rather
    than promoted onto the company — the client can see them on the lead page
    without them polluting the primary contact details.
    """
    from leads.models import Contact

    for email in record.extra_emails[:5]:
        if not company.contacts.filter(normalized_email=email).exists():
            Contact.objects.create(company=company, email=email,
                                   normalized_email=email,
                                   notes='found on website')
    for phone in record.extra_phones[:5]:
        if not company.contacts.filter(mobile=phone).exists():
            Contact.objects.create(company=company, mobile=phone,
                                   notes='found on website')


def _write_lead(company, contact, record: CleanRecord, *, origin,
                generation_job_id, import_job_id):
    from leads.models import Lead

    return Lead.objects.create(
        company=company,
        contact=contact,
        status=Lead.NEW,
        lead_source=record.lead_source or origin,
        source_query=record.source_query,
        source_url=record.source_url,
        generation_job_id=generation_job_id,
        import_job_id=import_job_id,
        remarks=record.remarks,
    )

"""
CSV/Excel import.

Reading the file, mapping the columns and writing the rows are three separate
concerns, and the write goes through `core.ingest` — the same code path the
scraper uses. That is what makes the client's "no duplicate data, data
validation" true for imports as well as for scraped leads.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from core.dedupe import BatchIndex
from core.ingest import CREATED, MERGED, REJECTED, REVIEW, ingest

LOGGER = logging.getLogger('imports')

# Fields the user can map a spreadsheet column onto. Order is the order shown
# on the mapping screen; the label is what the client reads.
MAPPABLE_FIELDS = [
    ('company_name', 'Company Name', True),
    ('industry', 'Industry', False),
    ('company_type', 'Company Type', False),
    ('plant_name', 'Plant Name', False),
    ('plant_address', 'Plant Address', False),
    ('city', 'City', False),
    ('district', 'District', False),
    ('state', 'State', False),
    ('pin_code', 'PIN Code', False),
    ('website', 'Website', False),
    ('company_phone', 'Company Phone', False),
    ('company_email', 'Company Email', False),
    ('contact_name', 'Contact Person', False),
    ('designation', 'Designation', False),
    ('department', 'Department', False),
    ('contact_mobile', 'Mobile', False),
    ('contact_direct_phone', 'Direct Phone', False),
    ('contact_email', 'Contact Email', False),
    ('cin', 'CIN', False),
    ('gstin', 'GSTIN', False),
    ('linkedin_url', 'LinkedIn', False),
    ('google_maps_url', 'Google Maps', False),
    ('remarks', 'Remarks', False),
]

# Column headings we can match on our own, so most files need no manual
# mapping at all.
AUTO_MATCH = {
    'company_name': ('company', 'company name', 'companyname', 'firm',
                     'organisation', 'organization', 'name', 'party', 'client'),
    'industry': ('industry', 'sector', 'category', 'business type'),
    'company_type': ('company type', 'type', 'constitution'),
    'plant_name': ('plant', 'plant name', 'unit', 'branch', 'factory'),
    'plant_address': ('address', 'plant address', 'full address', 'location',
                      'street', 'addr'),
    'city': ('city', 'town', 'place'),
    'district': ('district', 'dist', 'dist.'),
    'state': ('state', 'province'),
    'pin_code': ('pin', 'pin code', 'pincode', 'postal code', 'zip', 'zipcode'),
    'website': ('website', 'web', 'url', 'site', 'web site', 'webpage'),
    'company_phone': ('phone', 'company phone', 'telephone', 'landline',
                      'contact no', 'contact number', 'office phone', 'tel'),
    'company_email': ('email', 'company email', 'e-mail', 'mail', 'email id',
                      'official email'),
    'contact_name': ('contact person', 'contact name', 'person', 'owner',
                     'proprietor', 'director', 'concerned person'),
    'designation': ('designation', 'title', 'role', 'position'),
    'department': ('department', 'dept'),
    'contact_mobile': ('mobile', 'cell', 'mobile no', 'mobile number', 'whatsapp'),
    'contact_direct_phone': ('direct phone', 'direct', 'direct line', 'extension'),
    'contact_email': ('contact email', 'personal email', 'person email'),
    'cin': ('cin', 'cin no', 'cin number', 'corporate identity number'),
    'gstin': ('gstin', 'gst', 'gst no', 'gst number', 'gstin number'),
    'linkedin_url': ('linkedin', 'linked in', 'linkedin url'),
    'google_maps_url': ('google maps', 'maps', 'map link', 'google map'),
    'remarks': ('remarks', 'notes', 'comment', 'comments', 'description'),
}


def read_dataframe(path, file_name, nrows=None):
    """Load a CSV or Excel file into a DataFrame with everything as text."""
    import pandas as pd

    lowered = str(file_name).lower()
    if lowered.endswith(('.xlsx', '.xls', '.xlsm')):
        frame = pd.read_excel(path, nrows=nrows, dtype=str)
    else:
        # utf-8-sig strips the BOM Excel writes into CSV files.
        try:
            frame = pd.read_csv(path, nrows=nrows, dtype=str,
                                encoding='utf-8-sig', skip_blank_lines=True)
        except UnicodeDecodeError:
            frame = pd.read_csv(path, nrows=nrows, dtype=str,
                                encoding='latin-1', skip_blank_lines=True)
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame.fillna('')


def suggest_mapping(columns) -> dict:
    """Guess which spreadsheet column belongs to which field."""
    mapping = {}
    used = set()
    lookup = {str(c).strip().lower(): c for c in columns}

    for field_name, aliases in AUTO_MATCH.items():
        for alias in aliases:
            column = lookup.get(alias)
            if column and column not in used:
                mapping[field_name] = column
                used.add(column)
                break
    # Second pass: substring match for headings like "Company E-Mail Address".
    for field_name, aliases in AUTO_MATCH.items():
        if field_name in mapping:
            continue
        for heading, column in lookup.items():
            if column in used:
                continue
            if any(alias in heading for alias in aliases):
                mapping[field_name] = column
                used.add(column)
                break
    return mapping


def row_to_raw(row, mapping: dict) -> dict:
    """Turn one spreadsheet row into the raw dict `core.ingest` expects."""
    raw = {}
    for field_name, column in mapping.items():
        if column and column in row:
            raw[field_name] = row[column]
    return raw


def run_import(job, *, dry_run: bool, progress=None):
    """
    Process every row of an import job.

    With dry_run=True nothing is written: the counts and the rejection reasons
    are computed inside a transaction that is rolled back, so the preview the
    user approves is exactly what the commit will do.
    """
    from django.db import transaction

    frame = read_dataframe(job.file_path.path, job.file_name)
    mapping = job.column_mapping or {}
    if not mapping.get('company_name'):
        raise ValueError('Company Name must be mapped before importing.')

    check_mx = getattr(settings, 'VALIDATE_EMAIL_MX', False) and not dry_run

    counts = {CREATED: 0, MERGED: 0, REVIEW: 0, REJECTED: 0}
    rejections: list[dict] = []
    warnings: list[dict] = []
    samples: list[dict] = []
    batch = BatchIndex()

    def process_all():
        for position, (_, row) in enumerate(frame.iterrows(), start=1):
            raw = row_to_raw(row, mapping)
            raw.setdefault('lead_source', 'File Import')
            raw['remarks'] = raw.get('remarks', '')

            outcome = ingest(
                raw,
                origin='File Import',
                import_job_id=job.id,
                check_mx=check_mx,
                # An imported row is trusted to name a real company; the
                # listicle guard is for scraped page titles.
                identity_status='SITE_CONFIRMED',
                # A spreadsheet of companies is still useful without contact
                # details — the client can research them later.
                require_reachability=False,
                batch_index=batch,
                row_number=position,
            )
            counts[outcome.action] = counts.get(outcome.action, 0) + 1

            if outcome.action == REJECTED and len(rejections) < 500:
                rejections.append({
                    'row': position,
                    'company': str(raw.get('company_name', ''))[:120],
                    'reason': outcome.reason,
                })
            # Imported, but something on the row had to be thrown away. The
            # company is kept — the client typed it, so it is real data — but
            # they must be told which fields did not survive validation.
            elif outcome.warnings and len(warnings) < 500:
                warnings.append({
                    'row': position,
                    'company': str(raw.get('company_name', ''))[:120],
                    'reason': '; '.join(outcome.warnings[:4]),
                })
            if len(samples) < 20:
                samples.append({
                    'row': position,
                    'company': str(raw.get('company_name', ''))[:120],
                    'action': outcome.action,
                    'reason': outcome.reason,
                    'warnings': outcome.warnings[:3],
                })
            if progress and position % 100 == 0:
                progress(position, len(frame))

    if dry_run:
        # Roll the whole thing back: a preview must not change the database.
        try:
            with transaction.atomic():
                process_all()
                raise _Rollback()
        except _Rollback:
            pass
    else:
        process_all()

    result = {
        'total_rows': len(frame),
        'created': counts[CREATED],
        'merged': counts[MERGED],
        'review': counts[REVIEW],
        'rejected': counts[REJECTED],
        'warned': len(warnings),
        'rejections': rejections,
        'warnings': warnings,
        'samples': samples,
    }

    job.total_rows = result['total_rows']
    job.created_rows = result['created']
    job.merged_rows = result['merged']
    job.review_rows = result['review']
    job.rejected_rows = result['rejected']
    job.warned_rows = result['warned']
    # Both lists go in the same download, so the client sees every row the
    # import did not take at face value.
    job.rejections = rejections + [dict(entry, kind='imported with problems')
                                   for entry in warnings]

    if dry_run:
        job.preview = result
        job.status = 'PREVIEWED'
    else:
        job.status = 'COMPLETED'
        job.completed_at = timezone.now()
    job.save()
    return result


class _Rollback(Exception):
    """Internal: unwinds the dry-run transaction."""

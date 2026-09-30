import csv

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from core import quality
from core.ingest import apply_review

from .models import Company, Contact, DuplicateReview, Lead, Plant

# Quality bands offered as a filter on the lead list.
BAND_RANGES = {'A': (80, 100), 'B': (60, 79), 'C': (40, 59), 'D': (0, 39)}


@login_required
def index(request):
    """
    The lead list.

    Paginated and filtered in the database, never in Python — this is what
    makes 10,000 leads usable, and usable on a phone.
    """
    leads = Lead.objects.select_related('company', 'contact') \
                        .prefetch_related('company__plants')

    search = (request.GET.get('q') or '').strip()
    status = request.GET.get('status') or ''
    band = request.GET.get('band') or ''
    district = (request.GET.get('district') or '').strip()

    if search:
        leads = leads.filter(
            Q(company__company_name__icontains=search)
            | Q(company__normalized_name__icontains=search)
            | Q(company__company_email__icontains=search)
            | Q(company__company_phone__icontains=search)
            | Q(company__normalized_phone__icontains=search)
            | Q(company__cin__icontains=search)
            | Q(company__gstin__icontains=search)
            | Q(contact__name__icontains=search)
        ).distinct()

    if status in dict(Lead.STATUS_CHOICES):
        leads = leads.filter(status=status)

    if band in BAND_RANGES:
        low, high = BAND_RANGES[band]
        leads = leads.filter(company__data_quality_score__gte=low,
                             company__data_quality_score__lte=high)

    if district:
        leads = leads.filter(company__plants__district__iexact=district).distinct()

    page = Paginator(leads, settings.PAGE_SIZE).get_page(request.GET.get('page'))

    return render(request, 'leads/index.html', {
        'page_obj': page,
        'leads': page.object_list,
        'search': search,
        'status': status,
        'band': band,
        'district': district,
        'status_choices': Lead.STATUS_CHOICES,
        'districts': Plant.objects.exclude(district='')
                                  .values_list('district', flat=True)
                                  .distinct().order_by('district'),
        'total': leads.count(),
        'pending_reviews': DuplicateReview.objects.filter(status='PENDING').count(),
    })


@login_required
def detail(request, lead_id):
    """One lead: everything known, and everything that can be done with it."""
    lead = get_object_or_404(
        Lead.objects.select_related('company', 'contact'), id=lead_id)
    company = lead.company

    from emails.models import EmailQuota
    from emails.services import check_can_send
    can_send, send_reason = check_can_send(lead)

    return render(request, 'leads/detail.html', {
        'lead': lead,
        'company': company,
        'plants': company.plants.all(),
        'contacts': company.contacts.all(),
        # EmailLog points at the lead, so read it from there.
        'emails': lead.email_logs.all()[:20],
        'followups': lead.followups.all()[:20],
        'quotations': company.quotations.all()[:10],
        'activities': lead.activities.all()[:20],
        'status_choices': Lead.STATUS_CHOICES,
        'quality_score': company.data_quality_score,
        'quality_band': company.quality_band,
        'missing_fields': quality.missing_fields(company),
        'can_send_email': can_send,
        'send_reason': send_reason,
        'quota_left': EmailQuota.remaining_today(),
    })


@login_required
@require_POST
def update_lead(request, lead_id):
    """Edit the workflow fields the client fills in by hand."""
    lead = get_object_or_404(Lead, id=lead_id)

    lead.requirement = request.POST.get('requirement', lead.requirement)
    lead.remarks = request.POST.get('remarks', lead.remarks)
    lead.quotation = request.POST.get('quotation', lead.quotation)

    follow_up = (request.POST.get('follow_up_date') or '').strip()
    if follow_up:
        parsed = parse_date(follow_up)
        if parsed is None:
            messages.error(request, 'That follow-up date could not be read.')
            return redirect('leads:detail', lead_id=lead.id)
        lead.follow_up_date = parsed
    else:
        lead.follow_up_date = None

    lead.save(update_fields=['requirement', 'remarks', 'quotation',
                             'follow_up_date', 'updated_at'])

    from reports.utils import log_audit
    log_audit('Lead Updated', 'Lead', lead.id, 'Notes and follow-up date',
              request.user)
    messages.success(request, 'Lead saved.')
    return redirect('leads:detail', lead_id=lead.id)


@login_required
@require_POST
def mark_profile_sent(request, lead_id):
    """
    Record that the profile went out and start the reminder clock.

    This is the client's 7/10 day rule: the follow-up date is set here, and the
    daily worker turns it into a Follow-up Due lead when it arrives.
    """
    lead = get_object_or_404(Lead, id=lead_id)
    delay = request.POST.get('delay_days')
    try:
        delay = int(delay) if delay else None
    except ValueError:
        delay = None

    lead.mark_profile_sent(delay_days=delay)

    from reports.utils import log_audit
    log_audit('Profile Sent', 'Lead', lead.id,
              f'Follow-up set for {lead.follow_up_date}', request.user)
    messages.success(
        request,
        f'Marked as Profile Sent. Reminder set for {lead.follow_up_date:%d %b %Y}.')
    return redirect('leads:detail', lead_id=lead.id)


@login_required
def companies_list(request):
    companies = Company.objects.prefetch_related('plants', 'contacts',
                                                 'leads')

    search = (request.GET.get('q') or '').strip()
    if search:
        companies = companies.filter(
            Q(company_name__icontains=search)
            | Q(normalized_name__icontains=search)
            | Q(industry__icontains=search)
            | Q(company_email__icontains=search)
            | Q(cin__icontains=search)
            | Q(gstin__icontains=search)
        )

    page = Paginator(companies, settings.PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'leads/company_list.html', {
        'page_obj': page,
        'companies': page.object_list,
        'search': search,
        'total': companies.count(),
    })


@login_required
def contacts_index(request):
    contacts = Contact.objects.select_related('company')

    search = (request.GET.get('q') or '').strip()
    if search:
        contacts = contacts.filter(
            Q(name__icontains=search) | Q(email__icontains=search)
            | Q(mobile__icontains=search)
            | Q(company__company_name__icontains=search))

    if request.GET.get('named') == '1':
        contacts = contacts.exclude(name='')

    page = Paginator(contacts, settings.PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'leads/contacts_index.html', {
        'page_obj': page,
        'contacts': page.object_list,
        'search': search,
        'named_only': request.GET.get('named') == '1',
        'total': contacts.count(),
    })


# --------------------------------------------------------------------------
# Duplicate review — detection is useless without this screen
# --------------------------------------------------------------------------

@login_required
def review_list(request):
    """Pairs the system was not confident enough to merge on its own."""
    reviews = DuplicateReview.objects.select_related('existing_company')

    show = request.GET.get('show', 'pending')
    if show == 'pending':
        reviews = reviews.filter(status='PENDING')
    elif show == 'done':
        reviews = reviews.exclude(status='PENDING')

    page = Paginator(reviews, 20).get_page(request.GET.get('page'))
    return render(request, 'leads/review_list.html', {
        'page_obj': page,
        'reviews': page.object_list,
        'show': show,
        'pending_count': DuplicateReview.objects.filter(status='PENDING').count(),
    })


@login_required
def review_detail(request, review_id):
    """Side-by-side comparison with three plain choices."""
    review = get_object_or_404(
        DuplicateReview.objects.select_related('existing_company'), id=review_id)
    return render(request, 'leads/review_detail.html', {
        'review': review,
        'rows': review.field_comparison(),
    })


@login_required
@require_POST
def review_resolve(request, review_id):
    review = get_object_or_404(DuplicateReview, id=review_id)
    if not review.is_pending:
        messages.warning(request, 'That review has already been decided.')
        return redirect('leads:review_list')

    decision = request.POST.get('decision')
    if decision not in ('merge', 'existing', 'new', 'separate'):
        messages.error(request, 'Choose one of the options.')
        return redirect('leads:review_detail', review_id=review.id)

    status = apply_review(review, decision, user=request.user)

    from reports.utils import log_audit
    log_audit('Duplicate Resolved', 'DuplicateReview', review.id,
              f'Decision: {status}', request.user)

    wording = {
        'MERGED': 'Records merged.',
        'KEPT_EXISTING': 'Kept the existing record. New data discarded.',
        'USED_NEW': 'Existing record updated with the new data.',
        'NOT_DUPLICATE': 'Saved as a separate company.',
    }
    messages.success(request, wording.get(status, 'Review saved.'))

    next_review = DuplicateReview.objects.filter(status='PENDING').first()
    if next_review:
        return redirect('leads:review_detail', review_id=next_review.id)
    return redirect('leads:review_list')


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

EXPORT_COLUMNS = [
    'Company Name', 'Industry', 'Company Type', 'Plant Name', 'Plant Address',
    'City', 'District', 'State', 'PIN', 'Website', 'Company Phone',
    'Company Email', 'Contact Person', 'Designation', 'Department', 'Mobile',
    'Direct Phone', 'Contact Email', 'CIN', 'GSTIN', 'LinkedIn', 'Google Maps',
    'Lead Source', 'Data Verified', 'Quality Score', 'First Contact',
    'Profile Sent', 'Follow-up Date', 'Requirement', 'Quotation',
    'Lead Status', 'Remarks', 'Last Updated',
]


@login_required
def export_leads(request):
    """
    All 33 fields the client listed, streamed as CSV.

    Streaming rather than building a spreadsheet in memory, so exporting
    10,000 leads does not exhaust the server.
    """
    leads = Lead.objects.select_related('company', 'contact') \
                        .prefetch_related('company__plants', 'company__contacts')

    status = request.GET.get('status')
    if status in dict(Lead.STATUS_CHOICES):
        leads = leads.filter(status=status)

    class Echo:
        def write(self, value):
            return value

    writer = csv.writer(Echo())

    def rows():
        yield writer.writerow(EXPORT_COLUMNS)
        for lead in leads.iterator(chunk_size=500):
            company = lead.company
            plant = company.primary_plant
            contact = lead.contact or company.primary_contact
            yield writer.writerow([
                company.company_name,
                company.industry,
                company.company_type,
                plant.plant_name if plant else '',
                plant.plant_address if plant else '',
                plant.city if plant else '',
                plant.district if plant else '',
                plant.state if plant else '',
                plant.pin_code if plant else '',
                company.website,
                company.company_phone,
                company.company_email,
                contact.name if contact else '',
                contact.designation if contact else '',
                contact.department if contact else '',
                contact.mobile if contact else '',
                contact.direct_phone if contact else '',
                contact.email if contact else '',
                company.cin,
                company.gstin,
                company.linkedin_url,
                company.google_maps_url,
                lead.lead_source,
                company.get_email_status_display(),
                company.data_quality_score,
                lead.first_contact_date or '',
                lead.profile_sent_at or '',
                lead.follow_up_date or '',
                lead.requirement,
                lead.quotation,
                lead.get_status_display(),
                lead.remarks,
                lead.updated_at.strftime('%Y-%m-%d %H:%M'),
            ])

    response = StreamingHttpResponse(rows(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="leads-export.csv"'
    return response


@login_required
@require_POST
def recalculate_quality(request):
    """Refresh every quality score — used after a rules change or a backfill."""
    updated = 0
    for company in Company.objects.prefetch_related('plants', 'contacts').iterator(
            chunk_size=500):
        if quality.refresh(company):
            updated += 1
    messages.success(request, f'Quality scores recalculated for {updated} companies.')
    return redirect('leads:index')

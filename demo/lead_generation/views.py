from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from leads.models import Lead

from .models import City, District, GenerationJob, Industry


@login_required
def index(request):
    """Start a run, watch it live, and control it."""
    active = GenerationJob.objects.filter(
        status__in=GenerationJob.ACTIVE_STATUSES).first()

    return render(request, 'lead_generation/index.html', {
        'active_job': active,
        'recent_jobs': GenerationJob.objects.exclude(
            status__in=GenerationJob.ACTIVE_STATUSES)[:10],
        'industries': Industry.objects.filter(is_active=True).prefetch_related(
            'keywords'),
        'districts': District.objects.filter(is_active=True).prefetch_related(
            'cities'),
        'states': District.objects.filter(is_active=True)
                                  .values_list('state', flat=True).distinct(),
    })


@login_required
@require_POST
def start(request):
    """Queue a new generation run."""
    if GenerationJob.objects.filter(status__in=GenerationJob.ACTIVE_STATUSES).exists():
        messages.warning(request, 'A run is already going. Stop it before '
                                  'starting another.')
        return redirect('lead_generation:index')

    state = (request.POST.get('state') or '').strip()
    industry = (request.POST.get('industry') or '').strip()
    keywords = [k.strip() for k in request.POST.getlist('keywords') if k.strip()]
    extra = (request.POST.get('extra_keywords') or '').strip()
    if extra:
        keywords += [k.strip() for k in extra.split(',') if k.strip()]

    if not state or not industry:
        messages.error(request, 'Choose a state and an industry.')
        return redirect('lead_generation:index')
    if not keywords:
        messages.error(request, 'Choose at least one search keyword.')
        return redirect('lead_generation:index')

    try:
        max_websites = max(5, min(int(request.POST.get('max_websites') or 50), 500))
    except ValueError:
        max_websites = 50

    job = GenerationJob.objects.create(
        state=state,
        # Both are optional: blank district means every district in the state.
        district=(request.POST.get('district') or '').strip(),
        city=(request.POST.get('city') or '').strip(),
        industry=industry,
        keywords=','.join(dict.fromkeys(keywords)),
        max_websites=max_websites,
        status='PENDING',
    )

    from reports.utils import log_audit
    log_audit('Generation Started', 'GenerationJob', job.id,
              f'{industry} across {job.scope}', request.user)

    # Start now in a thread; cron would pick it up within a minute anyway, so
    # a dropped thread only delays the run, it never loses it.
    from core.worker import wake
    wake()

    messages.success(request, f'Run #{job.id} started — searching {job.scope}.')
    return redirect('lead_generation:index')


@login_required
@require_POST
def control(request, job_id, action):
    """Pause, resume or stop one specific run."""
    job = get_object_or_404(GenerationJob, id=job_id)

    transitions = {
        'pause': ('RUNNING', 'PAUSED', 'paused'),
        'resume': ('PAUSED', 'RUNNING', 'resumed'),
        'stop': (None, 'STOPPED', 'stopped'),
    }
    if action not in transitions:
        messages.error(request, 'Unknown action.')
        return redirect('lead_generation:index')

    required, new_status, verb = transitions[action]
    if required and job.status != required:
        messages.warning(request, f'Run #{job.id} is {job.get_status_display()} '
                                  f'— cannot be {verb}.')
        return redirect('lead_generation:index')

    job.status = new_status
    job.note(f'{verb.title()} by user.')
    if action == 'stop':
        from django.utils import timezone
        job.stopped_at = timezone.now()
    job.save()

    from reports.utils import log_audit
    log_audit(f'Generation {verb.title()}', 'GenerationJob', job.id, '',
              request.user)

    if action == 'resume':
        from core.worker import wake
        wake()

    messages.info(request, f'Run #{job.id} {verb}.')
    return redirect('lead_generation:index')


@login_required
def live_data(request):
    """Polled by the page every few seconds while a run is going."""
    job_id = request.GET.get('job_id')
    job = (GenerationJob.objects.filter(id=job_id).first() if job_id else
           GenerationJob.objects.filter(
               status__in=GenerationJob.ACTIVE_STATUSES).first())

    if not job:
        return JsonResponse({'status': 'no_job'})

    leads = (Lead.objects.filter(generation_job_id=job.id)
             .select_related('company')
             .prefetch_related('company__plants')[:50])

    rows = []
    for lead in leads:
        company = lead.company
        plant = company.primary_plant
        letter, label = company.quality_band
        rows.append({
            'id': lead.id,
            'company_name': company.company_name,
            'industry': company.industry or '—',
            'city': (plant.city if plant else '') or '—',
            'district': (plant.district if plant else '') or '—',
            'email': company.company_email or '—',
            'phone': company.company_phone or '—',
            'email_status': company.get_email_status_display(),
            'score': company.data_quality_score,
            'band': letter,
            'band_label': label,
            'website': company.website,
            'status': lead.get_status_display(),
        })

    return JsonResponse({
        'status': 'ok',
        'job': {
            'id': job.id,
            'status': job.status,
            'status_display': job.get_status_display(),
            'is_active': job.is_active,
            'scope': job.scope,
            'progress': job.progress_percent,
            'websites_found': job.websites_found,
            'max_websites': job.max_websites,
            'leads_found': job.leads_found,
            'valid_leads': job.valid_leads,
            'duplicate_leads': job.duplicate_leads,
            'rejected_leads': job.rejected_leads,
            'needs_review': job.needs_review,
            'current_query': job.current_query,
            'current_website': job.current_website,
            'error_message': job.error_message,
            'log': (job.log or '').splitlines()[-12:][::-1],
        },
        'leads': rows,
    })


@login_required
def cities_for_district(request):
    """Feeds the city dropdown once a district is chosen."""
    district_id = request.GET.get('district_id')
    cities = City.objects.filter(district_id=district_id, is_active=True) \
        if district_id else City.objects.none()
    return JsonResponse({'cities': [c.name for c in cities]})

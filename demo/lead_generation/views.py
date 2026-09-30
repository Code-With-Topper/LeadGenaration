from django.shortcuts import render, redirect
from django.contrib import messages
from .models import GenerationJob
from .tasks import run_generation_job_task

def index(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'start':
            state = request.POST.get('state')
            district = request.POST.get('district')
            city = request.POST.get('city')
            industry = request.POST.get('industry')
            keywords = request.POST.getlist('keywords')
            run_mode = request.POST.get('run_mode', 'until_stopped')
            is_headless = request.POST.get('is_headless') == 'on'

            job = GenerationJob.objects.create(
                state=state,
                district=district,
                city=city,
                industry=industry,
                keywords=','.join(keywords),
                status='PENDING',
                is_headless=is_headless
            )
            
            from reports.utils import log_audit
            log_audit('Generation Started', 'GenerationJob', job.id, f"Started extraction for {industry} in {city or state}", request.user if request.user.is_authenticated else None)
            
            # Launch in background thread (no Redis/Celery required)
            try:
                run_generation_job_task(job.id)
                messages.success(request, f"Generation Job #{job.id} has been started in the background.")
            except Exception as e:
                job.status = 'FAILED'
                job.error_message = f"Failed to start background worker: {str(e)}"
                job.save()
                messages.error(request, f"Failed to start generation: {str(e)}")
                
            return redirect('lead_generation:index')
            
        else:
            # For PAUSE, RESUME, STOP, we find the latest active job
            # If your app supports multiple concurrent jobs, you'd pass a specific job_id from the UI instead.
            active_job = GenerationJob.objects.exclude(status__in=['COMPLETED', 'FAILED', 'STOPPED']).order_by('-id').first()
            
            if not active_job:
                messages.warning(request, "No active job found to perform this action.")
                return redirect('lead_generation:index')
                
            if action == 'pause':
                active_job.status = 'PAUSED'
                active_job.save()
                from reports.utils import log_audit
                log_audit('Generation Paused', 'GenerationJob', active_job.id, f"Paused job #{active_job.id}", request.user if request.user.is_authenticated else None)
                messages.warning(request, f"Generation Job #{active_job.id} has been PAUSED.")
            elif action == 'resume':
                active_job.status = 'RUNNING'
                active_job.save()
                from reports.utils import log_audit
                log_audit('Generation Resumed', 'GenerationJob', active_job.id, f"Resumed job #{active_job.id}", request.user if request.user.is_authenticated else None)
                messages.info(request, f"Generation Job #{active_job.id} has been RESUMED.")
            elif action == 'stop':
                active_job.status = 'STOPPED'
                active_job.save()
                from reports.utils import log_audit
                log_audit('Generation Stopped', 'GenerationJob', active_job.id, f"Stopped job #{active_job.id}", request.user if request.user.is_authenticated else None)
                messages.error(request, f"Generation Job #{active_job.id} has been STOPPED.")
                
            return redirect('lead_generation:index')

    # Check for an active job
    active_job = GenerationJob.objects.exclude(status__in=['COMPLETED', 'FAILED', 'STOPPED']).order_by('-id').first()
    
    # Get previous jobs
    previous_jobs = GenerationJob.objects.filter(status__in=['COMPLETED', 'FAILED', 'STOPPED']).order_by('-id')[:5]
    
    return render(request, 'lead_generation/index.html', {
        'active_job': active_job,
        'previous_jobs': previous_jobs
    })

from django.http import JsonResponse
from leads.models import Lead

def live_job_data(request):
    job_id = request.GET.get('job_id')
    if job_id:
        latest_job = GenerationJob.objects.filter(id=job_id).first()
    else:
        latest_job = GenerationJob.objects.exclude(status__in=['COMPLETED', 'FAILED', 'STOPPED']).order_by('-id').first()
        
    if not latest_job:
        return JsonResponse({"status": "no_job"})
    
    # Get the latest 50 leads for this job
    leads_qs = Lead.objects.filter(generation_job_id=latest_job.id).select_related('company').order_by('-id')[:50]
    
    leads_data = []
    for ld in leads_qs:
        company = ld.company
        leads_data.append({
            "id": ld.id,
            "company_name": company.company_name,
            "industry": company.industry or "Unknown",
            "city": company.plants.first().city if company.plants.exists() else "Unknown",
            "state": company.plants.first().state if company.plants.exists() else "Unknown",
            "email": company.company_email or "Not verified",
            "phone": company.company_phone or "Not verified",
            "status": ld.status,
            "data_verified": ld.data_verified,
            "verification_status": company.verification_status,
            "google_maps_url": company.google_maps_url,
            "website": company.website,
        })
        
    job_data = {
        "id": latest_job.id,
        "status": latest_job.status,
        "websites_found": latest_job.websites_found,
        "companies_found": latest_job.leads_found,
        "valid_leads": latest_job.valid_leads,
        "needs_review": latest_job.needs_review,
        "duplicate_leads": latest_job.duplicate_leads,
        "invalid_leads": latest_job.invalid_leads,
        "current_query": latest_job.current_query or "",
        "current_website": latest_job.current_website or "",
        "error_message": latest_job.error_message or "",
    }
    
    return JsonResponse({
        "status": "ok",
        "job": job_data,
        "leads": leads_data
    })

from leads.models import Company, Plant, Contact, Lead
from lead_generation.models import GenerationJob, DuplicateResolution
import re
import logging
from urllib.parse import urlparse
from django.db import transaction

LOGGER = logging.getLogger("lead_generation")

def normalize_domain(url):
    if not url or url == "NOT FOUND":
        return ""
    try:
        parsed = urlparse(url)
        return parsed.netloc.replace("www.", "").lower()
    except Exception:
        return ""

def save_extraction_result(result: dict, job_id: int):
    """
    Saves the structured extraction result directly into the database.
    Performs normalization, validation, and duplicate detection.
    """
    try:
        job = GenerationJob.objects.get(id=job_id)
    except GenerationJob.DoesNotExist:
        LOGGER.error(f"[Extraction] Job {job_id} not found in DB.")
        return None

    # Normalization
    company_name = result.get('company_name', '').strip()
    company_name = re.sub(r"\s+", " ", company_name)
    company_name = re.split(r"\s+[|–—-]\s+", company_name, maxsplit=1)[0][:250]
    
    if not company_name:
        LOGGER.warning(f"[Extraction] Skipping result with empty company name for job #{job_id}")
        return None
    
    website = result.get('website', '').strip()
    email = result.get('primary_email', '').strip().lower()
    phone = result.get('primary_phone', '').strip()
    
    cin = result.get('cin', '').strip().upper()
    gstin = result.get('gstin', '').strip().upper()
    linkedin = result.get('linkedin_url', '').strip()

    # Validation Flags
    v_website = website if re.match(r"^https?://", website) else "NOT FOUND"
    v_email = email if email and re.match(r"[^@]+@[^@]+\.[^@]+", email) else "NOT FOUND"
    v_phone = phone if phone and 8 <= len(re.sub(r"\D", "", phone)) <= 15 else "NOT FOUND"
    v_cin = cin if cin else "NOT FOUND"
    v_gstin = gstin if gstin else "NOT FOUND"

    LOGGER.info(f"[Extraction] Processing: {company_name} | website={v_website} | email={v_email} | phone={v_phone}")

    # Match Hierarchy: CIN -> GSTIN -> Email -> Phone -> Domain -> Name+City
    company = None
    if not company and v_cin != "NOT FOUND":
        company = Company.objects.filter(cin=v_cin).first()
    if not company and v_gstin != "NOT FOUND":
        company = Company.objects.filter(gstin=v_gstin).first()
    if not company and v_email != "NOT FOUND":
        company = Company.objects.filter(company_email=v_email).first()
    if not company and v_phone != "NOT FOUND":
        company = Company.objects.filter(company_phone=v_phone).first()
    if not company and v_website != "NOT FOUND":
        n_domain = normalize_domain(v_website)
        if n_domain:
            company = Company.objects.filter(normalized_domain=n_domain).first()
    if not company:
        # Match Name + City
        company = Company.objects.filter(company_name__iexact=company_name, plants__city__iexact=job.city).first()

    if company:
        LOGGER.info(f"[Extraction] Duplicate found: {company.company_name} (id={company.id})")
        # If company exists, queue a duplicate resolution instead of blindly overwriting
        scraped_data = {
            "company_name": company_name,
            "website": v_website,
            "company_email": v_email,
            "company_phone": v_phone,
            "cin": v_cin,
            "gstin": v_gstin,
            "linkedin_url": linkedin,
            "emails": result.get('emails', []),
            "phones": result.get('phones', [])
        }
        DuplicateResolution.objects.create(
            job=job,
            existing_company=company,
            scraped_data=scraped_data,
            status='PENDING'
        )
        job.duplicate_leads += 1
        job.needs_review += 1
        job.save()
        return company
    
    # Create new company
    try:
        with transaction.atomic():
            company = Company.objects.create(
                company_name=company_name,
                normalized_name=company_name.lower(),
                website=v_website if v_website != "NOT FOUND" else "",
                normalized_domain=normalize_domain(v_website),
                industry=job.industry,
                company_email=v_email if v_email != "NOT FOUND" else "",
                company_phone=v_phone if v_phone != "NOT FOUND" else "",
                cin=v_cin if v_cin != "NOT FOUND" else "",
                gstin=v_gstin if v_gstin != "NOT FOUND" else "",
                linkedin_url=linkedin,
                verification_status="VERIFIED" if (v_email != "NOT FOUND" or v_phone != "NOT FOUND") else "NEW"
            )

            # Create Plant
            Plant.objects.get_or_create(
                company=company,
                city=job.city,
                defaults={
                    'state': job.state,
                    'district': job.district,
                    'plant_name': f"Main Plant - {job.city}",
                    'pin_code': ""
                }
            )

            # Create Lead
            lead_obj, lead_created = Lead.objects.get_or_create(
                company=company,
                generation_job_id=job.id,
                defaults={
                    'lead_source': 'Bing Search',
                    'source_query': result.get('source_query', ''),
                    'source_url': result.get('result_url', ''),
                    'status': 'NEW',
                    'data_verified': True if (v_email != "NOT FOUND" or v_phone != "NOT FOUND") else False
                }
            )

        LOGGER.info(f"[Extraction] CREATED Company: {company.company_name} (id={company.id}), Lead id={lead_obj.id}")

        # Job Counters
        job.leads_found += 1
        if lead_obj.data_verified:
            job.valid_leads += 1
        else:
            job.invalid_leads += 1
        job.save()

        # Create Contacts
        for em in result.get('emails', []):
            if em and re.match(r"[^@]+@[^@]+\.[^@]+", em):
                Contact.objects.get_or_create(company=company, email=em.lower(), defaults={'name': '', 'mobile': ''})
                
        for ph in result.get('phones', []):
            if ph and 8 <= len(re.sub(r"\D", "", ph)) <= 15:
                Contact.objects.get_or_create(company=company, mobile=ph, defaults={'name': '', 'email': ''})

        return company
        
    except Exception as e:
        LOGGER.error(f"[Extraction] Failed to save company '{company_name}': {e}")
        return None

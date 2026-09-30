import pandas as pd
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from .models import ImportJob
from leads.models import Lead, Company, Contact

def import_index(request):
    jobs = ImportJob.objects.all().order_by('-created_at')
    return render(request, 'imports/index.html', {'jobs': jobs})

def upload_file(request):
    if request.method == 'POST':
        file = request.FILES.get('file')
        if not file:
            messages.error(request, 'Please select a file.')
            return redirect('imports:index')
            
        if not file.name.endswith(('.csv', '.xlsx')):
            messages.error(request, 'Only CSV and Excel files are supported.')
            return redirect('imports:index')
            
        job = ImportJob.objects.create(file_name=file.name, file_path=file)
        
        # Read the file to get columns
        try:
            if file.name.endswith('.csv'):
                df = pd.read_csv(job.file_path.path, nrows=5)
            else:
                df = pd.read_excel(job.file_path.path, nrows=5)
                
            columns = df.columns.tolist()
            request.session['import_columns'] = columns
            
        except Exception as e:
            job.status = 'FAILED'
            job.error_log = str(e)
            job.save()
            messages.error(request, f"Failed to read file: {e}")
            return redirect('imports:index')
            
        return redirect('imports:map_columns', job_id=job.id)
    return redirect('imports:index')

def map_columns(request, job_id):
    job = get_object_or_404(ImportJob, id=job_id)
    columns = request.session.get('import_columns', [])
    
    expected_fields = [
        ('company_name', 'Company Name (Required)'),
        ('industry', 'Industry'),
        ('company_email', 'Company Email'),
        ('company_phone', 'Company Phone'),
        ('website', 'Website'),
        ('contact_name', 'Contact Name'),
        ('contact_email', 'Contact Email'),
        ('contact_phone', 'Contact Phone'),
        ('city', 'City'),
        ('state', 'State')
    ]
    
    if request.method == 'POST':
        mapping = {}
        for db_field, _ in expected_fields:
            csv_col = request.POST.get(f'map_{db_field}')
            if csv_col:
                mapping[db_field] = csv_col
                
        if not mapping.get('company_name'):
            messages.error(request, "Company Name must be mapped.")
            return redirect('imports:map_columns', job_id=job.id)
            
        job.column_mapping = mapping
        job.status = 'MAPPED'
        job.save()
        return redirect('imports:process_import', job_id=job.id)
        
    return render(request, 'imports/map_columns.html', {
        'job': job,
        'columns': columns,
        'expected_fields': expected_fields
    })

def process_import(request, job_id):
    job = get_object_or_404(ImportJob, id=job_id)
    if job.status not in ['MAPPED']:
        messages.error(request, "Invalid job status.")
        return redirect('imports:index')
        
    try:
        if job.file_name.endswith('.csv'):
            df = pd.read_csv(job.file_path.path)
        else:
            df = pd.read_excel(job.file_path.path)
            
        df = df.fillna('')
        mapping = job.column_mapping
        
        job.total_rows = len(df)
        success_count = 0
        failed_count = 0
        
        for index, row in df.iterrows():
            try:
                c_name = str(row[mapping['company_name']]).strip()
                if not c_name:
                    failed_count += 1
                    continue
                    
                # Company logic
                company, created = Company.objects.get_or_create(company_name=c_name)
                if 'industry' in mapping:
                    company.industry = str(row[mapping['industry']]).strip()
                if 'company_email' in mapping:
                    company.company_email = str(row[mapping['company_email']]).strip()
                if 'company_phone' in mapping:
                    company.company_phone = str(row[mapping['company_phone']]).strip()
                if 'website' in mapping:
                    company.website = str(row[mapping['website']]).strip()
                company.save()
                
                # Contact logic
                if 'contact_name' in mapping and str(row[mapping['contact_name']]).strip():
                    contact_name = str(row[mapping['contact_name']]).strip()
                    contact, cc = Contact.objects.get_or_create(company=company, name=contact_name)
                    if 'contact_email' in mapping:
                        contact.email = str(row[mapping['contact_email']]).strip()
                    if 'contact_phone' in mapping:
                        contact.mobile = str(row[mapping['contact_phone']]).strip()
                    contact.save()
                else:
                    contact = None
                    
                # Create Lead
                lead, lead_created = Lead.objects.get_or_create(
                    company=company,
                    defaults={'contact': contact, 'lead_source': 'File Import', 'status': 'NEW'}
                )
                if lead_created:
                    from reports.utils import log_audit
                    log_audit(
                        action='Lead Imported',
                        model_name='Lead',
                        object_id=lead.id,
                        changes=f"Imported from {job.file_name}",
                        user=request.user if request.user.is_authenticated else None
                    )
                success_count += 1
                
            except Exception as e:
                failed_count += 1
                
        job.imported_rows = success_count
        job.failed_rows = failed_count
        job.status = 'COMPLETED'
        job.completed_at = timezone.now()
        job.save()
        messages.success(request, f"Import complete. Imported {success_count}, Failed {failed_count}.")
        
    except Exception as e:
        job.status = 'FAILED'
        job.error_log = str(e)
        job.save()
        messages.error(request, f"Import failed: {e}")
        
    return redirect('imports:index')

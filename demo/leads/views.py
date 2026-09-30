from django.shortcuts import render, get_object_or_404
from .models import Lead, Company, Plant, Contact

def index(request):
    # Fetch all leads and prefetch related company for performance
    leads_list = Lead.objects.select_related('company').all().order_by('-created_at')
    
    # Calculate completeness dynamically for display
    # Example logic: 10 fields total (name, industry, email, phone, website, cin, gstin, city, state, contact)
    for lead in leads_list:
        comp = lead.company
        fields = [
            comp.company_name,
            comp.industry,
            comp.company_email,
            comp.company_phone,
            comp.website,
            comp.cin,
            comp.gstin
        ]
        
        filled = 0
        for f in fields:
            if f and str(f).strip() != "" and str(f).strip() != "NOT FOUND":
                filled += 1
                
        # Get first plant city
        plant = comp.plants.first()
        if plant and plant.city and plant.city != "NOT FOUND":
            filled += 1
            
        # Get contact count
        if comp.contacts.exists():
            filled += 1
            
        lead.completeness_percentage = int((filled / 9.0) * 100)
    
    return render(request, 'leads/index.html', {'leads': leads_list})

def detail(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    company = lead.company
    plants = company.plants.all()
    contacts = company.contacts.all()
    
    # Fetch related activities, emails, followups, quotations
    # (Since these models exist in other apps, we query them using reverse relations if set, or query directly)
    activities = company.activities.all().order_by('-created_at') if hasattr(company, 'activities') else []
    emails = company.emails.all().order_by('-created_at') if hasattr(company, 'emails') else []
    followups = company.followups.all().order_by('-date', '-time') if hasattr(company, 'followups') else []
    quotations = company.quotations.all().order_by('-created_at') if hasattr(company, 'quotations') else []
    
    context = {
        'lead': lead,
        'company': company,
        'plants': plants,
        'contacts': contacts,
        'activities': activities,
        'emails': emails,
        'followups': followups,
        'quotations': quotations
    }
    return render(request, 'leads/detail.html', context)

def contacts_index(request):
    contacts_list = Contact.objects.select_related('company').all().order_by('-created_at')
    return render(request, 'leads/contacts_index.html', {'contacts': contacts_list})

def export_leads(request):
    import pandas as pd
    from django.http import HttpResponse
    import io
    
    leads = Lead.objects.select_related('company', 'contact').all()
    
    data = []
    for lead in leads:
        data.append({
            'Lead ID': lead.id,
            'Company Name': lead.company.company_name,
            'Industry': lead.company.industry,
            'Company Email': lead.company.company_email,
            'Company Phone': lead.company.company_phone,
            'Website': lead.company.website,
            'Contact Name': lead.contact.name if lead.contact else '',
            'Contact Email': lead.contact.email if lead.contact else '',
            'Contact Phone': lead.contact.mobile if lead.contact else '',
            'Lead Status': lead.get_status_display(),
            'Source': lead.lead_source,
            'Created At': lead.created_at.strftime('%Y-%m-%d %H:%M')
        })
        
    df = pd.DataFrame(data)
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Leads')
        
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="leads_export.xlsx"'
    return response


def companies_list(request):
    companies = Company.objects.prefetch_related('plants', 'contacts', 'leads').all().order_by('-created_at')
    
    # Optional filtering
    query = request.GET.get('q')
    if query:
        companies = companies.filter(Q(company_name__icontains=query) | Q(industry__icontains=query))
        
    context = {
        'companies': companies,
        'search_query': query or ''
    }
    return render(request, 'leads/company_list.html', context)

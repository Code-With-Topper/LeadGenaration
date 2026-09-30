from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from leads.models import Lead
from emails.models import EmailLog
from followups.models import FollowUp
from quotations.models import Quotation

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard-index')
        
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            login(request, user)
            return redirect('dashboard-index')
        else:
            messages.error(request, 'Invalid credentials')
            
    return render(request, 'dashboard/login.html')

def logout_view(request):
    logout(request)
    return redirect('login')

@login_required(login_url='/login/')
def index(request):
    total_leads = Lead.objects.count()
    valid_leads = Lead.objects.filter(data_verified=True).count()
    incomplete_leads = Lead.objects.filter(completeness_percentage__lt=100).count()
    
    # We don't have a direct duplicate flag on Lead yet, so we compute it from GenerationJob or just count leads that might be marked.
    # For now, it will be 0 if we haven't implemented duplicate flags on Lead.
    duplicates = 0 
    
    emails_sent = EmailLog.objects.filter(status='SENT').count()
    followups = FollowUp.objects.count()
    quotations = Quotation.objects.count()
    converted = Lead.objects.filter(status='CONVERTED').count()

    context = {
        'total_leads': total_leads,
        'valid_leads': valid_leads,
        'incomplete_leads': incomplete_leads,
        'duplicates': duplicates,
        'emails_sent': emails_sent,
        'followups': followups,
        'quotations': quotations,
        'converted': converted,
    }
    return render(request, 'dashboard/index.html', context)

from django.http import HttpResponse

def public_home(request):
    if request.user.is_authenticated:
        return redirect('dashboard-index')
    return render(request, 'dashboard/public/home.html')

def privacy_policy(request):
    return render(request, 'dashboard/public/privacy.html')

def terms(request):
    return render(request, 'dashboard/public/terms.html')

def sitemap(request):
    xml = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://leadcrm.example.com/</loc></url>
    <url><loc>https://leadcrm.example.com/privacy-policy/</loc></url>
    <url><loc>https://leadcrm.example.com/terms/</loc></url>
</urlset>'''
    return HttpResponse(xml, content_type="application/xml")

def robots(request):
    txt = '''User-agent: *
Disallow: /dashboard/
Disallow: /admin/
Disallow: /crm/
Allow: /
Sitemap: https://leadcrm.example.com/sitemap.xml'''
    return HttpResponse(txt, content_type="text/plain")

import uuid
import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse
from django.core.mail import EmailMessage
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
import io
from .models import Quotation, QuotationItem
from leads.models import Lead

def quotation_list(request):
    quotations = Quotation.objects.all().order_by('-created_at')
    return render(request, 'quotations/list.html', {'quotations': quotations})

def create_quotation(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    company = lead.company
    contact = company.contacts.first()
    
    if request.method == 'POST':
        valid_until = request.POST.get('valid_until')
        terms = request.POST.get('terms')
        notes = request.POST.get('notes')
        
        # Read arrays of items
        descriptions = request.POST.getlist('description[]')
        quantities = request.POST.getlist('quantity[]')
        rates = request.POST.getlist('rate[]')
        taxes = request.POST.getlist('tax[]')
        discounts = request.POST.getlist('discount[]')
        
        # Generate ID
        q_num = f"QT-{datetime.datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        
        quotation = Quotation.objects.create(
            quotation_number=q_num,
            company=company,
            lead=lead,
            contact=contact,
            valid_until=valid_until or None,
            terms=terms,
            notes=notes,
            status='DRAFT'
        )
        
        subtotal_sum = 0
        tax_sum = 0
        discount_sum = 0
        
        for d, q, r, t, disc in zip(descriptions, quantities, rates, taxes, discounts):
            if not d.strip(): continue
            qty = float(q)
            rate = float(r)
            tax_pct = float(t)
            disc_amt = float(disc)
            
            row_gross = qty * rate
            row_tax = (row_gross - disc_amt) * (tax_pct / 100.0)
            row_total = row_gross - disc_amt + row_tax
            
            QuotationItem.objects.create(
                quotation=quotation,
                description=d,
                quantity=qty,
                rate=rate,
                tax_percentage=tax_pct,
                discount_amount=disc_amt,
                total_price=row_total
            )
            
            subtotal_sum += row_gross
            tax_sum += row_tax
            discount_sum += disc_amt
            
        quotation.subtotal = subtotal_sum
        quotation.tax_total = tax_sum
        quotation.discount_total = discount_sum
        quotation.total_amount = subtotal_sum - discount_sum + tax_sum
        quotation.save()
        
        # Update Lead Status
        if lead.status in ['NEW', 'CONTACTED', 'FOLLOW_UP', 'REQUIREMENT', 'INTERESTED', 'REQUIREMENT_RECEIVED']:
            lead.status = 'QUOTATION_SENT'
            lead.save()
            
        from reports.utils import log_audit
        log_audit(
            action='Quotation Created',
            model_name='Quotation',
            object_id=quotation.id,
            changes=f"Created Quote {q_num} for ${quotation.total_amount}",
            user=request.user if request.user.is_authenticated else None
        )
            
        messages.success(request, f"Quotation {q_num} created successfully.")
        return redirect('quotations:view_quotation', quotation_id=quotation.id)
        
    return render(request, 'quotations/create.html', {'lead': lead, 'company': company})

def view_quotation(request, quotation_id):
    quotation = get_object_or_404(Quotation, id=quotation_id)
    return render(request, 'quotations/view.html', {'quotation': quotation})

def generate_pdf(request, quotation_id):
    quotation = get_object_or_404(Quotation, id=quotation_id)
    
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Title
    p.setFont("Helvetica-Bold", 20)
    p.drawString(50, height - 50, "QUOTATION")
    
    # Details
    p.setFont("Helvetica", 12)
    p.drawString(50, height - 90, f"Number: {quotation.quotation_number}")
    p.drawString(50, height - 110, f"Date: {quotation.date.strftime('%Y-%m-%d')}")
    if quotation.valid_until:
        p.drawString(50, height - 130, f"Valid Until: {quotation.valid_until.strftime('%Y-%m-%d')}")
        
    p.drawString(350, height - 90, f"To: {quotation.company.company_name}")
    if quotation.contact:
        p.drawString(350, height - 110, f"Attn: {quotation.contact.name}")
        
    # Table Header
    y = height - 180
    p.setFont("Helvetica-Bold", 10)
    p.drawString(50, y, "Description")
    p.drawString(300, y, "Qty")
    p.drawString(350, y, "Rate")
    p.drawString(410, y, "Tax/Disc")
    p.drawString(480, y, "Total")
    p.line(50, y-5, 540, y-5)
    
    # Items
    y -= 25
    p.setFont("Helvetica", 10)
    for item in quotation.items.all():
        p.drawString(50, y, str(item.description)[:40])
        p.drawString(300, y, str(item.quantity))
        p.drawString(350, y, f"${item.rate}")
        p.drawString(410, y, f"{item.tax_percentage}% / ${item.discount_amount}")
        p.drawString(480, y, f"${item.total_price}")
        y -= 20
        
    p.line(50, y, 540, y)
    y -= 20
    
    # Totals
    p.setFont("Helvetica-Bold", 10)
    p.drawString(350, y, "Subtotal:")
    p.drawString(480, y, f"${quotation.subtotal}")
    y -= 15
    p.drawString(350, y, "Discount:")
    p.drawString(480, y, f"-${quotation.discount_total}")
    y -= 15
    p.drawString(350, y, "Tax:")
    p.drawString(480, y, f"+${quotation.tax_total}")
    y -= 20
    p.setFont("Helvetica-Bold", 12)
    p.drawString(350, y, "Grand Total:")
    p.drawString(480, y, f"${quotation.total_amount}")
    
    # Terms
    y -= 60
    if quotation.terms:
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Terms:")
        y -= 15
        p.setFont("Helvetica", 10)
        p.drawString(50, y, str(quotation.terms)[:100])
        y -= 20
        
    if quotation.notes:
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Notes:")
        y -= 15
        p.setFont("Helvetica", 10)
        p.drawString(50, y, str(quotation.notes)[:100])
        
    p.showPage()
    p.save()
    buffer.seek(0)
    
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Quotation_{quotation.quotation_number}.pdf"'
    return response

def send_quotation(request, quotation_id):
    quotation = get_object_or_404(Quotation, id=quotation_id)
    recipient = quotation.company.company_email
    
    if not recipient:
        messages.error(request, "Company has no email address.")
        return redirect('quotations:view_quotation', quotation_id=quotation.id)
        
    # Generate PDF in memory
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    p.drawString(100, 750, f"QUOTATION {quotation.quotation_number}")
    p.drawString(100, 730, f"Total: ${quotation.total_amount}")
    p.showPage()
    p.save()
    pdf_bytes = buffer.getvalue()
    
    try:
        email = EmailMessage(
            subject=f"Quotation #{quotation.quotation_number} from our Company",
            body=f"Please find attached your quotation #{quotation.quotation_number}.\n\nTotal: ${quotation.total_amount}\n\nBest Regards,\nSales Team",
            to=[recipient],
        )
        email.attach(f'Quotation_{quotation.quotation_number}.pdf', pdf_bytes, 'application/pdf')
        email.send()
        
        quotation.status = 'SENT'
        quotation.save()
        messages.success(request, f"Quotation emailed successfully to {recipient}.")
    except Exception as e:
        messages.error(request, f"Failed to send email: {e}")
        
    return redirect('quotations:view_quotation', quotation_id=quotation.id)

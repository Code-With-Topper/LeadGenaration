"""
Quotations.

Amounts are in rupees and money is handled with Decimal, never float — a
rounding error on a quotation is a rounding error the client has to explain to
their customer.
"""
import io
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from leads.models import Lead

from .models import Quotation, QuotationItem

TWO_PLACES = Decimal('0.01')


def to_decimal(value, default='0'):
    """Parse a form field into a Decimal without ever raising."""
    try:
        return Decimal(str(value).strip() or default)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


@login_required
def quotation_list(request):
    quotations = Quotation.objects.select_related('company', 'lead')
    status = request.GET.get('status') or ''
    if status in dict(Quotation.STATUS_CHOICES):
        quotations = quotations.filter(status=status)

    page = Paginator(quotations, settings.PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'quotations/list.html', {
        'page_obj': page,
        'quotations': page.object_list,
        'status': status,
        'status_choices': Quotation.STATUS_CHOICES,
        'total': quotations.count(),
    })


@login_required
def create_quotation(request, lead_id):
    lead = get_object_or_404(Lead.objects.select_related('company'), id=lead_id)
    company = lead.company

    if request.method == 'POST':
        descriptions = request.POST.getlist('description[]')
        quantities = request.POST.getlist('quantity[]')
        rates = request.POST.getlist('rate[]')
        taxes = request.POST.getlist('tax[]')
        discounts = request.POST.getlist('discount[]')

        rows = []
        for description, quantity, rate, tax, discount in zip(
                descriptions, quantities, rates, taxes, discounts):
            if not description.strip():
                continue
            rows.append({
                'description': description.strip()[:255],
                'quantity': to_decimal(quantity, '1'),
                'rate': to_decimal(rate),
                'tax': to_decimal(tax),
                'discount': to_decimal(discount),
            })

        if not rows:
            messages.error(request, 'Add at least one line item.')
            return render(request, 'quotations/create.html',
                          {'lead': lead, 'company': company})

        quotation = Quotation.objects.create(
            quotation_number=f"QT-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:4].upper()}",
            company=company,
            lead=lead,
            contact=lead.contact or company.contacts.exclude(name='').first(),
            valid_until=parse_date(
                (request.POST.get('valid_until') or '').strip()),
            terms=request.POST.get('terms') or '',
            notes=request.POST.get('notes') or '',
            status='DRAFT',
            created_by=request.user if request.user.is_authenticated else None,
        )

        subtotal = tax_total = discount_total = Decimal('0')
        for row in rows:
            gross = (row['quantity'] * row['rate']).quantize(TWO_PLACES)
            taxable = max(gross - row['discount'], Decimal('0'))
            row_tax = (taxable * row['tax'] / Decimal('100')).quantize(TWO_PLACES)
            QuotationItem.objects.create(
                quotation=quotation,
                description=row['description'],
                quantity=row['quantity'],
                rate=row['rate'],
                tax_percentage=row['tax'],
                discount_amount=row['discount'],
                total_price=(taxable + row_tax).quantize(TWO_PLACES),
            )
            subtotal += gross
            discount_total += row['discount']
            tax_total += row_tax

        quotation.subtotal = subtotal.quantize(TWO_PLACES)
        quotation.discount_total = discount_total.quantize(TWO_PLACES)
        quotation.tax_total = tax_total.quantize(TWO_PLACES)
        quotation.total_amount = (subtotal - discount_total + tax_total) \
            .quantize(TWO_PLACES)
        quotation.save()

        # Sending a quotation means the requirement is known.
        if lead.status in (Lead.NEW, Lead.CALLED, Lead.PROFILE_SENT,
                           Lead.FOLLOW_UP_DUE):
            lead.status = Lead.REQUIREMENT_RECEIVED
            lead.save(update_fields=['status', 'updated_at'])

        from reports.utils import log_audit
        log_audit('Quotation Created', 'Quotation', quotation.id,
                  f'{quotation.quotation_number} for Rs {quotation.total_amount}',
                  request.user)

        messages.success(request, f'Quotation {quotation.quotation_number} created.')
        return redirect('quotations:view_quotation', quotation_id=quotation.id)

    return render(request, 'quotations/create.html',
                  {'lead': lead, 'company': company})


@login_required
def view_quotation(request, quotation_id):
    quotation = get_object_or_404(
        Quotation.objects.select_related('company', 'lead', 'contact'),
        id=quotation_id)
    return render(request, 'quotations/view.html', {
        'quotation': quotation,
        'items': quotation.items.all(),
    })


@login_required
def generate_pdf(request, quotation_id):
    """A one-page PDF quotation, in rupees."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    quotation = get_object_or_404(Quotation, id=quotation_id)
    company = quotation.company
    plant = company.primary_plant

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left, right = 18 * mm, width - 18 * mm
    y = height - 20 * mm

    def line(label, value, offset=5 * mm, bold=False, size=9):
        nonlocal y
        pdf.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        pdf.drawString(left, y, f'{label}{value}')
        y -= offset

    pdf.setFont('Helvetica-Bold', 16)
    pdf.drawString(left, y, settings.COMPANY_NAME)
    pdf.setFont('Helvetica', 9)
    pdf.drawRightString(right, y, 'QUOTATION')
    y -= 6 * mm
    pdf.setFont('Helvetica', 8)
    pdf.drawString(left, y, settings.COMPANY_ADDRESS)
    pdf.drawRightString(right, y, quotation.quotation_number)
    y -= 4 * mm
    pdf.drawRightString(right, y, f'Date: {quotation.date:%d %b %Y}')
    y -= 10 * mm

    pdf.line(left, y, right, y)
    y -= 7 * mm

    line('To: ', company.company_name, bold=True, size=10)
    if quotation.contact and quotation.contact.name:
        line('Attn: ', quotation.contact.name)
    if plant and plant.full_address:
        line('', plant.full_address[:95])
    if company.gstin:
        line('GSTIN: ', company.gstin)
    if quotation.valid_until:
        line('Valid until: ', f'{quotation.valid_until:%d %b %Y}')

    y -= 4 * mm
    columns = [left, left + 88 * mm, left + 106 * mm, left + 130 * mm,
               left + 148 * mm]
    pdf.setFont('Helvetica-Bold', 8)
    for label, x in zip(['Description', 'Qty', 'Rate', 'Tax %', 'Amount'], columns):
        pdf.drawString(x, y, label)
    y -= 2 * mm
    pdf.line(left, y, right, y)
    y -= 5 * mm

    pdf.setFont('Helvetica', 8)
    for item in quotation.items.all():
        if y < 45 * mm:                     # start a new page before overflowing
            pdf.showPage()
            y = height - 20 * mm
            pdf.setFont('Helvetica', 8)
        pdf.drawString(columns[0], y, str(item.description)[:52])
        pdf.drawString(columns[1], y, f'{item.quantity:g}')
        pdf.drawString(columns[2], y, f'{item.rate:,.2f}')
        pdf.drawString(columns[3], y, f'{item.tax_percentage:g}')
        pdf.drawRightString(right, y, f'{item.total_price:,.2f}')
        y -= 5 * mm

    pdf.line(left, y, right, y)
    y -= 6 * mm

    for label, amount, bold in (
        ('Subtotal', quotation.subtotal, False),
        ('Discount', -quotation.discount_total, False),
        ('Tax', quotation.tax_total, False),
        ('Grand Total (INR)', quotation.total_amount, True),
    ):
        pdf.setFont('Helvetica-Bold' if bold else 'Helvetica', 10 if bold else 8)
        pdf.drawRightString(right - 30 * mm, y, f'{label}:')
        pdf.drawRightString(right, y, f'{amount:,.2f}')
        y -= 5 * mm

    if quotation.terms:
        y -= 5 * mm
        pdf.setFont('Helvetica-Bold', 8)
        pdf.drawString(left, y, 'Terms')
        y -= 4 * mm
        pdf.setFont('Helvetica', 7)
        for chunk in _wrap(str(quotation.terms), 120)[:6]:
            pdf.drawString(left, y, chunk)
            y -= 3.5 * mm

    if quotation.notes:
        y -= 3 * mm
        pdf.setFont('Helvetica-Bold', 8)
        pdf.drawString(left, y, 'Notes')
        y -= 4 * mm
        pdf.setFont('Helvetica', 7)
        for chunk in _wrap(str(quotation.notes), 120)[:6]:
            pdf.drawString(left, y, chunk)
            y -= 3.5 * mm

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="{quotation.quotation_number}.pdf"')
    return response


@login_required
@require_POST
def send_quotation(request, quotation_id):
    """
    Email a quotation.

    Goes through the same send service as every other message, so the daily
    cap and the unsubscribe list apply here too.
    """
    from emails.services import send_to_lead

    quotation = get_object_or_404(Quotation, id=quotation_id)
    lead = quotation.lead or quotation.company.leads.first()

    if lead is None:
        messages.error(request, 'This quotation has no lead to email.')
        return redirect('quotations:view_quotation', quotation_id=quotation.id)

    body = (
        f"Dear {quotation.contact.name if quotation.contact and quotation.contact.name else 'Sir/Madam'},\n\n"
        f"Please find our quotation {quotation.quotation_number} "
        f"for a total of Rs {quotation.total_amount:,.2f}.\n\n"
        f"{quotation.notes or ''}\n\n"
        f"Regards,\n{settings.COMPANY_NAME}"
    )

    result = send_to_lead(
        lead,
        subject=f'Quotation {quotation.quotation_number} — {settings.COMPANY_NAME}',
        body=body,
        user=request.user,
    )

    if result.ok:
        quotation.status = 'SENT'
        quotation.save(update_fields=['status', 'updated_at'])
        messages.success(request, result.message)
    else:
        messages.error(request, result.message)

    return redirect('quotations:view_quotation', quotation_id=quotation.id)


@login_required
@require_POST
def update_status(request, quotation_id):
    quotation = get_object_or_404(Quotation, id=quotation_id)
    status = request.POST.get('status')
    if status not in dict(Quotation.STATUS_CHOICES):
        messages.error(request, 'Unknown status.')
    else:
        quotation.status = status
        quotation.save(update_fields=['status', 'updated_at'])
        if status == 'ACCEPTED' and quotation.lead:
            quotation.lead.status = Lead.CONVERTED
            quotation.lead.follow_up_date = None
            quotation.lead.save(update_fields=['status', 'follow_up_date',
                                               'updated_at'])
            messages.success(request, 'Quotation accepted — lead marked Converted.')
        else:
            messages.success(request, f'Quotation marked {quotation.get_status_display()}.')
    return redirect('quotations:view_quotation', quotation_id=quotation.id)


def _wrap(text, width):
    """Crude word wrap for the PDF, which has no flowable text."""
    words, lines, current = text.split(), [], ''
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f'{current} {word}'.strip()
    if current:
        lines.append(current)
    return lines

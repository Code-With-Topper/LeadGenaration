"""
Populate the system with realistic sample leads so it can be demonstrated
without running a live crawl.

Deliberately includes messy rows — a duplicate spelling, an invalid email, a
phone written three different ways — so the duplicate check and the validation
can be seen working rather than described.
"""
from django.core.management.base import BaseCommand

SAMPLES = [
    # name, industry, city, district, pin, phone, email, website
    ('Durgapur Iron & Steel Works Pvt Ltd', 'Iron & Steel Manufacturing',
     'Durgapur', 'Paschim Bardhaman', '713203', '+91 9832011001',
     'info@durgapurironsteel.example', 'https://durgapurironsteel.example'),
    ('Asansol Sponge Iron Limited', 'Sponge Iron / DRI', 'Asansol',
     'Paschim Bardhaman', '713301', '0341 2256789',
     'sales@asansolsponge.example', 'https://asansolsponge.example'),
    ('Haldia Ferro Alloys Pvt Ltd', 'Ferro Alloys', 'Haldia',
     'Purba Medinipur', '721602', '9163022002',
     'enquiry@haldiaferro.example', 'https://haldiaferro.example'),
    ('Howrah Casting & Foundry Co', 'Foundry / Cast Iron', 'Howrah', 'Howrah',
     '711101', '033 26401234', 'contact@howrahcasting.example',
     'https://howrahcasting.example'),
    ('Raniganj Re-Rolling Mills', 'Rolling Mills', 'Raniganj',
     'Paschim Bardhaman', '713347', '9007033003', '',
     'https://raniganjrolling.example'),
    ('Kalyani Industrial Gases Pvt Ltd', 'Industrial Gas Production',
     'Kalyani', 'Nadia', '741235', '9830044004',
     'info@kalyanigases.example', 'https://kalyanigases.example'),
    ('Kharagpur Engineering Works', 'Industrial Manufacturing', 'Kharagpur',
     'Paschim Medinipur', '721301', '03222 255111',
     'works@kgpengineering.example', 'https://kgpengineering.example'),
    ('Purulia Steel Udyog', 'Iron & Steel Manufacturing', 'Purulia', 'Purulia',
     '723101', '9434055005', 'purulia.steel@kgpengineering.example', ''),
]

# Rows that should be caught rather than stored.
MESSY = [
    # A duplicate of row 1 with different spelling, punctuation and phone form.
    ('M/s Durgapur Iron and Steel Works Private Limited',
     'Iron & Steel Manufacturing', 'Durgapur', 'Paschim Bardhaman', '713203',
     '09832011001', 'info@durgapurironsteel.example',
     'https://www.durgapurironsteel.example'),
    # An invalid email and an impossible phone number.
    ('Test Metal Traders', 'Iron & Steel Manufacturing', 'Kolkata', 'Kolkata',
     '700001', '12345', 'not-an-email', 'ftp://broken'),
    # A search-engine listicle masquerading as a company.
    ('Top 10 Sponge Iron Manufacturers in West Bengal', 'Sponge Iron / DRI',
     'Kolkata', 'Kolkata', '700001', '9000000000', 'ads@blog.example',
     'https://blog.example'),
]


class Command(BaseCommand):
    help = 'Create sample leads (including messy ones) for a demonstration.'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true',
                            help='Delete existing companies first.')

    def handle(self, *args, **options):
        from core.ingest import CREATED, MERGED, REJECTED, REVIEW, ingest
        from leads.models import Company, DuplicateReview

        if options['clear']:
            DuplicateReview.objects.all().delete()
            Company.objects.all().delete()
            self.stdout.write('Cleared existing companies.')

        counts = {CREATED: 0, MERGED: 0, REVIEW: 0, REJECTED: 0}

        for row in SAMPLES + MESSY:
            name, industry, city, district, pin, phone, email, website = row
            outcome = ingest(
                {
                    'company_name': name, 'industry': industry, 'city': city,
                    'district': district, 'state': 'West Bengal',
                    'pin_code': pin, 'company_phone': phone,
                    'company_email': email, 'website': website,
                    'plant_address': f'Industrial Area, {city}',
                    'lead_source': 'Demo data',
                },
                origin='Demo data',
                # Offline: no DNS lookups, so the demo works anywhere.
                check_mx=False,
                require_reachability=False,
                identity_status='SITE_CONFIRMED',
            )
            counts[outcome.action] = counts.get(outcome.action, 0) + 1
            marker = {CREATED: '+', MERGED: '=', REVIEW: '?', REJECTED: 'x'}[outcome.action]
            self.stdout.write(f'  {marker} {name[:50]:52} {outcome.reason}')

        self.stdout.write(self.style.SUCCESS(
            f"\n{counts[CREATED]} created, {counts[MERGED]} merged as duplicates, "
            f"{counts[REVIEW]} queued for review, {counts[REJECTED]} rejected."))

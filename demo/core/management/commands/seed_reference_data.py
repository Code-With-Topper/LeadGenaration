"""
Load the reference data the system needs to be usable out of the box:
every district of West Bengal, the client's six target industries with their
search keywords, and two starter email templates.

Idempotent — safe to run again after a deploy.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

# All 23 districts of West Bengal. The client asked for the whole state; a
# dropdown with two of them made that impossible.
WEST_BENGAL = {
    'Alipurduar': ['Alipurduar'],
    'Bankura': ['Bankura', 'Bishnupur'],
    'Birbhum': ['Suri', 'Bolpur', 'Rampurhat'],
    'Cooch Behar': ['Cooch Behar'],
    'Dakshin Dinajpur': ['Balurghat'],
    'Darjeeling': ['Darjeeling', 'Siliguri'],
    'Hooghly': ['Chinsurah', 'Serampore', 'Dankuni', 'Tarakeswar', 'Bansberia'],
    'Howrah': ['Howrah', 'Bally', 'Uluberia', 'Dasnagar', 'Liluah'],
    'Jalpaiguri': ['Jalpaiguri', 'Dhupguri'],
    'Jhargram': ['Jhargram'],
    'Kalimpong': ['Kalimpong'],
    'Kolkata': ['Kolkata', 'Salt Lake', 'Behala', 'Tangra'],
    'Malda': ['English Bazar', 'Malda'],
    'Murshidabad': ['Berhampore', 'Jangipur'],
    'Nadia': ['Krishnanagar', 'Kalyani', 'Ranaghat', 'Haringhata'],
    'North 24 Parganas': ['Barrackpore', 'Barasat', 'Dum Dum', 'Naihati',
                          'Ichapur', 'Titagarh', 'Kamarhati'],
    'Paschim Bardhaman': ['Durgapur', 'Asansol', 'Raniganj', 'Jamuria',
                          'Kulti', 'Barakar'],
    'Paschim Medinipur': ['Medinipur', 'Kharagpur', 'Ghatal'],
    'Purba Bardhaman': ['Bardhaman', 'Kalna', 'Katwa', 'Memari'],
    'Purba Medinipur': ['Haldia', 'Tamluk', 'Digha', 'Contai'],
    'Purulia': ['Purulia', 'Raghunathpur'],
    'South 24 Parganas': ['Budge Budge', 'Baruipur', 'Maheshtala', 'Falta'],
    'Uttar Dinajpur': ['Raiganj', 'Islampur'],
}

# Neighbouring steel belt districts, so the client can widen the search later
# without waiting for a code change.
OTHER_STATES = {
    'Jharkhand': {
        'Bokaro': ['Bokaro Steel City'],
        'East Singhbhum': ['Jamshedpur', 'Adityapur'],
        'Dhanbad': ['Dhanbad'],
        'Ranchi': ['Ranchi'],
        'Saraikela Kharsawan': ['Saraikela'],
    },
    'Odisha': {
        'Sundargarh': ['Rourkela', 'Rajgangpur'],
        'Jharsuguda': ['Jharsuguda'],
        'Kendujhar': ['Joda', 'Barbil'],
        'Angul': ['Angul', 'Talcher'],
        'Khordha': ['Bhubaneswar'],
    },
}

# The client's exact target list, each with the phrases that actually find
# these companies in India.
INDUSTRIES = {
    'Iron & Steel Manufacturing': [
        'steel manufacturer', 'iron and steel company', 'steel plant',
        'TMT bar manufacturer', 'steel billet manufacturer',
        'MS ingot manufacturer', 'steel re-rolling',
    ],
    'Sponge Iron / DRI': [
        'sponge iron manufacturer', 'DRI plant', 'direct reduced iron plant',
        'sponge iron plant', 'sponge iron supplier',
    ],
    'Foundry / Cast Iron': [
        'iron foundry', 'cast iron foundry', 'grey iron castings',
        'casting manufacturer', 'ductile iron castings', 'CI casting works',
    ],
    'Rolling Mills': [
        'rolling mill', 're-rolling mill', 'hot rolling mill',
        'structural steel rolling mill', 'angle channel manufacturer',
    ],
    'Ferro Alloys': [
        'ferro alloys manufacturer', 'ferro manganese producer',
        'ferro silicon manufacturer', 'silico manganese plant',
        'ferro alloy plant',
    ],
    'Industrial Gas Production': [
        'industrial gas manufacturer', 'oxygen gas plant',
        'nitrogen gas supplier', 'acetylene gas manufacturer',
        'industrial oxygen supplier', 'air separation unit',
    ],
    'Industrial Manufacturing': [
        'engineering works', 'heavy engineering company',
        'steel fabrication unit', 'industrial equipment manufacturer',
        'machine shop', 'structural fabrication',
    ],
}

TEMPLATES = [
    {
        'name': 'First approach — company profile',
        'subject': 'Supplier introduction for {{company_name}}',
        'is_default': True,
        'body': """Dear {{contact_name}},

I am writing from {{sender_name}}. We supply to companies in the
{{industry}} sector and I believe we may be able to support your
requirements at {{company_name}}.

Our company profile is attached. We would be glad to be considered for
your approved supplier list.

If you could share the name of the person who handles purchase, I will
send our rates directly to them.

Thank you for your time.

Regards,
{{sender_name}}
""",
    },
    {
        'name': 'Follow-up after profile sent',
        'subject': 'Following up — {{company_name}}',
        'is_default': False,
        'body': """Dear {{contact_name}},

I sent our company profile to {{company_name}} a few days ago and wanted
to check whether it reached the right person.

If you have any current or upcoming requirement, please let me know the
item and quantity and I will send our best rate the same day.

If this is not relevant to you at the moment, do let me know and I will
not follow up again.

Regards,
{{sender_name}}
""",
    },
]


class Command(BaseCommand):
    help = 'Load districts, industries, search keywords and email templates.'

    @transaction.atomic
    def handle(self, *args, **options):
        from emails.models import EmailTemplate
        from lead_generation.models import City, District, Industry, SearchKeyword

        districts = cities = 0
        states = {'West Bengal': WEST_BENGAL, **OTHER_STATES}
        for state, mapping in states.items():
            for district_name, city_names in mapping.items():
                district, made = District.objects.get_or_create(
                    state=state, name=district_name)
                districts += bool(made)
                for city_name in city_names:
                    _, made = City.objects.get_or_create(district=district,
                                                         name=city_name)
                    cities += bool(made)

        industries = keywords = 0
        for order, (name, phrases) in enumerate(INDUSTRIES.items(), start=1):
            industry, made = Industry.objects.get_or_create(
                name=name, defaults={'sort_order': order})
            industries += bool(made)
            for phrase in phrases:
                _, made = SearchKeyword.objects.get_or_create(
                    industry=industry, keyword=phrase)
                keywords += bool(made)

        templates = 0
        for spec in TEMPLATES:
            _, made = EmailTemplate.objects.get_or_create(
                name=spec['name'],
                defaults={'subject': spec['subject'], 'body': spec['body'],
                          'is_default': spec['is_default']})
            templates += bool(made)

        self.stdout.write(self.style.SUCCESS(
            f'Reference data ready: +{districts} districts, +{cities} cities, '
            f'+{industries} industries, +{keywords} keywords, '
            f'+{templates} email templates.'))
        self.stdout.write(
            f'West Bengal now has '
            f'{District.objects.filter(state="West Bengal").count()} districts '
            f'available for lead generation.')

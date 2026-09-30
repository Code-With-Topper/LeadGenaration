"""
Recompute the matching keys and quality scores for every company.

Run after upgrading, or after changing a normalisation rule. Without this,
companies stored before the change keep empty matching keys and can never be
recognised as duplicates.
"""
from django.core.management.base import BaseCommand

from core import normalize, quality


class Command(BaseCommand):
    help = 'Rebuild normalized_* columns and quality scores on every company.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without saving.')

    def handle(self, *args, **options):
        from leads.models import Company, Contact

        dry_run = options['dry_run']
        changed = scored = 0

        for company in Company.objects.prefetch_related('plants', 'contacts') \
                                      .iterator(chunk_size=500):
            fields = []

            name = normalize.normalize_name(company.company_name)
            if name != company.normalized_name:
                company.normalized_name = name
                fields.append('normalized_name')

            domain = normalize.normalize_domain(company.website)
            if domain != company.normalized_domain:
                company.normalized_domain = domain
                fields.append('normalized_domain')

            email = normalize.normalize_email(company.company_email)
            if email != company.normalized_email:
                company.normalized_email = email
                fields.append('normalized_email')

            phone = normalize.normalize_phone(company.company_phone)
            if phone != company.normalized_phone:
                company.normalized_phone = phone
                fields.append('normalized_phone')

            score = quality.score_company(company)
            if score != company.data_quality_score:
                company.data_quality_score = score
                fields.append('data_quality_score')
                scored += 1

            if fields and not dry_run:
                company.save(update_fields=fields + ['updated_at'])
            if fields:
                changed += 1

        for contact in Contact.objects.exclude(email='').iterator(chunk_size=500):
            email = normalize.normalize_email(contact.email)
            if email != contact.normalized_email and not dry_run:
                contact.normalized_email = email
                contact.save(update_fields=['normalized_email', 'updated_at'])

        verb = 'would change' if dry_run else 'updated'
        self.stdout.write(self.style.SUCCESS(
            f'{changed} companies {verb}; {scored} quality scores recalculated.'))

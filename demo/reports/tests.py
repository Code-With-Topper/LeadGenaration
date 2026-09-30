"""Data-quality report tests: the numbers must be real, not placeholders."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from leads.models import Company, DuplicateReview, Lead, Plant
from reports.models import AuditLog
from reports.utils import log_audit


class ReportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)

    def test_the_duplicate_count_is_queried_not_hardcoded(self):
        """It was hardcoded to zero before, whatever the database held."""
        company = Company.objects.create(company_name='SD Steel')
        DuplicateReview.objects.create(existing_company=company,
                                       incoming={'company_name': 'S D Steel'})
        DuplicateReview.objects.create(existing_company=company,
                                       incoming={'company_name': 'SD Steel Ltd'},
                                       status='MERGED')

        response = self.client.get(reverse('reports:dashboard'))
        stats = response.context['duplicate_stats']
        self.assertEqual(stats['pending_review'], 1)
        self.assertEqual(stats['resolved'], 1)
        self.assertEqual(stats['merged'], 1)

    def test_reachability_is_counted_honestly(self):
        Company.objects.create(company_name='Has Email',
                               normalized_email='a@b.example')
        Company.objects.create(company_name='Has Phone',
                               normalized_phone='+919832011001')
        Company.objects.create(company_name='Has Nothing')

        response = self.client.get(reverse('reports:dashboard'))
        stats = response.context['lead_stats']
        self.assertEqual(stats['companies'], 3)
        self.assertEqual(stats['reachable'], 2)
        self.assertEqual(stats['unreachable'], 1)

    def test_mx_verified_is_separate_from_merely_having_an_email(self):
        Company.objects.create(company_name='Checked',
                               normalized_email='a@b.example',
                               email_status='MX_OK')
        Company.objects.create(company_name='Unchecked',
                               normalized_email='c@d.example',
                               email_status='SYNTAX_OK')

        stats = self.client.get(reverse('reports:dashboard')).context['lead_stats']
        self.assertEqual(stats['with_email'], 2)
        self.assertEqual(stats['mx_verified'], 1)

    def test_quality_bands_are_bucketed(self):
        for score in (95, 85, 70, 50, 10):
            Company.objects.create(company_name=f'Co {score}',
                                   data_quality_score=score)
        buckets = self.client.get(
            reverse('reports:dashboard')).context['quality_buckets']
        self.assertEqual(buckets['A'], 2)
        self.assertEqual(buckets['B'], 1)
        self.assertEqual(buckets['C'], 1)
        self.assertEqual(buckets['D'], 1)

    def test_leads_are_broken_down_by_district(self):
        company = Company.objects.create(company_name='Durgapur Co')
        Plant.objects.create(company=company, district='Paschim Bardhaman')
        Lead.objects.create(company=company)

        data = self.client.get(reverse('reports:dashboard')).context['district_data']
        self.assertEqual(list(data)[0]['district'], 'Paschim Bardhaman')


class AuditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)

    def test_an_entry_is_written(self):
        log_audit('Test Action', 'Lead', 7, 'something happened', self.user)
        entry = AuditLog.objects.get()
        self.assertEqual(entry.action, 'Test Action')
        self.assertEqual(entry.object_id, '7')
        self.assertEqual(entry.performed_by, self.user)

    def test_an_anonymous_user_is_recorded_as_system(self):
        from django.contrib.auth.models import AnonymousUser
        log_audit('Test Action', user=AnonymousUser())
        self.assertIsNone(AuditLog.objects.get().performed_by)

    def test_an_audit_failure_never_breaks_the_action(self):
        """The user's work matters more than the log entry."""
        entry = log_audit('X' * 5000, 'Lead', 1)
        # Either it was stored or it was not, but nothing raised.
        self.assertTrue(entry is None or AuditLog.objects.exists())

    def test_the_log_page_can_be_filtered(self):
        log_audit('Email Sent', 'EmailLog', 1)
        log_audit('Lead Updated', 'Lead', 2)

        response = self.client.get(reverse('reports:audit_logs'),
                                   {'action': 'Email'})
        self.assertEqual(len(response.context['logs']), 1)

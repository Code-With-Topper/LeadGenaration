"""Dashboard and access-control tests."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from leads.models import Company, DuplicateReview, Lead


class AccessTests(TestCase):
    def test_the_dashboard_needs_a_login(self):
        response = self.client.get(reverse('dashboard-index'))
        self.assertEqual(response.status_code, 302)

    def test_the_login_page_is_public(self):
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)

    def test_the_public_pages_are_public(self):
        for name in ('public-home', 'privacy', 'terms', 'robots', 'sitemap'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_after_signing_in_the_user_lands_on_the_dashboard(self):
        User.objects.create_user('tester', password='secret')
        response = self.client.post(reverse('login'),
                                    {'username': 'tester', 'password': 'secret'})
        self.assertRedirects(response, reverse('dashboard-index'))

    def test_a_wrong_password_does_not_sign_anyone_in(self):
        User.objects.create_user('tester', password='secret')
        response = self.client.post(reverse('login'),
                                    {'username': 'tester', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Wrong username or password')

    def test_a_protected_page_returns_the_user_after_signing_in(self):
        response = self.client.get(reverse('leads:index'))
        self.assertIn('next=/leads/', response['Location'])

    def test_search_engines_are_kept_out_of_the_crm(self):
        body = self.client.get(reverse('robots')).content.decode()
        for path in ('/leads/', '/crm/', '/emails/', '/reports/'):
            self.assertIn(f'Disallow: {path}', body)


class TileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)

    def test_there_is_one_tile_per_status_plus_a_total(self):
        response = self.client.get(reverse('dashboard-index'))
        labels = [tile['label'] for tile in response.context['tiles']]
        self.assertEqual(labels, [
            'Total Leads', 'New Lead', 'Called', 'Profile Sent',
            'Follow-up Due', 'Requirement Received', 'Converted',
            'Not Relevant'])

    def test_the_counts_are_real(self):
        for status, count in ((Lead.NEW, 3), (Lead.CONVERTED, 2)):
            for index in range(count):
                company = Company.objects.create(
                    company_name=f'{status} {index}')
                Lead.objects.create(company=company, status=status)

        response = self.client.get(reverse('dashboard-index'))
        tiles = {tile['key']: tile['count'] for tile in response.context['tiles']}
        self.assertEqual(tiles['total'], 5)
        self.assertEqual(tiles[Lead.NEW], 3)
        self.assertEqual(tiles[Lead.CONVERTED], 2)

    def test_overdue_follow_ups_are_surfaced(self):
        company = Company.objects.create(company_name='Overdue Co')
        Lead.objects.create(company=company, status=Lead.PROFILE_SENT,
                            follow_up_date=timezone.localdate() - timedelta(days=2))

        response = self.client.get(reverse('dashboard-index'))
        self.assertEqual(response.context['overdue_followups'], 1)
        self.assertContains(response, 'Needs your attention')

    def test_pending_duplicate_reviews_are_surfaced(self):
        company = Company.objects.create(company_name='SD Steel')
        DuplicateReview.objects.create(existing_company=company,
                                       incoming={'company_name': 'S D Steel'})
        response = self.client.get(reverse('dashboard-index'))
        self.assertEqual(response.context['pending_reviews'], 1)

    @override_settings(EMAIL_DAILY_LIMIT=10)
    def test_the_daily_email_allowance_is_shown(self):
        response = self.client.get(reverse('dashboard-index'))
        self.assertEqual(response.context['quota_limit'], 10)
        self.assertEqual(response.context['quota_left'], 10)

    def test_the_badges_appear_on_every_page(self):
        """The context processor puts them in the sidebar everywhere."""
        company = Company.objects.create(company_name='Overdue Co')
        Lead.objects.create(company=company, status=Lead.PROFILE_SENT,
                            follow_up_date=timezone.localdate())
        for name in ('leads:index', 'crm:pipeline', 'reports:dashboard'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.context['nav_followups_due'], 1, name)

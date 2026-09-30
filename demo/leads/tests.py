"""
Lead list, detail, review screen and export tests.

The 10,000-lead requirement is mostly about these pages: they must page in the
database rather than loading everything into Python.
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from leads.models import Company, Contact, DuplicateReview, Lead, Plant


def make_company(name, **kwargs):
    defaults = {
        'normalized_name': name.lower(),
        'industry': 'Iron & Steel',
        'data_quality_score': 50,
    }
    defaults.update(kwargs)
    return Company.objects.create(company_name=name, **defaults)


class AuthenticationTests(TestCase):
    def test_every_lead_page_requires_a_login(self):
        for name, args in (('leads:index', []), ('leads:company_list', []),
                           ('leads:contacts_index', []),
                           ('leads:review_list', [])):
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn('/login/', response['Location'])


class ListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)

    @override_settings(PAGE_SIZE=10)
    def test_the_list_is_paginated(self):
        """Without this the 10,000-lead requirement cannot be met."""
        for index in range(25):
            Lead.objects.create(company=make_company(f'Company {index:03d}'))

        response = self.client.get(reverse('leads:index'))
        page = response.context['page_obj']

        self.assertEqual(len(page.object_list), 10)
        self.assertEqual(page.paginator.count, 25)
        self.assertEqual(page.paginator.num_pages, 3)

    @override_settings(PAGE_SIZE=10)
    def test_a_filter_survives_paging(self):
        for index in range(15):
            Lead.objects.create(company=make_company(f'Company {index:03d}'),
                                status=Lead.CALLED)
        response = self.client.get(reverse('leads:index'),
                                   {'status': Lead.CALLED, 'page': 2})
        self.assertEqual(response.context['page_obj'].number, 2)
        self.assertEqual(response.context['status'], Lead.CALLED)

    def test_search_covers_the_fields_someone_would_actually_type(self):
        company = make_company('SD Steel Pvt Ltd',
                               company_email='info@sdsteel.example',
                               normalized_phone='+919832011001',
                               cin='U27100WB2005PTC123456')
        Lead.objects.create(company=company)
        Lead.objects.create(company=make_company('Haldia Ferro Alloys'))

        for term in ('SD Steel', 'sdsteel', '9832011001',
                     'U27100WB2005PTC123456'):
            response = self.client.get(reverse('leads:index'), {'q': term})
            names = [lead.company.company_name
                     for lead in response.context['leads']]
            self.assertEqual(names, ['SD Steel Pvt Ltd'], f'search for {term}')

    def test_the_company_search_box_works(self):
        """This raised NameError before — Q was used but never imported."""
        Lead.objects.create(company=make_company('SD Steel Pvt Ltd'))
        response = self.client.get(reverse('leads:company_list'), {'q': 'steel'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['companies']), 1)

    def test_filtering_by_quality_band(self):
        Lead.objects.create(company=make_company('Good Co', data_quality_score=90))
        Lead.objects.create(company=make_company('Weak Co', data_quality_score=20))

        response = self.client.get(reverse('leads:index'), {'band': 'A'})
        self.assertEqual([l.company.company_name for l in response.context['leads']],
                         ['Good Co'])

    def test_filtering_by_district(self):
        company = make_company('Durgapur Co')
        Plant.objects.create(company=company, city='Durgapur',
                             district='Paschim Bardhaman', state='West Bengal')
        Lead.objects.create(company=company)
        Lead.objects.create(company=make_company('Nowhere Co'))

        response = self.client.get(reverse('leads:index'),
                                   {'district': 'Paschim Bardhaman'})
        self.assertEqual(len(response.context['leads']), 1)

    def test_an_empty_database_renders_a_helpful_page(self):
        response = self.client.get(reverse('leads:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No leads yet')


class DetailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)
        self.company = make_company('SD Steel Pvt Ltd',
                                    company_email='info@sdsteel.example',
                                    normalized_email='info@sdsteel.example')
        self.lead = Lead.objects.create(company=self.company)

    def test_the_page_shows_what_is_missing(self):
        response = self.client.get(reverse('leads:detail', args=[self.lead.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('phone', response.context['missing_fields'])

    def test_email_history_appears_on_the_lead(self):
        """It silently showed nothing before: the relation was wrong."""
        from emails.models import EmailLog
        EmailLog.objects.create(lead=self.lead, recipient='info@sdsteel.example',
                                subject='Hello', message='Body')
        response = self.client.get(reverse('leads:detail', args=[self.lead.id]))
        self.assertEqual(len(response.context['emails']), 1)

    def test_notes_can_be_saved(self):
        self.client.post(reverse('leads:update_lead', args=[self.lead.id]),
                         {'requirement': 'Needs 50 MT billets',
                          'remarks': 'Spoke to Ramesh',
                          'quotation': 'QT-1',
                          'follow_up_date': '2026-11-01'})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.requirement, 'Needs 50 MT billets')
        self.assertEqual(self.lead.follow_up_date.isoformat(), '2026-11-01')

    def test_an_unreadable_date_is_refused_rather_than_stored(self):
        self.client.post(reverse('leads:update_lead', args=[self.lead.id]),
                         {'follow_up_date': 'not-a-date'})
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.follow_up_date)

    def test_marking_the_profile_sent_from_the_page(self):
        self.client.post(reverse('leads:mark_profile_sent', args=[self.lead.id]),
                         {'delay_days': '10'})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.PROFILE_SENT)
        self.assertIsNotNone(self.lead.follow_up_date)


class ReviewScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)
        self.company = make_company('Bengal Steel Rolling Works')
        Lead.objects.create(company=self.company)
        self.review = DuplicateReview.objects.create(
            existing_company=self.company,
            incoming={'company_name': 'Bengal Steel Works',
                      'normalized_name': 'bengal steel works',
                      'company_email': 'new@bengalsteel.example',
                      'city': 'Howrah'},
            match_score=72, match_reason='name 88% similar', origin='File Import')

    def test_the_queue_lists_what_is_waiting(self):
        response = self.client.get(reverse('leads:review_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['pending_count'], 1)

    def test_the_comparison_page_renders(self):
        response = self.client.get(
            reverse('leads:review_detail', args=[self.review.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bengal Steel Works')
        self.assertTrue(response.context['rows'])

    def test_a_decision_is_recorded_and_audited(self):
        from reports.models import AuditLog
        self.client.post(reverse('leads:review_resolve', args=[self.review.id]),
                         {'decision': 'merge'})
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'MERGED')
        self.assertEqual(self.review.resolved_by, self.user)
        self.assertTrue(AuditLog.objects.filter(action='Duplicate Resolved').exists())

    def test_a_review_cannot_be_decided_twice(self):
        self.client.post(reverse('leads:review_resolve', args=[self.review.id]),
                         {'decision': 'merge'})
        self.client.post(reverse('leads:review_resolve', args=[self.review.id]),
                         {'decision': 'separate'})
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'MERGED')

    def test_an_unknown_decision_is_refused(self):
        self.client.post(reverse('leads:review_resolve', args=[self.review.id]),
                         {'decision': 'nonsense'})
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'PENDING')


class ExportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)

    def test_the_export_has_all_the_fields_the_client_listed(self):
        from leads.views import EXPORT_COLUMNS
        company = make_company('SD Steel Pvt Ltd',
                               company_email='info@sdsteel.example',
                               company_phone='+919832011001',
                               cin='U27100WB2005PTC123456')
        Plant.objects.create(company=company, city='Durgapur',
                             district='Paschim Bardhaman', state='West Bengal',
                             pin_code='713203')
        Contact.objects.create(company=company, name='Ramesh',
                               designation='Purchase Manager')
        Lead.objects.create(company=company, lead_source='Web Search')

        response = self.client.get(reverse('leads:export_leads'))
        body = b''.join(response.streaming_content).decode()

        # The client asked for 33 fields; every one has a column.
        self.assertEqual(len(EXPORT_COLUMNS), 33)
        for column in EXPORT_COLUMNS:
            self.assertIn(column, body, column)

        self.assertIn('SD Steel Pvt Ltd', body)
        self.assertIn('Paschim Bardhaman', body)
        self.assertIn('Purchase Manager', body)
        self.assertIn('713203', body)

    def test_the_export_can_be_filtered_by_status(self):
        Lead.objects.create(company=make_company('Converted Co'),
                            status=Lead.CONVERTED)
        Lead.objects.create(company=make_company('New Co'), status=Lead.NEW)

        response = self.client.get(reverse('leads:export_leads'),
                                   {'status': Lead.CONVERTED})
        body = b''.join(response.streaming_content).decode()
        self.assertIn('Converted Co', body)
        self.assertNotIn('New Co', body)


class QualityScoreTests(TestCase):
    def test_a_complete_record_scores_higher_than_a_bare_one(self):
        from core import quality

        bare = make_company('Bare Co', data_quality_score=0)
        full = make_company(
            'Full Co', data_quality_score=0,
            company_email='info@full.example', normalized_email='info@full.example',
            email_status='MX_OK', company_phone='+919832011001',
            normalized_phone='+919832011001', website='https://full.example',
            normalized_domain='full.example', cin='U27100WB2005PTC123456',
            identity_status='SITE_CONFIRMED')
        Plant.objects.create(company=full, city='Durgapur', pin_code='713203',
                             plant_address='Industrial Area')
        Contact.objects.create(company=full, name='Ramesh')

        self.assertGreater(quality.score_company(full),
                           quality.score_company(bare))
        self.assertGreaterEqual(quality.score_company(full), 80)
        self.assertEqual(quality.band(quality.score_company(full))[0], 'A')

    def test_a_guessed_name_scores_lower_than_a_confirmed_one(self):
        from core import quality
        guessed = make_company('Guessed Co', identity_status='TITLE_GUESS')
        confirmed = make_company('Confirmed Co', identity_status='SITE_CONFIRMED')
        self.assertLess(quality.score_company(guessed),
                        quality.score_company(confirmed))

    def test_the_score_is_stored_not_recomputed_on_every_page_load(self):
        from core import quality
        company = make_company('Scored Co', data_quality_score=0,
                               identity_status='SITE_CONFIRMED')
        quality.refresh(company)
        company.refresh_from_db()
        self.assertGreater(company.data_quality_score, 0)

"""CRM tests: the client's seven statuses and the pipeline board."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import Activity
from leads.models import Company, Lead


class StatusChoiceTests(TestCase):
    def test_the_statuses_are_exactly_what_the_client_asked_for(self):
        """
        Client's list: New Lead, Called, Profile Sent, Follow-up Due,
        Requirement Received, Converted, Not Relevant. No extras — an unused
        status is a decision the client has to make for no reason.
        """
        self.assertEqual(
            [label for _value, label in Lead.STATUS_CHOICES],
            ['New Lead', 'Called', 'Profile Sent', 'Follow-up Due',
             'Requirement Received', 'Converted', 'Not Relevant'])


class StatusChangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)
        company = Company.objects.create(company_name='SD Steel Pvt Ltd')
        self.lead = Lead.objects.create(company=company)

    def post_status(self, status):
        return self.client.post(reverse('crm:update_status', args=[self.lead.id]),
                                {'status': status})

    def test_a_status_change_is_recorded_as_activity(self):
        self.post_status(Lead.CALLED)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.CALLED)
        self.assertTrue(Activity.objects.filter(lead=self.lead).exists())

    def test_moving_to_profile_sent_schedules_the_reminder(self):
        """Whichever route the user takes, the reminder is set the same way."""
        self.post_status(Lead.PROFILE_SENT)
        self.lead.refresh_from_db()
        self.assertTrue(self.lead.profile_sent)
        self.assertIsNotNone(self.lead.follow_up_date)

    def test_closing_a_lead_clears_its_reminder(self):
        self.lead.follow_up_date = timezone.localdate() + timedelta(days=3)
        self.lead.save()

        self.post_status(Lead.CONVERTED)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.follow_up_date)

    def test_an_invalid_status_is_refused(self):
        # The old system wrote 'FOLLOW_UP', which was not a valid choice, and
        # the lead then appeared in no pipeline column at all.
        self.post_status('FOLLOW_UP')
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.NEW)

    def test_the_drag_and_drop_call_answers_json(self):
        response = self.client.post(
            reverse('crm:update_status', args=[self.lead.id]),
            {'status': Lead.CALLED},
            headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])


class PipelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)

    def test_every_status_gets_a_column_with_a_true_total(self):
        for index in range(3):
            company = Company.objects.create(company_name=f'Company {index}')
            Lead.objects.create(company=company, status=Lead.NEW)

        response = self.client.get(reverse('crm:pipeline'))
        columns = response.context['columns']

        self.assertEqual(len(columns), len(Lead.STATUS_CHOICES))
        new_column = next(c for c in columns if c['status'] == Lead.NEW)
        self.assertEqual(new_column['total'], 3)

    def test_a_column_shows_a_preview_not_every_lead(self):
        """At 10,000 leads a column must not try to render them all."""
        from crm.views import CARDS_PER_COLUMN
        for index in range(CARDS_PER_COLUMN + 5):
            company = Company.objects.create(company_name=f'Company {index}')
            Lead.objects.create(company=company, status=Lead.NEW)

        response = self.client.get(reverse('crm:pipeline'))
        column = next(c for c in response.context['columns']
                      if c['status'] == Lead.NEW)
        self.assertEqual(len(column['leads']), CARDS_PER_COLUMN)
        self.assertEqual(column['total'], CARDS_PER_COLUMN + 5)
        self.assertEqual(column['more'], 5)

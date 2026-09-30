"""
Follow-up reminder tests.

The client's requirement: "7 or 10 days after the profile is sent, remind me."
The previous system had a `follow_up_date` field that nothing ever wrote and no
scheduler to act on it, so these tests cover the whole chain — setting the
date, the daily sweep that acts on it, and not chasing people who are done.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import worker
from followups.models import FollowUp
from leads.models import Company, Lead


def make_lead(name='SD Steel Pvt Ltd'):
    company = Company.objects.create(company_name=name,
                                     normalized_name=name.lower())
    return Lead.objects.create(company=company)


@override_settings(FOLLOW_UP_DELAY_DAYS=7)
class ScheduleTests(TestCase):
    def test_marking_the_profile_sent_sets_the_reminder(self):
        lead = make_lead()
        lead.mark_profile_sent()

        self.assertEqual(lead.status, Lead.PROFILE_SENT)
        self.assertEqual(lead.profile_sent_at, timezone.localdate())
        self.assertEqual(lead.follow_up_date,
                         timezone.localdate() + timedelta(days=7))

    def test_ten_days_can_be_chosen_per_lead(self):
        lead = make_lead()
        lead.mark_profile_sent(delay_days=10)
        self.assertEqual(lead.follow_up_date,
                         timezone.localdate() + timedelta(days=10))

    def test_the_first_contact_date_is_recorded_once(self):
        lead = make_lead()
        lead.mark_profile_sent()
        first = lead.first_contact_date
        lead.mark_profile_sent()
        self.assertEqual(lead.first_contact_date, first)


class SweepTests(TestCase):
    """The daily job that turns a date into something the client can see."""

    def test_a_lead_whose_date_has_arrived_becomes_follow_up_due(self):
        lead = make_lead()
        lead.status = Lead.PROFILE_SENT
        lead.follow_up_date = timezone.localdate()
        lead.save()

        moved = worker.sweep_followups()

        lead.refresh_from_db()
        self.assertEqual(moved, 1)
        self.assertEqual(lead.status, Lead.FOLLOW_UP_DUE)

    def test_an_overdue_lead_is_picked_up_too(self):
        lead = make_lead()
        lead.status = Lead.PROFILE_SENT
        lead.follow_up_date = timezone.localdate() - timedelta(days=30)
        lead.save()

        worker.sweep_followups()
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.FOLLOW_UP_DUE)
        self.assertTrue(lead.is_follow_up_overdue)

    def test_a_future_date_is_left_alone(self):
        lead = make_lead()
        lead.status = Lead.PROFILE_SENT
        lead.follow_up_date = timezone.localdate() + timedelta(days=5)
        lead.save()

        self.assertEqual(worker.sweep_followups(), 0)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.PROFILE_SENT)

    def test_converted_and_not_relevant_leads_are_never_chased(self):
        for status in (Lead.CONVERTED, Lead.NOT_RELEVANT):
            lead = make_lead(f'Company {status}')
            lead.status = status
            lead.follow_up_date = timezone.localdate() - timedelta(days=1)
            lead.save()

        self.assertEqual(worker.sweep_followups(), 0)

    def test_the_sweep_is_safe_to_run_twice(self):
        lead = make_lead()
        lead.status = Lead.PROFILE_SENT
        lead.follow_up_date = timezone.localdate()
        lead.save()

        self.assertEqual(worker.sweep_followups(), 1)
        self.assertEqual(worker.sweep_followups(), 0)

    def test_the_management_command_runs(self):
        from io import StringIO
        from django.core.management import call_command

        lead = make_lead()
        lead.status = Lead.PROFILE_SENT
        lead.follow_up_date = timezone.localdate()
        lead.save()

        out = StringIO()
        call_command('process_followups', stdout=out)
        self.assertIn('1 lead', out.getvalue())


class SnoozeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)

    def test_snoozing_moves_the_date_and_clears_the_due_flag(self):
        lead = make_lead()
        lead.status = Lead.FOLLOW_UP_DUE
        lead.profile_sent = True
        lead.follow_up_date = timezone.localdate()
        lead.save()

        self.client.post(reverse('followups:snooze', args=[lead.id]), {'days': 14})

        lead.refresh_from_db()
        self.assertEqual(lead.follow_up_date,
                         timezone.localdate() + timedelta(days=14))
        # Left as Follow-up Due it would show as overdue again tomorrow.
        self.assertEqual(lead.status, Lead.PROFILE_SENT)
        self.assertEqual(lead.follow_up_count, 1)

    def test_a_silly_snooze_length_is_clamped(self):
        lead = make_lead()
        self.client.post(reverse('followups:snooze', args=[lead.id]),
                         {'days': '99999'})
        lead.refresh_from_db()
        self.assertLessEqual(
            (lead.follow_up_date - timezone.localdate()).days, 365)


class ManualTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)
        self.lead = make_lead()

    def test_a_task_can_be_scheduled_from_a_lead(self):
        date = (timezone.localdate() + timedelta(days=3)).isoformat()
        self.client.post(reverse('followups:create', args=[self.lead.id]),
                         {'date': date, 'type': 'CALL', 'notes': 'Ring Ramesh'})

        task = FollowUp.objects.get()
        self.assertEqual(task.lead, self.lead)
        self.assertEqual(task.notes, 'Ring Ramesh')
        # The lead's own reminder moves to the earliest open task.
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.follow_up_date.isoformat(), date)

    def test_a_task_with_no_date_is_refused(self):
        self.client.post(reverse('followups:create', args=[self.lead.id]),
                         {'type': 'CALL'})
        self.assertEqual(FollowUp.objects.count(), 0)

    def test_a_task_can_be_marked_done(self):
        task = FollowUp.objects.create(lead=self.lead,
                                       date=timezone.localdate())
        self.client.post(reverse('followups:complete', args=[task.id]))
        task.refresh_from_db()
        self.assertEqual(task.status, 'COMPLETED')
        self.assertIsNotNone(task.completed_at)


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)

    def test_overdue_today_and_upcoming_are_counted_separately(self):
        today = timezone.localdate()
        for offset, name in ((-3, 'Overdue Co'), (0, 'Today Co'), (5, 'Soon Co')):
            lead = make_lead(name)
            lead.status = Lead.PROFILE_SENT
            lead.follow_up_date = today + timedelta(days=offset)
            lead.save()

        response = self.client.get(reverse('followups:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['overdue_count'], 1)
        self.assertEqual(response.context['today_count'], 1)
        self.assertEqual(response.context['upcoming_count'], 1)

"""
Email tests.

Three of the client's requirements live here: about ten emails a day, a record
of what was sent, and a follow-up reminder afterwards. Each is enforced in
code, so each is tested.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail, signing
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from emails.models import EmailLog, EmailQuota, EmailTemplate, unsubscribe_url
from emails.services import check_can_send, send_to_lead
from leads.models import Company, Contact, Lead, Suppression


def make_lead(name='SD Steel Pvt Ltd', email='info@sdsteel.example'):
    company = Company.objects.create(
        company_name=name, normalized_name=name.lower(),
        company_email=email, normalized_email=email,
        industry='Iron & Steel', email_status='MX_OK')
    return Lead.objects.create(company=company)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   DEFAULT_FROM_EMAIL='enquiry@sdenterprise.example',
                   EMAIL_DAILY_LIMIT=3, SITE_URL='https://crm.example')
class SendingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.lead = make_lead()
        mail.outbox = []

    def test_a_plain_send_works_and_is_logged(self):
        result = send_to_lead(self.lead, 'Introduction', 'Hello', user=self.user)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['info@sdsteel.example'])
        self.assertEqual(mail.outbox[0].from_email, 'enquiry@sdenterprise.example')

        log = EmailLog.objects.get()
        self.assertEqual(log.status, 'SENT')
        self.assertEqual(log.sent_by, self.user)

    def test_every_message_carries_a_working_unsubscribe_link(self):
        send_to_lead(self.lead, 'Introduction', 'Hello')
        body = mail.outbox[0].body
        self.assertIn('https://crm.example/emails/unsubscribe/', body)
        self.assertIn('List-Unsubscribe', mail.outbox[0].extra_headers)

    # -- the daily cap -----------------------------------------------------

    def test_the_daily_limit_is_enforced(self):
        """The client asked for about ten a day; the code refuses the eleventh."""
        leads = [make_lead(f'Company {i}', f'c{i}@x.example') for i in range(5)]
        sent = [send_to_lead(lead, 'Subject', 'Body').ok for lead in leads]

        self.assertEqual(sent.count(True), 3, 'limit of 3 was not enforced')
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(EmailQuota.remaining_today(), 0)

    def test_a_blocked_send_is_logged_with_the_reason(self):
        for index in range(3):
            send_to_lead(make_lead(f'C{index}', f'c{index}@x.example'), 'S', 'B')
        result = send_to_lead(self.lead, 'Subject', 'Body')

        self.assertFalse(result.ok)
        self.assertEqual(result.status, 'BLOCKED')
        log = EmailLog.objects.filter(status='BLOCKED').get()
        self.assertIn('limit', log.error_message.lower())

    def test_a_blocked_send_does_not_consume_quota(self):
        Suppression.add_email('info@sdsteel.example')
        send_to_lead(self.lead, 'Subject', 'Body')
        self.assertEqual(EmailQuota.used_today(), 0)

    def test_quota_counts_only_today(self):
        EmailQuota.objects.create(date=timezone.localdate() - timedelta(days=1),
                                  sent_count=99)
        self.assertEqual(EmailQuota.used_today(), 0)
        self.assertEqual(EmailQuota.remaining_today(), 3)

    # -- suppression -------------------------------------------------------

    def test_an_unsubscribed_address_is_never_emailed(self):
        Suppression.add_email('info@sdsteel.example')
        result = send_to_lead(self.lead, 'Subject', 'Body')
        self.assertFalse(result.ok)
        self.assertEqual(len(mail.outbox), 0)

    def test_suppression_ignores_case_and_spacing(self):
        Suppression.add_email('  INFO@SDSTEEL.EXAMPLE ')
        self.assertTrue(Suppression.blocks_email('info@sdsteel.example'))

    def test_a_closed_lead_is_never_emailed(self):
        self.lead.status = Lead.CONVERTED
        self.lead.save()
        result = send_to_lead(self.lead, 'Subject', 'Body')
        self.assertFalse(result.ok)
        self.assertIn('Converted', result.message)

    def test_a_lead_with_an_invalid_address_is_refused(self):
        company = Company.objects.create(company_name='No Mail Co')
        lead = Lead.objects.create(company=company)
        allowed, reason = check_can_send(lead)
        self.assertFalse(allowed)
        self.assertIn('no valid email', reason.lower())

    # -- what a send does to the lead --------------------------------------

    def test_a_first_email_moves_the_lead_to_called(self):
        send_to_lead(self.lead, 'Subject', 'Body')
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.CALLED)
        self.assertIsNotNone(self.lead.first_contact_date)

    @override_settings(FOLLOW_UP_DELAY_DAYS=7)
    def test_sending_the_profile_schedules_the_follow_up(self):
        """The client's 7/10-day reminder, set the moment the profile goes out."""
        send_to_lead(self.lead, 'Our profile', 'Attached', mark_profile_sent=True)
        self.lead.refresh_from_db()

        self.assertEqual(self.lead.status, Lead.PROFILE_SENT)
        self.assertTrue(self.lead.profile_sent)
        self.assertEqual(self.lead.follow_up_date,
                         timezone.localdate() + timedelta(days=7))

    @override_settings(FOLLOW_UP_DELAY_DAYS=10)
    def test_the_delay_is_configurable(self):
        send_to_lead(self.lead, 'Our profile', 'Attached', mark_profile_sent=True)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.follow_up_date,
                         timezone.localdate() + timedelta(days=10))

    def test_a_contact_address_is_preferred_over_the_company_one(self):
        contact = Contact.objects.create(
            company=self.lead.company, name='Ramesh',
            email='ramesh@sdsteel.example',
            normalized_email='ramesh@sdsteel.example')
        self.lead.contact = contact
        self.lead.save()
        send_to_lead(self.lead, 'Subject', 'Body')
        self.assertEqual(mail.outbox[0].to, ['ramesh@sdsteel.example'])

    def test_a_send_failure_is_recorded_not_swallowed(self):
        with override_settings(
                EMAIL_BACKEND='django.core.mail.backends.dummy.NoSuchBackend'):
            result = send_to_lead(self.lead, 'Subject', 'Body')
        self.assertFalse(result.ok)
        self.assertEqual(result.status, 'FAILED')
        log = EmailLog.objects.get()
        self.assertEqual(log.status, 'FAILED')
        self.assertTrue(log.error_message)
        # A failure must not burn a slot from the daily allowance.
        self.assertEqual(EmailQuota.used_today(), 0)


@override_settings(SITE_URL='https://crm.example')
class UnsubscribeTests(TestCase):
    def setUp(self):
        self.lead = make_lead()

    def test_clicking_the_link_blocks_the_address_for_good(self):
        url = unsubscribe_url('info@sdsteel.example')
        response = self.client.get(url.replace('https://crm.example', ''))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Suppression.blocks_email('info@sdsteel.example'))

        allowed, _reason = check_can_send(self.lead)
        self.assertFalse(allowed, 'the address must now be refused')

    def test_unsubscribing_also_closes_the_lead(self):
        url = unsubscribe_url('info@sdsteel.example')
        self.client.get(url.replace('https://crm.example', ''))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.NOT_RELEVANT)
        self.assertIsNone(self.lead.follow_up_date)

    def test_a_tampered_link_is_rejected(self):
        response = self.client.get(
            reverse('emails:unsubscribe', kwargs={'token': 'made-up-token'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not valid')
        self.assertEqual(Suppression.objects.count(), 0)

    def test_unsubscribing_needs_no_login(self):
        """A recipient has no account, so this page must be public."""
        url = unsubscribe_url('info@sdsteel.example')
        response = self.client.get(url.replace('https://crm.example', ''))
        self.assertEqual(response.status_code, 200)


class TemplateRenderTests(TestCase):
    def test_placeholders_are_filled_in(self):
        lead = make_lead()
        Contact.objects.create(company=lead.company, name='Ramesh Kumar',
                               is_primary=True)
        template = EmailTemplate.objects.create(
            name='Intro',
            subject='Supplier introduction for {{company_name}}',
            body='Dear {{contact_name}},\nWe supply the {{industry}} sector.')

        subject, body = template.render(lead)
        self.assertEqual(subject, 'Supplier introduction for SD Steel Pvt Ltd')
        self.assertIn('Dear Ramesh Kumar', body)
        self.assertIn('Iron & Steel', body)
        self.assertNotIn('{{', body)

    def test_a_missing_contact_name_falls_back_politely(self):
        template = EmailTemplate.objects.create(
            name='Intro', subject='Hello', body='Dear {{contact_name}},')
        _subject, body = template.render(make_lead())
        self.assertIn('Dear Sir/Madam', body)

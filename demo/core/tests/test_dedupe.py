from django.test import TestCase

from core import dedupe
from core.ingest import CREATED, MERGED, REJECTED, REVIEW, ingest
from leads.models import Company, Contact, DuplicateReview, Lead, Plant


def raw(**overrides):
    base = {
        'company_name': 'SD Steel Pvt Ltd',
        'industry': 'Iron & Steel Manufacturing',
        'city': 'Durgapur',
        'district': 'Paschim Bardhaman',
        'state': 'West Bengal',
        'pin_code': '713203',
        'company_phone': '9832011001',
        'company_email': 'info@sdsteel.example',
        'website': 'https://sdsteel.example',
    }
    base.update(overrides)
    return base


def add(**overrides):
    """Ingest one record with network checks off, as the tests must be offline."""
    return ingest(raw(**overrides), origin='test', check_mx=False)


class HardKeyDuplicateTests(TestCase):
    """
    Tier 1: a statutory or unique contact key means the same company, full stop.
    These merge without asking anyone.
    """

    def setUp(self):
        self.first = add()
        self.assertEqual(self.first.action, CREATED)

    def test_same_website_domain_is_a_duplicate(self):
        outcome = add(company_name='SD Steel',
                      website='https://www.sdsteel.example/contact',
                      company_email='', company_phone='')
        self.assertEqual(outcome.action, MERGED)
        self.assertIn('domain', outcome.reason)
        self.assertEqual(Company.objects.count(), 1)

    def test_same_email_in_any_case_is_a_duplicate(self):
        outcome = add(company_name='Totally Different Name',
                      company_email='INFO@SDSTEEL.EXAMPLE',
                      website='', company_phone='', pin_code='700001',
                      city='Kolkata', district='Kolkata')
        self.assertEqual(outcome.action, MERGED)
        self.assertEqual(Company.objects.count(), 1)

    def test_same_phone_in_any_format_is_a_duplicate(self):
        """The old system stored '+91 98…' raw, so this never matched."""
        outcome = add(company_name='Another Name Entirely',
                      company_phone='+91 98320 11001',
                      company_email='', website='',
                      city='Kolkata', district='Kolkata', pin_code='700001')
        self.assertEqual(outcome.action, MERGED)
        self.assertEqual(Company.objects.count(), 1)

    def test_same_cin_is_a_duplicate(self):
        Company.objects.all().delete()
        add(cin='U27100WB2005PTC123456')
        outcome = add(company_name='Unrelated Name', company_email='x@y.example',
                      company_phone='9000011111', website='https://other.example',
                      cin='U27100WB2005PTC123456')
        self.assertEqual(outcome.action, MERGED)
        self.assertIn('CIN', outcome.reason)
        self.assertEqual(Company.objects.count(), 1)


class SimilarityDuplicateTests(TestCase):
    """Tier 2: no shared key, but the name and place line up."""

    def test_near_identical_name_and_pin_merges_automatically(self):
        add()
        # A different domain, email and phone, so no hard key can match and
        # only the name-plus-PIN score is left to decide.
        outcome = add(company_name='S. D. Steel Private Limited',
                      company_email='sales@sdsteel-alt.example',
                      company_phone='9832099999',
                      website='https://sdsteel-alt.example')
        self.assertEqual(outcome.action, MERGED)
        self.assertGreaterEqual(outcome.score, dedupe.AUTO_MERGE_SCORE)
        self.assertEqual(Company.objects.count(), 1)

    def test_a_partial_match_is_queued_for_a_human(self):
        add(company_name='Bengal Steel Rolling Works')
        outcome = add(company_name='Bengal Steel Works',
                      company_email='other@else.example',
                      company_phone='9000022222',
                      website='https://different.example')
        self.assertEqual(outcome.action, REVIEW)
        self.assertEqual(DuplicateReview.objects.filter(status='PENDING').count(), 1)
        # Nothing is merged behind the user's back.
        self.assertEqual(Company.objects.count(), 1)

    def test_a_genuinely_different_company_is_kept_separate(self):
        add()
        outcome = add(company_name='Haldia Ferro Alloys Pvt Ltd',
                      company_email='x@haldia.example',
                      company_phone='9163022002',
                      website='https://haldia.example',
                      city='Haldia', district='Purba Medinipur',
                      pin_code='721602')
        self.assertEqual(outcome.action, CREATED)
        self.assertEqual(Company.objects.count(), 2)


class MergeBehaviourTests(TestCase):
    """A merge must only ever add information, never lose it."""

    def test_blanks_are_filled_from_the_new_record(self):
        add(company_email='', cin='')
        add(company_name='SD Steel Pvt Ltd',
            company_email='info@sdsteel.example',
            cin='U27100WB2005PTC123456')

        company = Company.objects.get()
        self.assertEqual(company.company_email, 'info@sdsteel.example')
        self.assertEqual(company.cin, 'U27100WB2005PTC123456')
        # A CIN is stronger identity than a guessed name.
        self.assertEqual(company.identity_status, 'ROC_MATCHED')

    def test_a_verified_email_is_not_replaced_by_an_unverified_one(self):
        add()
        company = Company.objects.get()
        company.email_status = 'MX_OK'
        company.save(update_fields=['email_status'])

        add(company_email='other@sdsteel.example')   # SYNTAX_OK only

        company.refresh_from_db()
        self.assertEqual(company.company_email, 'info@sdsteel.example')
        self.assertEqual(company.email_status, 'MX_OK')

    def test_every_source_is_remembered(self):
        add(source_url='https://sdsteel.example/about')
        add(source_url='https://sdsteel.example/contact')
        company = Company.objects.get()
        urls = {entry['url'] for entry in company.source_history}
        self.assertEqual(len(urls), 2, company.source_history)

    def test_a_merge_does_not_create_a_second_lead(self):
        add()
        add(company_name='S. D. Steel Private Limited')
        self.assertEqual(Lead.objects.count(), 1)


class RejectionTests(TestCase):
    def test_a_listicle_title_never_becomes_a_company(self):
        outcome = add(company_name='Top 10 Sponge Iron Manufacturers in West Bengal')
        self.assertEqual(outcome.action, REJECTED)
        self.assertEqual(Company.objects.count(), 0)

    def test_a_directory_page_never_becomes_a_company(self):
        outcome = add(website='https://www.indiamart.com/sdsteel')
        self.assertEqual(outcome.action, REJECTED)
        self.assertIn('directory', outcome.reason)

    def test_invalid_contact_details_are_dropped_but_the_company_is_kept(self):
        outcome = ingest(
            raw(company_email='not-an-email', company_phone='9832011001'),
            origin='test', check_mx=False)
        self.assertEqual(outcome.action, CREATED)
        company = Company.objects.get()
        self.assertEqual(company.company_email, '')
        self.assertTrue(any('email' in w for w in outcome.warnings), outcome.warnings)

    def test_a_website_alone_is_enough_to_keep_a_lead(self):
        # An enquiry form is a real way in; the phone can be added later.
        outcome = ingest(
            raw(company_email='', company_phone='',
                website='https://onlyweb.example'),
            origin='test', check_mx=False, require_reachability=True)
        self.assertEqual(outcome.action, CREATED)

    def test_a_lead_with_no_way_to_contact_it_is_rejected(self):
        outcome = ingest(
            raw(company_email='', company_phone='', website=''),
            origin='test', check_mx=False, require_reachability=True)
        self.assertEqual(outcome.action, REJECTED)
        self.assertIn('no usable email or phone', outcome.reason)


class DatabaseGuardTests(TestCase):
    """
    The unique constraints are the last line of defence: even with the dedupe
    code bypassed entirely, two companies cannot share an identity.
    """

    def test_a_duplicate_email_cannot_be_forced_in(self):
        from django.db import IntegrityError, transaction
        add()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Company.objects.create(
                    company_name='Sneaky Duplicate',
                    normalized_email='info@sdsteel.example')

    def test_a_duplicate_domain_cannot_be_forced_in(self):
        from django.db import IntegrityError, transaction
        add()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Company.objects.create(company_name='Sneaky',
                                       normalized_domain='sdsteel.example')

    def test_blank_keys_do_not_collide(self):
        # Several companies legitimately have no website at all.
        for index in range(3):
            Company.objects.create(company_name=f'No Website {index}')
        self.assertEqual(Company.objects.filter(normalized_domain='').count(), 3)


class BatchIndexTests(TestCase):
    """
    Within one file, rows must be de-duplicated against each other — none of
    them is in the database yet when the others are checked.
    """

    def test_repeats_inside_one_batch_are_caught(self):
        from core.record import build_record

        index = dedupe.BatchIndex()
        first = build_record(raw(), require_reachability=False)
        seen, _row, _reason = index.seen(first)
        self.assertFalse(seen)
        index.add(first, 1)

        again = build_record(
            raw(company_name='S. D. STEEL PVT. LTD.'), require_reachability=False)
        seen, row, reason = index.seen(again)
        self.assertTrue(seen)
        self.assertEqual(row, 1)
        self.assertIn('row 1', reason)


class ReviewResolutionTests(TestCase):
    def setUp(self):
        add(company_name='Bengal Steel Rolling Works')
        add(company_name='Bengal Steel Works',
            company_email='other@else.example', company_phone='9000022222',
            website='https://different.example')
        self.review = DuplicateReview.objects.get()

    def test_merge_keeps_one_company_and_enriches_it(self):
        from core.ingest import apply_review
        apply_review(self.review, 'merge')
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'MERGED')
        self.assertEqual(Company.objects.count(), 1)

    def test_keeping_the_existing_record_discards_the_new_one(self):
        from core.ingest import apply_review
        before = Company.objects.get().company_email
        apply_review(self.review, 'existing')
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'KEPT_EXISTING')
        self.assertEqual(Company.objects.get().company_email, before)

    def test_marking_them_different_creates_a_second_company(self):
        from core.ingest import apply_review
        apply_review(self.review, 'separate')
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, 'NOT_DUPLICATE')
        self.assertEqual(Company.objects.count(), 2)

    def test_the_comparison_screen_only_shows_fields_that_differ(self):
        rows = self.review.field_comparison()
        self.assertTrue(rows)
        self.assertTrue(any(row['differs'] for row in rows))

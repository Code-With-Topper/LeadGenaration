"""
Lead generation tests.

These cover the parts that decide data quality, without opening a browser:
query building (does "all of West Bengal" really work?), how a company name is
read from a page, and what happens to each extracted record.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from lead_generation import engine
from lead_generation.models import City, District, GenerationJob, Industry, SearchKeyword
from leads.models import Company, DuplicateReview, Lead


class QueryBuildingTests(TestCase):
    def setUp(self):
        for name in ('Paschim Bardhaman', 'Purba Medinipur', 'Howrah'):
            district = District.objects.create(state='West Bengal', name=name)
            City.objects.create(district=district, name=f'{name} Town')
        District.objects.create(state='Jharkhand', name='Bokaro')

    def job(self, **kwargs):
        defaults = {'state': 'West Bengal', 'industry': 'Iron & Steel',
                    'keywords': 'sponge iron manufacturer, rolling mill'}
        defaults.update(kwargs)
        return GenerationJob.objects.create(**defaults)

    def test_a_city_run_searches_just_that_city(self):
        plans = engine.build_queries(
            self.job(district='Paschim Bardhaman', city='Durgapur'))
        self.assertEqual(len(plans), 2)            # two keywords, one place
        self.assertTrue(all('Durgapur' in plan.query for plan in plans))
        self.assertTrue(all(plan.city == 'Durgapur' for plan in plans))

    def test_a_district_run_needs_no_city(self):
        """The old form made city mandatory, so this was impossible."""
        plans = engine.build_queries(self.job(district='Paschim Bardhaman'))
        self.assertEqual(len(plans), 2)
        self.assertTrue(all('Paschim Bardhaman district' in plan.query
                            for plan in plans))
        self.assertTrue(all(plan.district == 'Paschim Bardhaman'
                            for plan in plans))

    def test_a_whole_state_run_covers_every_district(self):
        """The client asked for all of West Bengal, not two districts of it."""
        plans = engine.build_queries(self.job())
        # 3 West Bengal districts x 2 keywords. Jharkhand is not included.
        self.assertEqual(len(plans), 6)
        self.assertFalse(any('Bokaro' in plan.query for plan in plans))
        for name in ('Paschim Bardhaman', 'Purba Medinipur', 'Howrah'):
            self.assertTrue(any(name in plan.query for plan in plans), name)

    def test_coverage_comes_before_keyword_depth(self):
        """
        The first pass must sweep every district with one keyword, not spend
        every keyword on the first district. A run cut short then still has
        state-wide coverage.
        """
        plans = engine.build_queries(self.job())
        first_pass = plans[:3]
        self.assertEqual(
            {plan.district for plan in first_pass},
            {'Paschim Bardhaman', 'Purba Medinipur', 'Howrah'},
            [plan.district for plan in first_pass])

    def test_the_number_of_searches_is_capped(self):
        """
        A whole state with seven keywords is 161 searches — most of a day, and
        indistinguishable from a stuck run.
        """
        for index in range(30):
            District.objects.create(state='West Bengal', name=f'District {index}')
        job = self.job(keywords=','.join(f'keyword {i}' for i in range(7)))
        plans = engine.build_queries(job)
        self.assertLessEqual(len(plans), engine.MAX_QUERIES_PER_RUN)
        self.assertEqual(len(plans), engine.MAX_QUERIES_PER_RUN)

    def test_the_industry_is_the_fallback_keyword(self):
        plans = engine.build_queries(self.job(keywords='', district='Howrah'))
        self.assertEqual(len(plans), 1)
        self.assertIn('Iron & Steel', plans[0].query)

    def test_each_plan_carries_the_district_it_belongs_to(self):
        """
        A state-wide run must still record which district each lead came from.
        """
        plans = engine.build_queries(self.job())
        districts = {plan.district for plan in plans}
        self.assertEqual(districts,
                         {'Paschim Bardhaman', 'Purba Medinipur', 'Howrah'})
        self.assertTrue(all(plan.state == 'West Bengal' for plan in plans))
        self.assertTrue(all(plan.location['district'] for plan in plans))

    def test_seeded_reference_data_covers_all_23_wb_districts(self):
        from io import StringIO
        from django.core.management import call_command
        call_command('seed_reference_data', stdout=StringIO())
        self.assertEqual(
            District.objects.filter(state='West Bengal').count(), 23)


class SearchResultFilterTests(TestCase):
    def test_a_company_site_is_accepted(self):
        result = engine._clean_result('https://sdsteel.in/', 'SD Steel Pvt Ltd')
        self.assertIsNotNone(result)
        self.assertEqual(result[1], 'https://sdsteel.in/')

    def test_directories_and_social_sites_are_dropped(self):
        for url in ('https://www.indiamart.com/x', 'https://justdial.com/y',
                    'https://www.facebook.com/page',
                    'https://en.wikipedia.org/wiki/Steel'):
            self.assertIsNone(engine._clean_result(url, 'Some Steel Co'), url)

    def test_listicle_results_are_dropped(self):
        self.assertIsNone(engine._clean_result(
            'https://blog.example/top-sponge-iron',
            'Top 10 Sponge Iron Manufacturers in West Bengal'))

    def test_a_result_with_no_title_is_dropped(self):
        self.assertIsNone(engine._clean_result('https://sdsteel.in/', ''))

    def test_google_redirect_urls_are_decoded(self):
        self.assertEqual(
            engine._decode_google_url(
                'https://www.google.com/url?q=https://sdsteel.in/&sa=U'),
            'https://sdsteel.in/')

    def test_a_google_internal_link_is_discarded(self):
        self.assertEqual(
            engine._decode_google_url('https://www.google.com/preferences'), '')


class SearchSourceTests(TestCase):
    """
    The search layer, without a browser.

    Lead generation previously returned nothing because the browser announced
    itself as headless and Google blocked it, and because a blocked engine was
    asked 161 times in a row. These cover the replacement.
    """

    def test_duckduckgo_is_tried_before_bing_and_google(self):
        """
        DuckDuckGo renders without JavaScript and tolerates a server doing
        this politely, so it is the source most likely to answer at all.
        """
        names = [name for name, _fn in engine.SEARCH_SOURCES]
        self.assertEqual(names[0], 'DuckDuckGo')
        self.assertIn('Bing', names)
        self.assertIn('Google', names)

    def test_a_blocked_source_is_dropped_after_two_refusals(self):
        state = engine.SearchState()
        self.assertEqual(len(state.live_sources()), 3)

        for _ in range(engine.SearchState.BLOCKS_BEFORE_GIVING_UP):
            state.record('DuckDuckGo', engine.SearchOutcome.BLOCKED)

        live = [name for name, _fn in state.live_sources()]
        self.assertNotIn('DuckDuckGo', live)
        self.assertEqual(len(live), 2)
        self.assertFalse(state.all_blocked)

    def test_all_blocked_is_reported_once_every_source_refuses(self):
        state = engine.SearchState()
        for name, _fn in engine.SEARCH_SOURCES:
            for _ in range(engine.SearchState.BLOCKS_BEFORE_GIVING_UP):
                state.record(name, engine.SearchOutcome.BLOCKED)
        self.assertTrue(state.all_blocked)
        self.assertIn('blocked', state.summary())

    def test_a_success_clears_an_earlier_block(self):
        """One rate-limited request must not retire a working source."""
        state = engine.SearchState()
        state.record('Bing', engine.SearchOutcome.BLOCKED)
        state.record('Bing', engine.SearchOutcome.OK)
        state.record('Bing', engine.SearchOutcome.BLOCKED)
        self.assertIn('Bing', [name for name, _fn in state.live_sources()])

    def test_run_search_falls_through_to_the_next_source(self):
        calls = []

        def blocked(browser, query, limit):
            calls.append('blocked')
            return [], engine.SearchOutcome.BLOCKED

        def works(browser, query, limit):
            calls.append('works')
            return [('SD Steel Pvt Ltd', 'https://sdsteel.in')], \
                engine.SearchOutcome.OK

        original = engine.SEARCH_SOURCES
        engine.SEARCH_SOURCES = (('First', blocked), ('Second', works))
        try:
            state = engine.SearchState()
            results, note = engine.run_search(None, 'q', 5, state)
        finally:
            engine.SEARCH_SOURCES = original

        self.assertEqual(calls, ['blocked', 'works'])
        self.assertEqual(len(results), 1)
        self.assertIn('Second', note)

    def test_an_empty_result_says_which_sources_were_tried(self):
        """A bare "0 leads" teaches the user nothing."""
        def nothing(browser, query, limit):
            return [], engine.SearchOutcome.EMPTY

        original = engine.SEARCH_SOURCES
        engine.SEARCH_SOURCES = (('OnlyOne', nothing),)
        try:
            engine.polite_sleep = lambda: None
            _results, note = engine.run_search(None, 'q', 5,
                                               engine.SearchState())
        finally:
            engine.SEARCH_SOURCES = original

        self.assertIn('OnlyOne', note)
        self.assertIn('empty', note)

    def test_duckduckgo_redirect_urls_are_decoded(self):
        self.assertEqual(
            engine._decode_ddg_url(
                '//duckduckgo.com/l/?uddg=https%3A%2F%2Fsdsteel.in%2F&rut=x'),
            'https://sdsteel.in/')

    def test_a_duckduckgo_internal_link_is_discarded(self):
        self.assertEqual(
            engine._decode_ddg_url('https://duckduckgo.com/settings'), '')

    def test_a_direct_result_url_passes_through(self):
        self.assertEqual(engine._decode_ddg_url('https://sdsteel.in/'),
                         'https://sdsteel.in/')


class BlockDetectionTests(TestCase):
    """
    A consent wall is full of links, so "did links load?" is not enough: the
    run would happily scrape a cookie notice.
    """

    class FakePage:
        def __init__(self, url, title):
            self._url, self._title = url, title

        @property
        def url(self):
            return self._url

        def title(self):
            return self._title

    def check(self, url, title):
        browser = engine.Browser()
        browser.page = self.FakePage(url, title)
        return engine.is_blocked_page(browser)

    def test_a_captcha_page_is_detected(self):
        self.assertTrue(self.check('https://www.google.com/sorry/index',
                                   'Our systems have detected unusual traffic'))

    def test_a_google_consent_wall_is_detected(self):
        self.assertTrue(self.check('https://consent.google.com/m?continue=',
                                   'Before you continue to Google Search'))

    def test_a_cloudflare_interstitial_is_detected(self):
        self.assertTrue(self.check('https://www.bing.com/search?q=x',
                                   'Just a moment...'))

    def test_a_real_results_page_is_not_flagged(self):
        self.assertFalse(self.check(
            'https://html.duckduckgo.com/html/?q=sponge+iron',
            'sponge iron manufacturer at DuckDuckGo'))


class BrowserIdentityTests(TestCase):
    """
    The single biggest cause of lead generation finding nothing: Playwright's
    default announces HeadlessChrome and sets navigator.webdriver, and the
    search engines block that on sight.
    """

    def test_the_user_agent_is_not_headless(self):
        self.assertNotIn('Headless', engine.USER_AGENT)
        self.assertIn('Chrome/', engine.USER_AGENT)


class CompanyNameFromPageTests(TestCase):
    """
    The old engine stored the search-result title as the company name. These
    cover the replacement: read what the site says about itself.
    """

    def test_a_copyright_footer_yields_the_name(self):
        self.assertEqual(
            engine._name_from_candidate(
                'Home Products Contact\n© 2024 SD Steel Pvt Ltd. '
                'All rights reserved.\nDesigned by someone'),
            'SD Steel Pvt Ltd')

    def test_a_plain_organisation_name_is_taken_as_is(self):
        self.assertEqual(engine._name_from_candidate('SD Steel Pvt Ltd'),
                         'SD Steel Pvt Ltd')

    def test_a_title_with_a_tagline_is_trimmed(self):
        self.assertEqual(
            engine._name_from_candidate(
                'SD Steel Pvt Ltd | Leading Sponge Iron Maker in WB'),
            'SD Steel Pvt Ltd')

    def test_a_listicle_title_yields_no_name(self):
        self.assertEqual(
            engine._name_from_candidate(
                'Top 10 Sponge Iron Manufacturers in West Bengal'), '')

    def test_a_tagline_masquerading_as_a_name_is_rejected(self):
        self.assertEqual(
            engine._name_from_candidate(
                'We are the leading supplier of quality steel products '
                'across eastern India'), '')

    def test_the_site_name_beats_the_search_title(self):
        findings = engine.SiteFindings(
            site_name='SD Steel Pvt Ltd',
            result_title='Best Steel Suppliers in Durgapur - Justdial')
        self.assertEqual(findings.best_name, 'SD Steel Pvt Ltd')


class StoreFindingsTests(TestCase):
    def setUp(self):
        self.job = GenerationJob.objects.create(
            state='West Bengal', district='Paschim Bardhaman', city='Durgapur',
            industry='Iron & Steel', keywords='sponge iron', status='RUNNING')

    def findings(self, **kwargs):
        defaults = {
            'site_name': 'SD Steel Pvt Ltd',
            'url': 'https://sdsteel.example',
            'emails': ['info@sdsteel.example'],
            'phones': ['9832011001'],
            'pin_code': '713203',
            'query': 'sponge iron company in Durgapur, West Bengal contact address',
            'location': {'city': 'Durgapur', 'district': 'Paschim Bardhaman',
                         'state': 'West Bengal'},
        }
        defaults.update(kwargs)
        return engine.SiteFindings(**defaults)

    def test_a_good_site_becomes_a_lead(self):
        engine._store(self.job, self.findings(), check_mx=False)
        self.job.refresh_from_db()

        self.assertEqual(self.job.leads_found, 1)
        company = Company.objects.get()
        self.assertEqual(company.company_name, 'SD Steel Pvt Ltd')
        self.assertEqual(company.normalized_phone, '+919832011001')
        # The name came from the site, so identity is confirmed, not guessed.
        self.assertEqual(company.identity_status, 'SITE_CONFIRMED')
        self.assertGreater(company.data_quality_score, 0)

    def test_the_location_is_stored_from_the_job(self):
        engine._store(self.job, self.findings(), check_mx=False)
        plant = Company.objects.get().primary_plant
        self.assertEqual(plant.city, 'Durgapur')
        self.assertEqual(plant.district, 'Paschim Bardhaman')
        self.assertEqual(plant.pin_code, '713203')

    def test_extra_addresses_are_kept_as_unnamed_contacts(self):
        engine._store(self.job, self.findings(
            emails=['info@sdsteel.example', 'sales@sdsteel.example']),
            check_mx=False)
        company = Company.objects.get()
        self.assertEqual(company.company_email, 'info@sdsteel.example')
        self.assertTrue(company.contacts.filter(
            normalized_email='sales@sdsteel.example').exists())

    def test_a_second_visit_to_the_same_site_does_not_duplicate_it(self):
        engine._store(self.job, self.findings(), check_mx=False)
        engine._store(self.job, self.findings(
            url='https://www.sdsteel.example/contact', emails=[], phones=[]),
            check_mx=False)

        self.assertEqual(Company.objects.count(), 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.duplicate_leads, 1)

    def test_a_site_with_no_name_is_counted_as_rejected(self):
        engine._store(self.job, self.findings(site_name='', result_title=''),
                      check_mx=False)
        self.job.refresh_from_db()
        self.assertEqual(self.job.rejected_leads, 1)
        self.assertEqual(Company.objects.count(), 0)

    def test_a_listicle_falling_through_is_still_rejected(self):
        engine._store(self.job, self.findings(
            site_name='',
            result_title='Top 10 Sponge Iron Manufacturers in West Bengal'),
            check_mx=False)
        self.job.refresh_from_db()
        self.assertEqual(self.job.rejected_leads, 1)
        self.assertEqual(Company.objects.count(), 0)


class JobControlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x')
        self.client.force_login(self.user)
        industry = Industry.objects.create(name='Iron & Steel')
        SearchKeyword.objects.create(industry=industry, keyword='sponge iron')
        District.objects.create(state='West Bengal', name='Paschim Bardhaman')

    def start(self, **extra):
        data = {'state': 'West Bengal', 'industry': 'Iron & Steel',
                'keywords': ['sponge iron'], 'max_websites': '25'}
        data.update(extra)
        return self.client.post(reverse('lead_generation:start'), data)

    def test_a_run_is_queued_not_started_in_the_request(self):
        self.start()
        job = GenerationJob.objects.get()
        # PENDING or RUNNING: the worker thread may have claimed it already.
        self.assertIn(job.status, ('PENDING', 'RUNNING', 'FAILED', 'COMPLETED'))
        self.assertEqual(job.max_websites, 25)

    def test_a_state_wide_run_needs_no_district_or_city(self):
        self.start()
        job = GenerationJob.objects.get()
        self.assertEqual(job.district, '')
        self.assertEqual(job.city, '')
        self.assertIn('all districts', job.scope)

    def test_a_run_without_keywords_is_refused(self):
        self.start(keywords=[])
        self.assertEqual(GenerationJob.objects.count(), 0)

    def test_the_website_limit_is_clamped(self):
        self.start(max_websites='99999')
        self.assertLessEqual(GenerationJob.objects.get().max_websites, 500)

    def test_a_job_can_be_stopped(self):
        job = GenerationJob.objects.create(
            state='West Bengal', industry='Iron & Steel',
            keywords='sponge iron', status='RUNNING')
        self.client.post(reverse('lead_generation:control',
                                 args=[job.id, 'stop']))
        job.refresh_from_db()
        self.assertEqual(job.status, 'STOPPED')

    def test_pausing_a_job_that_is_not_running_is_refused(self):
        job = GenerationJob.objects.create(
            state='West Bengal', industry='Iron & Steel',
            keywords='x', status='COMPLETED')
        self.client.post(reverse('lead_generation:control',
                                 args=[job.id, 'pause']))
        job.refresh_from_db()
        self.assertEqual(job.status, 'COMPLETED')

    def test_two_runs_cannot_overlap(self):
        GenerationJob.objects.create(
            state='West Bengal', industry='Iron & Steel',
            keywords='x', status='RUNNING')
        self.start()
        self.assertEqual(GenerationJob.objects.count(), 1)

    def test_live_data_reports_progress(self):
        GenerationJob.objects.create(
            state='West Bengal', industry='Iron & Steel', keywords='x',
            status='RUNNING', websites_found=4, leads_found=2, max_websites=50)
        response = self.client.get(reverse('lead_generation:live_data'))
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['job']['websites_found'], 4)
        self.assertEqual(payload['job']['progress'], 8)


class JobStartGuardTests(TestCase):
    def test_a_stopped_job_is_never_started(self):
        """
        A user can press Stop between queueing a run and the worker claiming
        it. Starting it anyway would ignore them.
        """
        job = GenerationJob.objects.create(
            state='West Bengal', industry='Iron & Steel', keywords='x',
            status='STOPPED')
        engine.run_generation_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'STOPPED')
        self.assertEqual(job.websites_found, 0)

    def test_a_completed_job_is_never_re_run(self):
        job = GenerationJob.objects.create(
            state='West Bengal', industry='Iron & Steel', keywords='x',
            status='COMPLETED', leads_found=5)
        engine.run_generation_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'COMPLETED')
        self.assertEqual(job.leads_found, 5)

    def test_a_missing_job_does_not_raise(self):
        engine.run_generation_job(999999)


class WorkerRecoveryTests(TestCase):
    def test_a_job_whose_worker_died_is_released(self):
        """
        Previously a job left RUNNING by a restart stayed RUNNING for ever,
        with no way for the user to clear it.
        """
        from datetime import timedelta

        from django.utils import timezone

        from core import worker

        job = GenerationJob.objects.create(
            state='West Bengal', industry='Iron & Steel', keywords='x',
            status='RUNNING',
            heartbeat_at=timezone.now() - timedelta(hours=2))

        recovered = worker.recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(recovered, 1)
        self.assertEqual(job.status, 'FAILED')
        self.assertIn('worker stopped', job.error_message)

    def test_a_healthy_job_is_left_alone(self):
        from django.utils import timezone

        from core import worker

        job = GenerationJob.objects.create(
            state='West Bengal', industry='Iron & Steel', keywords='x',
            status='RUNNING', heartbeat_at=timezone.now())

        self.assertEqual(worker.recover_stale_jobs(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, 'RUNNING')

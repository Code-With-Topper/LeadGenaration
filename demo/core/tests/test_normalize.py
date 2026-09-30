from django.test import SimpleTestCase

from core import normalize


class NormalizeNameTests(SimpleTestCase):
    def test_legal_suffixes_and_case_collapse_to_one_key(self):
        """The forms a company name is written in must all match each other."""
        variants = [
            'SD Steel Pvt Ltd',
            'S. D. Steel Private Limited',
            'M/s SD STEEL PVT. LTD.',
            'sd steel limited',
            'The SD Steel Co',
        ]
        keys = {normalize.normalize_name(v) for v in variants}
        self.assertEqual(keys, {'sd steel'}, f'got {keys}')

    def test_ampersand_and_the_word_and_give_the_same_key(self):
        """
        Indian company names use these interchangeably. Without this, the same
        company written two ways would never be matched on name alone.
        """
        keys = {normalize.normalize_name(v) for v in [
            'Durgapur Iron & Steel Works Pvt Ltd',
            'Durgapur Iron and Steel Works Private Limited',
            'DURGAPUR IRON STEEL WORKS LTD',
            'M/s Durgapur Iron & Steel Works',
        ]}
        self.assertEqual(keys, {'durgapur iron steel works'}, f'got {keys}')

    def test_a_trailing_and_co_is_still_stripped(self):
        self.assertEqual(normalize.normalize_name('Howrah Casting & Co'),
                         'howrah casting')
        self.assertEqual(normalize.normalize_name('Howrah Casting and Company'),
                         'howrah casting')

    def test_industry_words_are_kept(self):
        # "Industries" distinguishes companies; it must not be stripped.
        self.assertEqual(normalize.normalize_name('Bengal Industries Ltd'),
                         'bengal industries')

    def test_a_name_that_is_only_a_suffix_has_no_key(self):
        self.assertEqual(normalize.normalize_name('Pvt Ltd'), '')

    def test_empty_and_pandas_nan_are_handled(self):
        for value in ('', None, 'nan', 'NOT FOUND', '   '):
            self.assertEqual(normalize.normalize_name(value), '')


class NormalizePhoneTests(SimpleTestCase):
    def test_indian_forms_collapse_to_one_e164_value(self):
        """This is what makes phone-based duplicate detection work at all."""
        variants = [
            '9876512345', '+91 98765 12345', '+919876512345',
            '09876512345', '0091-9876512345', '91 98765 12345',
            '(98765) 12345', '9876512345 ext 22',
        ]
        keys = {normalize.normalize_phone(v) for v in variants}
        self.assertEqual(keys, {'+919876512345'}, f'got {keys}')

    def test_landline_with_std_code(self):
        self.assertEqual(normalize.normalize_phone('033 2640 1234'), '+913326401234')

    def test_too_short_and_too_long_are_rejected(self):
        for value in ('12', '2023', '123456789', '9876543210123456789'):
            self.assertEqual(normalize.normalize_phone(value), '',
                             f'{value} should be rejected')

    def test_toll_free_is_kept(self):
        self.assertEqual(normalize.normalize_phone('1800 123 4567'),
                         '+9118001234567')


class NormalizeDomainTests(SimpleTestCase):
    def test_www_scheme_and_path_do_not_change_the_domain(self):
        keys = {normalize.normalize_domain(v) for v in [
            'https://www.sdsteel.in/contact',
            'http://sdsteel.in',
            'sdsteel.in',
            'HTTPS://WWW.SDSTEEL.IN:443/about/',
        ]}
        self.assertEqual(keys, {'sdsteel.in'}, f'got {keys}')

    def test_non_urls_give_nothing(self):
        for value in ('', 'not a url', 'ftp://', 'localhost'):
            self.assertEqual(normalize.normalize_domain(value), '')


class TitleNoiseTests(SimpleTestCase):
    def test_tagline_after_separator_is_dropped(self):
        self.assertEqual(
            normalize.strip_title_noise('SD Steel Pvt Ltd | Best in West Bengal'),
            'SD Steel Pvt Ltd')
        self.assertEqual(
            normalize.strip_title_noise('SD Steel - Sponge Iron Makers'),
            'SD Steel')

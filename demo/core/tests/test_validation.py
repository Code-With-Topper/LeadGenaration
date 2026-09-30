from django.test import SimpleTestCase

from core import validation


class EmailValidationTests(SimpleTestCase):
    def test_valid_address_passes(self):
        result = validation.validate_email('Info@SDSteel.in')
        self.assertTrue(result.ok)
        self.assertEqual(result.value, 'info@sdsteel.in')

    def test_malformed_addresses_are_rejected_with_a_reason(self):
        for value in ('not-an-email', 'a@b', 'a@@b.com', 'no-at-sign.com', '@b.com'):
            result = validation.validate_email(value)
            self.assertFalse(result.ok, f'{value} should be rejected')
            self.assertTrue(result.reason, 'a rejection must say why')

    def test_placeholder_and_disposable_addresses_are_rejected(self):
        for value in ('test@example.com', 'youremail@domain.com',
                      'someone@mailinator.com', 'aaaa@real.in'):
            self.assertFalse(validation.validate_email(value).ok, value)

    def test_role_addresses_that_cannot_reply_are_rejected(self):
        self.assertFalse(validation.validate_email('noreply@sdsteel.in').ok)

    def test_css_and_image_false_positives_are_rejected(self):
        # A greedy regex over page HTML picks these up.
        self.assertEqual(validation.validate_email('logo@2x.png').status, 'ASSET')

    def test_generic_mailboxes_are_usable_but_flagged(self):
        self.assertTrue(validation.validate_email('sales@sdsteel.in').ok)
        self.assertTrue(validation.is_generic_email('sales@sdsteel.in'))
        self.assertFalse(validation.is_generic_email('ramesh@sdsteel.in'))


class PhoneValidationTests(SimpleTestCase):
    def test_mobile_and_landline_are_recognised(self):
        self.assertEqual(validation.validate_phone('9876512345').status, 'MOBILE_OK')
        self.assertEqual(validation.validate_phone('033 2640 1234').status,
                         'LANDLINE_OK')

    def test_placeholder_numbers_are_rejected(self):
        for value in ('9999999999', '1234567890', '0123456789'):
            result = validation.validate_phone(value)
            self.assertFalse(result.ok, value)
            self.assertEqual(result.status, 'JUNK')

    def test_a_year_is_not_a_phone_number(self):
        self.assertFalse(validation.validate_phone('2023').ok)


class WebsiteValidationTests(SimpleTestCase):
    def test_a_real_url_passes(self):
        self.assertTrue(validation.validate_website('https://sdsteel.in').ok)

    def test_bad_schemes_are_rejected(self):
        self.assertEqual(validation.validate_website('ftp://oops').status,
                         'FORMAT_BAD')

    def test_directories_are_not_companies(self):
        for url in ('https://www.indiamart.com/sdsteel',
                    'https://justdial.com/x', 'https://linkedin.com/company/x',
                    'https://en.wikipedia.org/wiki/Steel'):
            result = validation.validate_website(url)
            self.assertFalse(result.ok, url)
            self.assertEqual(result.status, 'DIRECTORY')


class CompanyNameValidationTests(SimpleTestCase):
    def test_a_real_name_passes(self):
        self.assertTrue(validation.validate_company_name('SD Steel Pvt Ltd').ok)

    def test_listicle_titles_are_rejected(self):
        """
        The single biggest data-quality defect in the old system: a search
        result headline stored as a company.
        """
        for title in (
            'Top 10 Sponge Iron Manufacturers in West Bengal',
            'Best Steel Suppliers in Kolkata',
            'List of Foundries in Howrah',
            '15 Best Rolling Mills Near Me',
            'Steel Manufacturers in Durgapur - Directory',
        ):
            result = validation.validate_company_name(title)
            self.assertFalse(result.ok, title)
            self.assertEqual(result.status, 'LISTICLE')

    def test_empty_and_junk_names_are_rejected(self):
        for value in ('', '  ', '--', '123', 'A'):
            self.assertFalse(validation.validate_company_name(value).ok, repr(value))


class StatutoryIdentifierTests(SimpleTestCase):
    def test_valid_cin_passes(self):
        self.assertTrue(validation.validate_cin('U27100WB2005PTC123456').ok)

    def test_cin_structure_is_enforced(self):
        for value in ('X27100WB2005PTC123456',      # bad first letter
                      'U27100ZZ2005PTC123456',      # not a state
                      'U27100WB1700PTC123456',      # implausible year
                      'U27100WB2005PTC12345'):      # too short
            self.assertFalse(validation.validate_cin(value).ok, value)

    def test_gstin_checksum_is_actually_verified(self):
        # Build a GSTIN whose check digit is correct, then break only that digit.
        body = '19AAACS1234A1Z'
        good = body + validation.gstin_checksum(body)
        self.assertTrue(validation.validate_gstin(good).ok, good)

        wrong_digit = 'A' if good[-1] != 'A' else 'B'
        bad = body + wrong_digit
        result = validation.validate_gstin(bad)
        self.assertFalse(result.ok)
        self.assertIn('checksum', result.reason)

    def test_gst_state_code_is_checked(self):
        # 50 is not an allotted GST state code (01-38, 97 and 99 are).
        body = '50AAACS1234A1Z'
        result = validation.validate_gstin(body + validation.gstin_checksum(body))
        self.assertFalse(result.ok)
        self.assertIn('state code', result.reason)

    def test_real_jurisdiction_codes_97_and_99_are_accepted(self):
        for code in ('97', '99'):
            body = f'{code}AAACS1234A1Z'
            self.assertTrue(
                validation.validate_gstin(body + validation.gstin_checksum(body)).ok,
                code)


class PinTests(SimpleTestCase):
    def test_six_digits_pass(self):
        self.assertEqual(validation.validate_pin('713 203').value, '713203')

    def test_wrong_length_or_leading_zero_fails(self):
        for value in ('71320', '7132033', '013203'):
            self.assertFalse(validation.validate_pin(value).ok, value)

    def test_west_bengal_pins_are_recognised(self):
        self.assertEqual(validation.state_from_pin('713203'), 'West Bengal')

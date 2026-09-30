"""
Import tests.

The reproduced defect in the old system: a CSV holding one company four times,
with the same email and the same phone, created three companies and validated
nothing. These tests pin that behaviour shut.
"""
import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from imports.models import ImportJob
from leads.models import Company, DuplicateReview, Lead, Plant

MESSY_CSV = """Company,Industry,Email,Phone,Web,Person,City,District,State,Pin
SD Steel Pvt Ltd,Iron & Steel,info@sdsteel.example,9832011001,https://sdsteel.example,Ramesh,Durgapur,Paschim Bardhaman,West Bengal,713203
SD Steel Pvt. Ltd.,Iron & Steel,info@sdsteel.example,9832011001,https://sdsteel.example,Ramesh,Durgapur,Paschim Bardhaman,West Bengal,713203
SD STEEL PVT LTD,Iron & Steel,INFO@SDSTEEL.EXAMPLE,+91 98320 11001,https://www.sdsteel.example,Ramesh,Durgapur,Paschim Bardhaman,West Bengal,713203
SD Steel Pvt Ltd,Iron & Steel,info@sdsteel.example,9832011001,https://sdsteel.example,Ramesh,Durgapur,Paschim Bardhaman,West Bengal,713203
Haldia Ferro Alloys Pvt Ltd,Ferro Alloys,enquiry@haldiaferro.example,9163022002,https://haldiaferro.example,Sujit,Haldia,Purba Medinipur,West Bengal,721602
Bad Data Co,Foundry,not-an-email,12,ftp://oops,X,Asansol,Paschim Bardhaman,West Bengal,99
,Foundry,orphan@nowhere.example,9000000001,https://orphan.example,Y,Haldia,Purba Medinipur,West Bengal,721602
"""

MAPPING = {
    'map_company_name': 'Company', 'map_industry': 'Industry',
    'map_company_email': 'Email', 'map_company_phone': 'Phone',
    'map_website': 'Web', 'map_contact_name': 'Person',
    'map_city': 'City', 'map_district': 'District', 'map_state': 'State',
    'map_pin_code': 'Pin',
}


@override_settings(VALIDATE_EMAIL_MX=False)
class ImportFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tester', password='x',
                                             is_staff=True, is_superuser=True)
        self.client.force_login(self.user)

    def upload(self, text=MESSY_CSV, name='leads.csv'):
        response = self.client.post(
            reverse('imports:upload'),
            {'file': SimpleUploadedFile(name, text.encode(), 'text/csv')})
        self.assertEqual(response.status_code, 302)
        return ImportJob.objects.latest('id')

    def run_to_preview(self, job):
        self.client.post(reverse('imports:map_columns', args=[job.id]), MAPPING)
        self.client.get(reverse('imports:preview', args=[job.id]))
        job.refresh_from_db()
        return job

    # -- reading the file -------------------------------------------------

    def test_columns_are_matched_automatically(self):
        job = self.upload("""Company Name,Email ID,Mobile No,City
ABC Steel Ltd,a@b.example,9832011001,Durgapur
""")
        self.assertEqual(job.column_mapping.get('company_name'), 'Company Name')
        self.assertEqual(job.column_mapping.get('company_email'), 'Email ID')
        self.assertEqual(job.column_mapping.get('contact_mobile'), 'Mobile No')
        self.assertEqual(job.column_mapping.get('city'), 'City')

    def test_a_non_spreadsheet_is_refused(self):
        response = self.client.post(
            reverse('imports:upload'),
            {'file': SimpleUploadedFile('notes.txt', b'hello', 'text/plain')})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ImportJob.objects.count(), 0)

    # -- the dry run -------------------------------------------------------

    def test_the_preview_changes_nothing(self):
        """This is the whole point of the dry run."""
        job = self.run_to_preview(self.upload())
        self.assertEqual(job.status, 'PREVIEWED')
        self.assertEqual(Company.objects.count(), 0)
        self.assertEqual(Lead.objects.count(), 0)
        self.assertEqual(DuplicateReview.objects.count(), 0)

    def test_the_preview_reports_accurate_counts(self):
        job = self.run_to_preview(self.upload())
        self.assertEqual(job.total_rows, 7)
        # SD Steel, Haldia, and Bad Data Co — whose name is real even though
        # every one of its contact fields was invalid.
        self.assertEqual(job.created_rows, 3)
        # The three extra SD Steel spellings.
        self.assertEqual(job.merged_rows, 3)
        # Only the row with no company name at all.
        self.assertEqual(job.rejected_rows, 1)
        # Bad Data Co is imported, but the user is told what was dropped.
        self.assertGreaterEqual(job.warned_rows, 1)

    def test_rejected_rows_come_with_a_reason(self):
        job = self.run_to_preview(self.upload())
        reasons = ' '.join(entry['reason'] for entry in job.rejections)
        self.assertIn('company name', reasons.lower())

    def test_rejected_rows_can_be_downloaded(self):
        job = self.run_to_preview(self.upload())
        response = self.client.get(
            reverse('imports:download_rejected', args=[job.id]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('Outcome', body)
        # Both the rejected row and the salvaged-but-flagged one appear.
        self.assertIn('no company name', body)
        self.assertIn('Bad Data Co', body)
        self.assertIn('imported with problems', body)

    # -- the commit --------------------------------------------------------

    def test_one_company_written_four_ways_becomes_one_company(self):
        """
        The headline defect. Previously this produced three companies.
        """
        job = self.run_to_preview(self.upload())
        self.client.post(reverse('imports:commit', args=[job.id]))

        self.assertEqual(
            Company.objects.filter(normalized_name='sd steel').count(), 1,
            list(Company.objects.values_list('company_name', flat=True)))
        # SD Steel, Haldia Ferro, Bad Data Co.
        self.assertEqual(Company.objects.count(), 3)
        # And only one lead for the company, not four.
        self.assertEqual(
            Lead.objects.filter(company__normalized_name='sd steel').count(), 1)

    def test_invalid_contact_details_are_never_stored(self):
        job = self.run_to_preview(self.upload())
        self.client.post(reverse('imports:commit', args=[job.id]))

        self.assertFalse(Company.objects.filter(company_email='not-an-email').exists())
        self.assertFalse(Company.objects.filter(company_phone='12').exists())
        self.assertFalse(Company.objects.filter(website='ftp://oops').exists())

    def test_city_and_state_are_actually_saved(self):
        """The old importer offered these in the UI and then discarded them."""
        job = self.run_to_preview(self.upload())
        self.client.post(reverse('imports:commit', args=[job.id]))

        company = Company.objects.get(normalized_name='sd steel')
        plant = company.primary_plant
        self.assertIsNotNone(plant, 'no Plant row was created')
        self.assertEqual(plant.city, 'Durgapur')
        self.assertEqual(plant.district, 'Paschim Bardhaman')
        self.assertEqual(plant.state, 'West Bengal')
        self.assertEqual(plant.pin_code, '713203')

    def test_matching_keys_are_populated_on_import(self):
        """
        Without these, a scraped lead could never be matched against an
        imported one — the two paths would each keep their own copy.
        """
        job = self.run_to_preview(self.upload())
        self.client.post(reverse('imports:commit', args=[job.id]))

        company = Company.objects.get(normalized_name='sd steel')
        self.assertEqual(company.normalized_domain, 'sdsteel.example')
        self.assertEqual(company.normalized_email, 'info@sdsteel.example')
        self.assertEqual(company.normalized_phone, '+919832011001')

    def test_phone_is_stored_in_one_canonical_form(self):
        job = self.run_to_preview(self.upload())
        self.client.post(reverse('imports:commit', args=[job.id]))
        company = Company.objects.get(normalized_name='sd steel')
        self.assertEqual(company.normalized_phone, '+919832011001')

    def test_re_importing_the_same_file_adds_nothing(self):
        first = self.run_to_preview(self.upload())
        self.client.post(reverse('imports:commit', args=[first.id]))
        before = Company.objects.count()

        second = self.run_to_preview(self.upload())
        self.client.post(reverse('imports:commit', args=[second.id]))

        self.assertEqual(Company.objects.count(), before)
        second.refresh_from_db()
        self.assertEqual(second.created_rows, 0)

    def test_a_contact_person_is_recorded(self):
        job = self.run_to_preview(self.upload())
        self.client.post(reverse('imports:commit', args=[job.id]))
        company = Company.objects.get(normalized_name='sd steel')
        self.assertTrue(company.contacts.filter(name='Ramesh').exists())

    def test_a_quality_score_is_calculated(self):
        job = self.run_to_preview(self.upload())
        self.client.post(reverse('imports:commit', args=[job.id]))
        company = Company.objects.get(normalized_name='sd steel')
        self.assertGreater(company.data_quality_score, 0)

    def test_commit_is_refused_before_a_preview(self):
        job = self.upload()
        self.client.post(reverse('imports:map_columns', args=[job.id]), MAPPING)
        response = self.client.post(reverse('imports:commit', args=[job.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Company.objects.count(), 0)

    def test_a_mapping_without_company_name_is_refused(self):
        job = self.upload()
        good_mapping = dict(job.column_mapping)

        self.client.post(reverse('imports:map_columns', args=[job.id]),
                         {'map_industry': 'Industry'})

        job.refresh_from_db()
        # The bad mapping is not stored, so the earlier one survives.
        self.assertEqual(job.column_mapping, good_mapping)
        self.assertTrue(job.column_mapping.get('company_name'))

    def test_an_excel_file_is_read_too(self):
        import pandas as pd
        buffer = io.BytesIO()
        pd.DataFrame([{'Company Name': 'Excel Steel Ltd',
                       'Email': 'x@excelsteel.example',
                       'City': 'Kolkata'}]).to_excel(buffer, index=False)
        buffer.seek(0)
        response = self.client.post(
            reverse('imports:upload'),
            {'file': SimpleUploadedFile('leads.xlsx', buffer.read(),
                                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        self.assertEqual(response.status_code, 302)
        job = ImportJob.objects.latest('id')
        self.assertIn('Company Name', job.detected_columns)

    def test_the_template_file_can_be_downloaded_and_re_imported(self):
        response = self.client.get(reverse('imports:sample_file'))
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn('Company Name', text)

        job = self.upload(text, name='template.csv')
        self.assertEqual(job.column_mapping.get('company_name'), 'Company Name')

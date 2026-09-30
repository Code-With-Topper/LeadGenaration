import csv

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import ImportJob
from .services import MAPPABLE_FIELDS, read_dataframe, run_import, suggest_mapping

MAX_UPLOAD_MB = 20


@login_required
def index(request):
    jobs = ImportJob.objects.all()
    page = Paginator(jobs, settings.PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'imports/index.html', {
        'page_obj': page,
        'jobs': page.object_list,
    })


@login_required
@require_POST
def upload(request):
    upload_file = request.FILES.get('file')
    if not upload_file:
        messages.error(request, 'Please choose a file first.')
        return redirect('imports:index')

    if not upload_file.name.lower().endswith(('.csv', '.xlsx', '.xls', '.xlsm')):
        messages.error(request, 'Only CSV and Excel files can be imported.')
        return redirect('imports:index')

    if upload_file.size > MAX_UPLOAD_MB * 1024 * 1024:
        messages.error(request, f'File is too large. The limit is {MAX_UPLOAD_MB} MB.')
        return redirect('imports:index')

    job = ImportJob.objects.create(
        file_name=upload_file.name, file_path=upload_file,
        created_by=request.user if request.user.is_authenticated else None)

    try:
        frame = read_dataframe(job.file_path.path, job.file_name, nrows=5)
    except Exception as exc:
        job.status = 'FAILED'
        job.error_log = str(exc)
        job.save()
        messages.error(request, f'Could not read that file: {exc}')
        return redirect('imports:index')

    if frame.empty or not len(frame.columns):
        job.status = 'FAILED'
        job.error_log = 'The file has no readable columns.'
        job.save()
        messages.error(request, 'That file has no readable columns.')
        return redirect('imports:index')

    job.detected_columns = [str(c) for c in frame.columns]
    job.column_mapping = suggest_mapping(job.detected_columns)
    job.status = 'MAPPED' if job.column_mapping.get('company_name') else 'UPLOADED'
    job.save()
    return redirect('imports:map_columns', job_id=job.id)


@login_required
def map_columns(request, job_id):
    """
    Match spreadsheet columns to lead fields.

    The mapping is stored on the job, not in the session, so the user can
    close the page, switch from laptop to phone, and carry on.
    """
    job = get_object_or_404(ImportJob, id=job_id)

    if request.method == 'POST':
        mapping = {}
        for field_name, _label, _required in MAPPABLE_FIELDS:
            column = (request.POST.get(f'map_{field_name}') or '').strip()
            if column and column in job.detected_columns:
                mapping[field_name] = column

        if not mapping.get('company_name'):
            messages.error(request, 'Company Name must be mapped — it is the '
                                    'one field every lead needs.')
            return redirect('imports:map_columns', job_id=job.id)

        job.column_mapping = mapping
        job.status = 'MAPPED'
        job.save(update_fields=['column_mapping', 'status'])
        return redirect('imports:preview', job_id=job.id)

    # Show the first few rows so the user can see what they are mapping.
    sample_rows = []
    try:
        frame = read_dataframe(job.file_path.path, job.file_name, nrows=3)
        sample_rows = frame.to_dict('records')
    except Exception:
        pass

    return render(request, 'imports/map_columns.html', {
        'job': job,
        'columns': job.detected_columns,
        'fields': MAPPABLE_FIELDS,
        'mapping': job.column_mapping or {},
        'sample_rows': sample_rows,
    })


@login_required
def preview(request, job_id):
    """
    The dry run: show exactly what the import will do, and change nothing.

    This is where duplicates and invalid rows are surfaced *before* they can
    reach the database.
    """
    job = get_object_or_404(ImportJob, id=job_id)

    if job.status not in ('MAPPED', 'PREVIEWED'):
        messages.error(request, 'Map the columns before previewing.')
        return redirect('imports:map_columns', job_id=job.id)

    if request.method == 'POST' or not job.preview:
        try:
            run_import(job, dry_run=True)
        except Exception as exc:
            job.status = 'FAILED'
            job.error_log = str(exc)
            job.save()
            messages.error(request, f'Preview failed: {exc}')
            return redirect('imports:index')

    return render(request, 'imports/preview.html', {
        'job': job,
        'result': job.preview or {},
    })


@login_required
@require_POST
def commit(request, job_id):
    """Write the rows for real, after the user has approved the preview."""
    job = get_object_or_404(ImportJob, id=job_id)

    if job.status != 'PREVIEWED':
        messages.error(request, 'Run the preview before importing.')
        return redirect('imports:preview', job_id=job.id)

    # Large files are handed to the background worker so the browser is never
    # left waiting on a request that cannot finish in time.
    if job.total_rows > 500:
        job.status = 'QUEUED'
        job.save(update_fields=['status'])
        messages.info(
            request,
            f'{job.total_rows} rows queued. The import runs in the background — '
            'this page will show the result when it finishes.')
        _nudge_worker()
        return redirect('imports:index')

    try:
        result = run_import(job, dry_run=False)
    except Exception as exc:
        job.status = 'FAILED'
        job.error_log = str(exc)
        job.save()
        messages.error(request, f'Import failed: {exc}')
        return redirect('imports:index')

    from reports.utils import log_audit
    log_audit('Leads Imported', 'ImportJob', job.id,
              f"{result['created']} new, {result['merged']} merged, "
              f"{result['review']} to review, {result['rejected']} rejected",
              request.user)

    messages.success(
        request,
        f"Import done — {result['created']} new companies, "
        f"{result['merged']} merged into existing ones, "
        f"{result['review']} need your review, "
        f"{result['rejected']} rejected"
        + (f", {result['warned']} imported with some fields dropped."
           if result.get('warned') else "."))
    return redirect('imports:index')


@login_required
@require_POST
def cancel(request, job_id):
    job = get_object_or_404(ImportJob, id=job_id)
    if job.is_finished:
        messages.warning(request, 'That import has already finished.')
    else:
        job.status = 'CANCELLED'
        job.save(update_fields=['status'])
        messages.success(request, 'Import cancelled. Nothing was changed.')
    return redirect('imports:index')


@login_required
def download_rejected(request, job_id):
    """
    The rejected rows, with the reason for each.

    A row that vanishes without explanation is the fastest way to lose a
    user's trust in an import.
    """
    job = get_object_or_404(ImportJob, id=job_id)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = (
        f'attachment; filename="rejected-rows-import-{job.id}.csv"')
    writer = csv.writer(response)
    writer.writerow(['Row number', 'Company name', 'Outcome', 'Detail'])
    for entry in job.rejections or []:
        writer.writerow([entry.get('row'), entry.get('company'),
                         entry.get('kind', 'rejected'), entry.get('reason')])
    return response


@login_required
def sample_file(request):
    """A template CSV with the exact headings the importer understands."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="lead-import-template.csv"'
    writer = csv.writer(response)
    writer.writerow([label for _f, label, _r in MAPPABLE_FIELDS])
    writer.writerow([
        'SD Steel Pvt Ltd', 'Iron & Steel', 'Private Limited', 'Durgapur Unit',
        'Plot 12, Industrial Area', 'Durgapur', 'Paschim Bardhaman',
        'West Bengal', '713205', 'https://example.in', '03432567890',
        'info@example.in', 'Ramesh Kumar', 'Purchase Manager', 'Purchase',
        '9876500011', '03432567891', 'ramesh@example.in',
        'U27100WB2005PTC123456', '19AAACS1234A1ZQ',
        'https://linkedin.com/company/example', '', 'Met at trade fair',
    ])
    return response


def _nudge_worker():
    """
    Ask the in-process worker to pick the job up now.

    The cron worker would find it within a minute anyway; this just makes the
    common case feel immediate.
    """
    try:
        from core.worker import wake
        wake()
    except Exception:
        pass

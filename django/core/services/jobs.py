from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from core.models import BackgroundJob, Ingestion
from core.common import BusinessError, stamp
from . import extraction_ai, ingestion


def require_idle(item):
    if BackgroundJob.objects.filter(
        ingestion_uid=item.ingestion_uid, status__in=["queued", "running"]
    ).exists():
        raise BusinessError("Une opération est déjà en cours pour ce document.", 409)


@transaction.atomic
def enqueue(item, operation, provider=""):
    item = Ingestion.objects.select_for_update().get(pk=item.pk)
    if item.archived_at:
        raise BusinessError("Restaurez cet import pour reprendre le traitement.", 409)
    if item.document_uid:
        raise BusinessError("Import déjà converti.", 409)
    if operation not in {"transcribe", "analyze", "pipeline"}:
        raise BusinessError("Opération inconnue.")
    require_idle(item)
    if operation == "analyze":
        if not (item.transcription_text or "").strip():
            raise BusinessError("Transcrivez d'abord le document.")
        provider = provider or "openrouter"
        extraction_ai.require_provider(provider)
    if operation == "pipeline" and provider != "local":
        extraction_ai.require_provider(provider)
    job = BackgroundJob.objects.create(
        company_uid=item.company_uid,
        ingestion_uid=item.ingestion_uid,
        operation=operation,
        provider=provider,
    )
    field = (
        "transcription_status"
        if operation in {"transcribe", "pipeline"}
        else "extraction_status"
    )
    setattr(item, field, "queued")
    item.last_error = None
    item.save(update_fields=[field, "last_error"])
    return job


def work_one():
    # Interrupted jobs are reported as failed, never silently retried externally.
    BackgroundJob.objects.filter(
        status="running", started_at__lt=timezone.now() - timedelta(hours=2)
    ).update(
        status="failed",
        error="Traitement interrompu. Relancez après vérification.",
        finished_at=timezone.now(),
    )
    with transaction.atomic():
        job = (
            BackgroundJob.objects.select_for_update(skip_locked=True)
            .filter(status="queued")
            .order_by("id")
            .first()
        )
        if not job:
            return False
        job.status = "running"
        job.started_at = timezone.now()
        job.save()
    item = Ingestion.objects.get(
        company_uid=job.company_uid, ingestion_uid=job.ingestion_uid
    )
    try:
        if item.document_uid:
            raise BusinessError("Import déjà converti.")
        if job.operation in {"transcribe", "pipeline"}:
            ingestion.transcribe(item)
            if job.operation == "pipeline":
                from .invoice_imports import local_invoice, index_extraction
                from core.common import json_text

                if job.provider == "local":
                    data = local_invoice(item)
                    item.extraction_json = json_text(data)
                    index_extraction(item, data)
                    item.extraction_model = "local"
                    item.extraction_status, item.status = "done", "analyzed"
                    item.save(
                        update_fields=[
                            "extraction_json",
                            "source_invoice_ref",
                            "source_invoice_match",
                            "extraction_model",
                            "extraction_status",
                            "status",
                        ]
                    )
                else:
                    ingestion.analyze(item, job.provider)
        else:
            ingestion.analyze(item, job.provider or "openrouter")
        job.status = "done"
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:1000]
        item.last_error = job.error
        item.status = "failed"
        field = (
            "transcription_status"
            if job.operation == "transcribe"
            or (job.operation == "pipeline" and item.transcription_status != "done")
            else "extraction_status"
        )
        setattr(item, field, "failed")
        item.save(update_fields=[field, "status", "last_error"])
    job.finished_at = timezone.now()
    job.save()
    return True

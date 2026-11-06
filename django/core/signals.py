from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Customer, Document, StockMovement, Opportunity, CrmActivity, Email
from .services.webhooks import emit


@receiver(pre_save, sender=Document)
@receiver(pre_save, sender=Email)
def prior_status(sender, instance, **kwargs):
    instance._prior_status = (
        sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        if instance.pk
        else None
    )


@receiver(post_save, sender=Customer)
def customer_saved(sender, instance, created, raw=False, **kwargs):
    if not raw:
        emit(
            instance.company_uid,
            "customer.created" if created else "customer.updated",
            {
                "customer_uid": instance.customer_uid,
                "name": instance.legal_name,
                "type": instance.type,
                "status": instance.status,
            },
        )


@receiver(post_save, sender=Document)
def document_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    data = {
        key: getattr(instance, key)
        for key in (
            "document_uid",
            "customer_uid",
            "number",
            "type",
            "direction",
            "status",
            "currency_code",
            "total_excluding_tax",
            "total_including_tax",
            "amount_due",
        )
    }
    emit(
        instance.company_uid,
        "document.created" if created else "document.updated",
        data,
    )
    if not created and instance.status != getattr(
        instance, "_prior_status", instance.status
    ):
        emit(
            instance.company_uid,
            "document.status_changed",
            data | {"previous_status": instance._prior_status},
        )


@receiver(post_save, sender=StockMovement)
def stock_saved(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        emit(
            instance.company_uid,
            "stock.movement",
            {
                key: getattr(instance, key)
                for key in (
                    "movement_uid",
                    "item_uid",
                    "movement_type",
                    "quantity",
                    "occurred_on",
                    "reference",
                )
            },
        )


@receiver(post_save, sender=Opportunity)
def opportunity_saved(sender, instance, created, raw=False, **kwargs):
    if not raw:
        emit(
            instance.company_uid,
            "crm.opportunity.created" if created else "crm.opportunity.updated",
            {
                key: getattr(instance, key)
                for key in (
                    "opportunity_uid",
                    "customer_uid",
                    "title",
                    "stage",
                    "amount",
                    "currency_code",
                )
            },
        )


@receiver(post_save, sender=CrmActivity)
def activity_saved(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        emit(
            instance.company_uid,
            "crm.activity.created",
            {
                key: getattr(instance, key)
                for key in ("activity_uid", "customer_uid", "kind", "title", "due_on")
            },
        )


@receiver(post_save, sender=Email)
def email_saved(sender, instance, created, raw=False, **kwargs):
    if (
        not raw
        and instance.status == "sent"
        and getattr(instance, "_prior_status", None) != "sent"
    ):
        emit(
            instance.company_uid,
            "email.sent",
            {
                "email_uid": instance.email_uid,
                "document_uid": instance.document_uid,
                "recipient": instance.recipient_email,
                "subject": instance.subject,
            },
        )

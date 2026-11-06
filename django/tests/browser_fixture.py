"""Synthetic data for the isolated browser validation instance, never production."""

from django.conf import settings
from core.models import User, Company, CompanyMember, Customer
from core.services.documents import save_customer, save_document

if settings.ENVIRONMENT not in {"local", "testing"}:
    raise RuntimeError("Synthetic fixture is disabled in production")
if User.objects.exclude(email="demo@example.test").exists():
    raise RuntimeError("Refusing to seed an instance with existing users")
user, created = User.objects.get_or_create(
    email="demo@example.test", defaults={"name": "Camille Démonstration"}
)
if created:
    user.set_password("DemoForBrowser123!")
    user.save()
    company = Company.objects.create(
        owner_user_uid=user.user_uid,
        legal_name="Atelier Démonstration",
        siren="123456789",
        vat_number="FR32123456789",
        address_line1="1 rue de la Démonstration",
        postal_code="75001",
        city="Paris",
        country_code="FR",
        billing_email=user.email,
        payment_terms="Paiement à 30 jours. Pénalités de retard et indemnité forfaitaire applicables selon les conditions contractuelles.",
        invoice_footer="Document fictif de validation — sans valeur commerciale.",
    )
    CompanyMember.objects.create(
        company_uid=company.company_uid, user_uid=user.user_uid, role="owner"
    )
    customer = save_customer(
        company,
        dict(
            type="business",
            legal_name="Maison des Exemples",
            country_code="FR",
            email="client@example.test",
            address_line1="2 avenue des Exemples",
            postal_code="69001",
            city="Lyon",
        ),
    )
    for kind, subject, price in [
        ("quote", "Aménagement d'un espace de travail", "1500"),
        ("invoice", "Étude et préparation", "850"),
        ("quote", "Accompagnement mensuel", "450"),
    ]:
        save_document(
            company,
            user,
            kind,
            dict(
                customer_uid=customer.customer_uid,
                subject=subject,
                issue_date="2026-10-25",
                due_date="2026-11-24",
                lines=[
                    dict(
                        description=subject,
                        quantity="1",
                        unit_price_excluding_tax=price,
                        vat_rate="20",
                    )
                ],
            ),
        )
print("Synthetic browser fixture ready.")

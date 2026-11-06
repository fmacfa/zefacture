from pathlib import Path
from django.conf import settings
from django.db import connection
from django.http import FileResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe


@require_safe
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return HttpResponse("ok")
    except Exception:
        return HttpResponse("unavailable", status=503)


@require_safe
def public(request, page="home"):
    content = {
        "home": (
            "Votre facturation, chez vous.",
            "Devis, factures Factur-X, clients, achats et trésorerie dans votre propre instance.",
        ),
        "tarifs": (
            "Une édition libre.",
            "ZeFacture est distribué sous licence AGPL-3.0. L'hébergement et les services externes restent à la charge de l'exploitant.",
        ),
        "fonctionnalites": (
            "Du devis à l'encaissement.",
            "Gérez vos clients, documents, fournisseurs, stocks, dépenses et transactions bancaires.",
        ),
        "factur-x": (
            "Des factures lisibles et structurées.",
            "Génération locale d'un PDF/A-3 et de son XML Factur-X EN16931, avec validation avant émission.",
        ),
        "superpdp": (
            "Connecter votre plateforme.",
            "Cette instance peut transmettre les factures via SUPERPDP après configuration. La génération locale fonctionne indépendamment de ce service.",
        ),
        "contact": ("Contacter l'exploitant.", settings.INSTANCE_CONTACT),
        "mentions-legales": ("Mentions de l'instance.", settings.INSTANCE_LEGAL),
        "confidentialite": (
            "Vos données sur votre serveur.",
            "Les données métier et les fichiers sont conservés sur le serveur de l’exploitant de cette instance. L’OCR et la génération des PDF sont locaux.\n\n"
            "Lorsque vous connectez Gmail ou Outlook, cette édition demande l’identité du compte, l’envoi des messages et une autorisation durable. Elle ne lit pas votre boîte de réception. Les identifiants du compte et les jetons nécessaires sont conservés ; les jetons sont chiffrés avec une clé privée de l’instance. Les API Google servent uniquement à identifier le compte autorisé et à envoyer les messages demandés. Cette édition n’utilise pas ces données pour la publicité ni l’entraînement de modèles d’IA.\n\n"
            "Un envoi transmet l’expéditeur, le destinataire, l’objet, le corps et les pièces jointes au fournisseur de messagerie choisi, puis au destinataire. Les messages et leur historique restent conservés par l’instance. La déconnexion efface les jetons locaux ; vous pouvez également retirer le consentement dans votre compte fournisseur. La déconnexion ne supprime pas les documents ou l’historique métier déjà conservés.\n\n"
            "L’analyse IA facultative transmet le texte de l’import au service configuré lorsque vous la déclenchez. Les annuaires, transmissions PDP et webhooks utilisent également les services et destinataires configurés. Leurs réglages et l’accès aux données sont limités par les rôles de l’entreprise.\n\n"
            "Pour connaître les durées de conservation, les sauvegardes et les modalités d’accès, d’export ou de suppression applicables à vos données, contacter l’exploitant : "
            + settings.INSTANCE_CONTACT
            + ".\n\n"
            + settings.INSTANCE_PRIVACY_NOTES,
        ),
        "cgu": (
            "Conditions d'utilisation.",
            "L'accès est réservé aux utilisateurs autorisés par l'exploitant. Vérifiez les coordonnées, les montants et les mentions des documents avant émission. Le logiciel est fourni selon les conditions de la licence AGPL-3.0.",
        ),
        "cgv": (
            "Conditions de cette instance.",
            "Les conditions commerciales de vos prestations doivent être renseignées dans vos devis et factures. Les conditions propres à l'hébergement sont définies par l'exploitant.",
        ),
        "source": ("Code source et licence.", ""),
    }
    title, text = content.get(page, content["home"])
    return render(
        request,
        "core/public.html",
        {"title": title, "text": text, "source": page == "source"},
    )


@require_safe
def source_archive(request):
    archive = settings.BASE_DIR / "source.tar.gz"
    if not archive.is_file():
        return HttpResponse(
            "Archive indisponible. Lancez python manage.py build_source.", status=503
        )
    return FileResponse(
        archive.open("rb"), as_attachment=True, filename="zefacture-source.tar.gz"
    )

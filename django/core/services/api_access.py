from core.common import BusinessError, digest
from core.models import ApiKey, Company


def api_company(request, scope):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise BusinessError("Clé API requise.", 401)
    value = authorization[7:]
    if not value or len(value) > 256:
        raise BusinessError("Clé API invalide.", 401)
    key = ApiKey.objects.filter(key_hash=digest(value), status="active").first()
    if key is None:
        raise BusinessError("Clé API invalide.", 401)
    company = Company.objects.filter(
        company_uid=key.company_uid, status="active"
    ).first()
    if company is None:
        raise BusinessError("Clé API invalide.", 401)
    if key.scope != scope:
        raise BusinessError("Cette clé ne dispose pas de l’accès requis.", 403)
    return company

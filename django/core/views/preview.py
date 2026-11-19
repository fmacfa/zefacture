from django.http import HttpResponse
from django.shortcuts import render

from core.common import BusinessError
from core.services.document_preview import read_page


def preview_response(
    request, file, *, title, back_url, download_url, document=None, expense=None
):
    image_requested = request.GET.get("image") == "1"
    context = {
        "title": title,
        "back_url": back_url,
        "download_url": download_url,
        "reading_copy": getattr(file, "kind", "") == "pdp_readable",
        "invoice": document,
        "expense": expense,
    }
    status = 200
    try:
        number = request.GET.get("page", "1")
        if not number.isascii() or not number.isdecimal() or len(number) > 9:
            raise BusinessError("Numéro de page invalide.", 404)
        page = int(number)
        count, picture = read_page(file, page, render_image=image_requested)
        if image_requested:
            return HttpResponse(
                picture,
                content_type="image/png",
                headers={"Cache-Control": "private, no-store"},
            )
        context.update(page=page, page_count=count)
    except BusinessError as exc:
        if image_requested:
            raise
        context["preview_error"], status = str(exc), exc.status
    return render(request, "core/preview.html", context, status=status)

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from core.security import scoped
from core.services import reports as service


@scoped("finance")
def reports(request):
    selected = service.period(request.GET)
    report = service.summary(request, selected)
    dataset = request.GET.get("dataset", "summary")
    headers, rows = service.dataset(request, dataset, selected, report)
    format = request.GET.get("format", "")
    if format:
        raw, mime = service.export_bytes(
            format,
            headers,
            rows,
            service.DATASETS[dataset],
            request.company,
            selected,
            report,
        )
        response = HttpResponse(raw, content_type=mime)
        response["Content-Disposition"] = (
            f'attachment; filename="zefacture-{dataset}.{format}"'
        )
        response["Cache-Control"] = "private, no-store"
        return response
    params = request.GET.copy()
    params.pop("page", None)
    params.pop("format", None)
    return render(
        request,
        "core/reports.html",
        {
            "title": "Rapports et comparatifs",
            "period": selected,
            "report": report,
            "datasets": service.DATASETS,
            "dataset": dataset,
            "headers": headers,
            "rows": Paginator(rows, 50).get_page(request.GET.get("page")),
            "query": params.urlencode(),
        },
    )

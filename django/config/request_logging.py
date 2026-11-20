"""Record HTTP outcomes without copying private request URLs into operational logs."""

import logging
import traceback


class RequestOutcomeFormatter(logging.Formatter):
    def format(self, record):
        request = getattr(record, "request", None)
        match = getattr(request, "resolver_match", None)
        route = getattr(match, "view_name", None) or "route inconnue"
        method = getattr(request, "method", None) or "HTTP"
        status = getattr(record, "status_code", None) or "-"
        parts = [record.levelname, record.name, method, route, str(status)]
        if record.exc_info:
            cls, _, tb = record.exc_info
            parts.append(cls.__name__)
            parts.extend(
                f"{frame.filename}:{frame.lineno} ({frame.name})"
                for frame in traceback.extract_tb(tb)
            )
        return " ".join(parts)

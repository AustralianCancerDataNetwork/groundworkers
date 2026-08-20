from __future__ import annotations

import logging
import re
from typing import Literal, cast, get_args
from uuid import uuid4

ErrorCode = Literal[
    "NOT_FOUND",
    "INVALID_INPUT",
    "BACKEND_UNAVAIL",
    "QUERY_ERROR",
    "FORMAT_BINARY_DECODE",
    "FORMAT_UNRECOGNISED",
    "MISSING_DEPENDENCY",
    "INTERNAL_ERROR",
]

ERROR_CODES = frozenset(get_args(ErrorCode))

_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^\s:/@]+):[^\s/@]+@",
    re.IGNORECASE,
)
_NAMED_SECRET = re.compile(
    r"(?i)\b(password|passwd|api[_-]?key|token|secret|credential)\s*([=:])\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")


def scrub_error_message(message: str) -> str:
    """Remove common credential forms from an operator-facing error message."""

    safe = _URL_CREDENTIALS.sub(
        lambda match: f"{match.group('scheme')}{match.group('user')}:***@",
        str(message),
    )
    safe = _NAMED_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}***",
        safe,
    )
    safe = _BEARER.sub("Bearer ***", safe)
    return safe[:2000]


def internal_error_response(
    exc: BaseException,
    *,
    logger: logging.Logger,
    boundary: str,
) -> dict[str, str | bool]:
    """Log an unexpected defect and return a stable, correlation-safe result."""

    incident_id = uuid4().hex
    logger.exception(
        "Unexpected Groundworkers failure at %s (incident_id=%s)",
        boundary,
        incident_id,
        exc_info=exc,
    )
    return {
        "error": True,
        "code": "INTERNAL_ERROR",
        "message": f"An internal error occurred. Incident ID: {incident_id}.",
    }


class GroundworkersError(Exception):
    def __init__(self, code: ErrorCode | str, message: str):
        if code not in ERROR_CODES:
            raise ValueError(f"Unknown Groundworkers error code: {code}")
        self.code = cast(ErrorCode, code)
        self.message = scrub_error_message(message)
        super().__init__(self.message)

    def to_dict(self) -> dict[str, str | bool]:
        return {"error": True, "code": self.code, "message": self.message}


__all__ = [
    "ERROR_CODES",
    "ErrorCode",
    "GroundworkersError",
    "internal_error_response",
    "scrub_error_message",
]

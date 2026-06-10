from __future__ import annotations


ERROR_CODES = {
    "NOT_FOUND",
    "INVALID_INPUT",
    "BACKEND_UNAVAIL",
    "QUERY_ERROR",
    "FORMAT_BINARY_DECODE",
    "FORMAT_UNRECOGNISED",
    "MISSING_DEPENDENCY",
}


class GroundworkersError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, str | bool]:
        return {"error": True, "code": self.code, "message": self.message}

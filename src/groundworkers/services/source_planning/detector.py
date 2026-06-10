"""Format detection for stateless source planning."""

from __future__ import annotations

import csv
import re

from groundworkers.base.errors import GroundworkersError
from groundworkers.services.source_planning.models import SourceFormat

_XLSX_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"
_RE_CREATE_TABLE = re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)
_HEAD = 4096


class FormatDetector:
    """Identify a submitted source container format from bytes and filename."""

    def detect(self, content: bytes, filename: str | None = None) -> SourceFormat:
        """Return the most likely ``SourceFormat`` for ``content``."""

        if filename:
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext == "xlsx":
                return SourceFormat.XLSX
            if ext == "pdf":
                return SourceFormat.PDF
            if ext == "docx":
                return SourceFormat.DOCX

        if content[:4] == _XLSX_MAGIC:
            head = content[:_HEAD]
            if b"xl/" in head or b"xl\\" in head:
                return SourceFormat.XLSX
            if b"word/" in head:
                return SourceFormat.DOCX
            return SourceFormat.XLSX
        if content[:5] == _PDF_MAGIC:
            return SourceFormat.PDF

        text = _decode_text(content)
        if text is None:
            suffix = f" ({filename!r})" if filename else ""
            raise GroundworkersError(
                "FORMAT_BINARY_DECODE",
                f"Content appears binary but is not a recognised binary format{suffix}",
            )

        stripped = text.lstrip()
        if stripped.startswith(("<", "<?xml")):
            return SourceFormat.XML

        if stripped and stripped[0] in ("{", "["):
            import json

            try:
                json.loads(text)
                return SourceFormat.JSON
            except (ValueError, MemoryError, json.JSONDecodeError):
                pass

        if _RE_CREATE_TABLE.search(text[:65_536]):
            return SourceFormat.DDL_SQL

        try:
            csv.Sniffer().sniff(text[:8_192], delimiters=",\t|;")
            return SourceFormat.CSV
        except csv.Error:
            pass

        suffix = f" for {filename!r}" if filename else ""
        raise GroundworkersError("FORMAT_UNRECOGNISED", f"Cannot identify format{suffix}")


def _decode_text(content: bytes) -> str | None:
    sample = content[:_HEAD]
    if sample and sample.count(b"\x00") > len(sample) // 100:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None

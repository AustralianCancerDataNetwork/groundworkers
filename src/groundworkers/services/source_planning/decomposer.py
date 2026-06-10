"""Dispatch source bytes to format-specific stateless decomposers."""

from __future__ import annotations

from groundworkers.base.errors import GroundworkersError
from groundworkers.services.source_planning.models import RawTable, SourceFormat

_OPTIONAL_DEP_HINT = {
    SourceFormat.XLSX: "pip install groundworkers[xlsx]",
    SourceFormat.PDF: "pip install groundworkers[pdf]",
    SourceFormat.DOCX: "pip install groundworkers[docx]",
}


class TableDecomposer:
    """Convert source bytes into one or more ``RawTable`` artifacts."""

    def decompose(
        self,
        content: bytes,
        source_format: SourceFormat,
        filename: str | None = None,
    ) -> list[RawTable]:
        try:
            return self._dispatch(content, source_format, filename)
        except ImportError as exc:
            hint = _OPTIONAL_DEP_HINT.get(source_format, "pip install groundworkers")
            raise GroundworkersError(
                "MISSING_DEPENDENCY",
                f"Optional dependency missing for {source_format} ingestion: {hint}",
            ) from exc

    def _dispatch(
        self,
        content: bytes,
        source_format: SourceFormat,
        filename: str | None,
    ) -> list[RawTable]:
        if source_format == SourceFormat.CSV:
            from groundworkers.services.source_planning.decomposers import csv_

            return csv_.decompose(content, filename)
        if source_format == SourceFormat.XLSX:
            from groundworkers.services.source_planning.decomposers import xlsx_

            return xlsx_.decompose(content, filename)
        if source_format == SourceFormat.XML:
            from groundworkers.services.source_planning.decomposers import xml_

            return xml_.decompose(content, filename)
        if source_format == SourceFormat.JSON:
            from groundworkers.services.source_planning.decomposers import json_

            return json_.decompose(content, filename)
        if source_format == SourceFormat.DDL_SQL:
            from groundworkers.services.source_planning.decomposers import ddl_

            return ddl_.decompose(content, filename)
        if source_format == SourceFormat.PDF:
            from groundworkers.services.source_planning.decomposers import pdf_

            return pdf_.decompose(content, filename)
        if source_format == SourceFormat.DOCX:
            from groundworkers.services.source_planning.decomposers import docx_

            return docx_.decompose(content, filename)
        return []

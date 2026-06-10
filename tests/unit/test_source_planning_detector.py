from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from groundworkers.base.errors import GroundworkersError
from groundworkers.services.source_planning.detector import FormatDetector
from groundworkers.services.source_planning.models import SourceFormat


def test_detector_identifies_text_based_formats():
    detector = FormatDetector()

    assert detector.detect(b'{"records":[{"code":"A"},{"code":"B"}]}', "records.json") == SourceFormat.JSON
    assert detector.detect(b"<root><record>1</record></root>", "records.xml") == SourceFormat.XML
    assert detector.detect(b"CREATE TABLE demo (id INT PRIMARY KEY);", "demo.sql") == SourceFormat.DDL_SQL
    assert detector.detect(b"code,label\nA,Alpha\nB,Beta\n", "demo.csv") == SourceFormat.CSV


def test_detector_identifies_binary_zip_variants_from_magic_and_manifest():
    detector = FormatDetector()

    xlsx_like = b"PK\x03\x04" + b"xl/workbook.xml"
    docx_like = b"PK\x03\x04" + b"word/document.xml"
    pdf_like = b"%PDF-1.7\n"

    assert detector.detect(xlsx_like, None) == SourceFormat.XLSX
    assert detector.detect(docx_like, None) == SourceFormat.DOCX
    assert detector.detect(pdf_like, None) == SourceFormat.PDF


def test_detector_raises_for_unrecognised_binary_content():
    detector = FormatDetector()

    with pytest.raises(GroundworkersError) as exc_info:
        detector.detect(b"\x00\x00\x00\x00\x01\x02\x03", "mystery.bin")

    assert exc_info.value.code == "FORMAT_BINARY_DECODE"


def test_detector_raises_for_unrecognised_text_content():
    detector = FormatDetector()

    with pytest.raises(GroundworkersError) as exc_info:
        detector.detect(b"just some prose without a stable tabular shape", "notes.txt")

    assert exc_info.value.code == "FORMAT_UNRECOGNISED"

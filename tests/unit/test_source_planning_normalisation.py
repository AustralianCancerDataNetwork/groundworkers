from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.source_planning.models import RawTable, SourceFormat
from groundworkers.source_planning.normalisation import normalise_headers


def test_normalise_headers_cleans_bom_html_duplicates_and_values():
    raw = RawTable(
        name="dictionary",
        headers=["\ufeff Code ", "Code", "<b>Description</b>", "Value   Set"],
        rows=[
            {
                "\ufeff Code ": 101,
                "Code": 102,
                "<b>Description</b>": "<p>Alpha</p>",
                "Value   Set": " A   |   B ",
            }
        ],
        sample_rows=[],
        source_format=SourceFormat.CSV,
        row_count=1,
        metadata={},
    )

    table = normalise_headers(raw)

    assert table.original_headers == ["\ufeff Code ", "Code", "<b>Description</b>", "Value   Set"]
    assert table.headers == ["Code", "Code__2", "Description", "Value Set"]
    assert table.rows == [{"Code": "101", "Code__2": "102", "Description": "Alpha", "Value Set": "A | B"}]
    assert table.sample_rows == [{"Code": "101", "Code__2": "102", "Description": "Alpha", "Value Set": "A | B"}]
    assert table.header_provenance["Code"].operations == ["strip_bom", "trim_whitespace"]
    assert table.header_provenance["Code__2"].operations == ["duplicate_suffix"]
    assert table.header_provenance["Description"].operations == ["strip_html_tags", "collapse_whitespace"]
    assert "duplicate headers were normalized deterministically" in table.normalisation_notes
    assert "html-rich text was reduced to cleaned text surfaces" in table.normalisation_notes
    assert "mixed cell values were coerced to string surfaces" in table.normalisation_notes
    assert [warning.code for warning in table.warnings] == ["DUPLICATE_HEADER_NORMALISED"]


def test_normalise_headers_replaces_empty_headers_deterministically():
    raw = RawTable(
        name="sheets",
        headers=[" ", None],  # type: ignore[list-item]
        rows=[{" ": "x"}],
        sample_rows=[],
        source_format=SourceFormat.XLSX,
        row_count=1,
        metadata={},
    )

    table = normalise_headers(raw)

    assert table.headers == ["column_1", "column_2"]
    assert table.header_provenance["column_1"].operations == ["trim_whitespace", "empty_header_replaced"]
    assert table.header_provenance["column_2"].operations == ["empty_header_replaced"]
    assert [warning.code for warning in table.warnings] == ["EMPTY_HEADER_REPLACED", "EMPTY_HEADER_REPLACED"]

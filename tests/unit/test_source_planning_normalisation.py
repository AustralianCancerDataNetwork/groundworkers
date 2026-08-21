

from groundworkers.services.source_planning.models import RawTable, SourceFormat
from groundworkers.services.source_planning.normalisation import (
    NormalisationPolicy,
    normalise_table,
    normalise_tables,
)


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

    table = normalise_table(raw, policy=NormalisationPolicy(prune_empty_columns=False))

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

    table = normalise_table(raw, policy=NormalisationPolicy(prune_empty_columns=False))

    assert table.headers == ["column_1", "column_2"]
    assert table.header_provenance["column_1"].operations == ["trim_whitespace", "empty_header_replaced"]
    assert table.header_provenance["column_2"].operations == ["empty_header_replaced"]
    assert [warning.code for warning in table.warnings] == ["EMPTY_HEADER_REPLACED", "EMPTY_HEADER_REPLACED"]


def test_normalise_table_prunes_structurally_empty_columns_conservatively():
    raw = RawTable(
        name="prune-demo",
        headers=["Code", "Unused", "Also Empty"],
        rows=[
            {"Code": "A1", "Unused": " ", "Also Empty": ""},
            {"Code": "A2", "Unused": "", "Also Empty": None},
        ],
        sample_rows=[],
        source_format=SourceFormat.CSV,
        row_count=2,
        metadata={},
    )

    table = normalise_table(raw)

    assert table.headers == ["Code"]
    assert table.rows == [{"Code": "A1"}, {"Code": "A2"}]
    assert "structurally empty columns were pruned" in table.normalisation_notes
    assert [warning.code for warning in table.warnings] == ["EMPTY_COLUMN_PRUNED", "EMPTY_COLUMN_PRUNED"]


def test_normalise_table_can_preserve_empty_columns_when_policy_disables_pruning():
    raw = RawTable(
        name="preserve-demo",
        headers=["Code", "Unused"],
        rows=[{"Code": "A1", "Unused": ""}],
        sample_rows=[],
        source_format=SourceFormat.CSV,
        row_count=1,
        metadata={},
    )

    table = normalise_table(raw, policy=NormalisationPolicy(prune_empty_columns=False))

    assert table.headers == ["Code", "Unused"]
    assert table.rows == [{"Code": "A1", "Unused": ""}]
    assert all(warning.code != "EMPTY_COLUMN_PRUNED" for warning in table.warnings)


def test_normalise_tables_applies_shared_policy_across_multiple_tables():
    tables = [
        RawTable(
            name="one",
            headers=["Code", "Unused"],
            rows=[{"Code": "1", "Unused": ""}],
            sample_rows=[],
            source_format=SourceFormat.CSV,
            row_count=1,
            metadata={},
        ),
        RawTable(
            name="two",
            headers=["\ufeff Label "],
            rows=[{"\ufeff Label ": "  Alpha  "}],
            sample_rows=[],
            source_format=SourceFormat.CSV,
            row_count=1,
            metadata={"banner_row_removed": True},
        ),
    ]

    normalised = normalise_tables(tables)

    assert [table.headers for table in normalised] == [["Code"], ["Label"]]
    assert "a banner/title row was removed during format-specific extraction" in normalised[1].normalisation_notes

from __future__ import annotations

import builtins
import io

import pytest

from groundworkers.base.errors import GroundworkersError
from groundworkers.services.source_planning.decomposer import TableDecomposer
from groundworkers.services.source_planning.models import SourceFormat
from groundworkers.services.source_planning.service import SourcePlanningService


def _xlsx_bytes() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "conditions"
    sheet["A1"] = "Example registry export"
    sheet["A2"], sheet["B2"], sheet["C2"] = "Code", "Code", "Label"
    sheet["A3"], sheet["B3"], sheet["C3"] = "E11.9", "E11.9-alt", "Diabetes"
    sheet["A4"], sheet["B4"], sheet["C4"] = None, "F00", "Dementia"
    sheet.merge_cells("A3:A4")
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _docx_bytes(*, with_table: bool = True) -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    if with_table:
        table = document.add_table(rows=3, cols=2)
        for row, values in zip(table.rows, (("Domain", "Code"), ("Condition", "E11.9"), ("", "F00"))):
            for cell, value in zip(row.cells, values):
                cell.text = value
        table.cell(1, 0).merge(table.cell(2, 0))
    else:
        document.add_heading("Conditions", level=1)
        document.add_paragraph("E11.9 maps to diabetes.")
        document.add_paragraph("F00 maps to dementia.")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _minimal_pdf(texts: tuple[str, ...], *, table: bool) -> bytes:
    """Build a tiny PDF with stdlib only; table geometry is real PDF drawing data."""
    commands = []
    if table:
        for y in (160, 135, 110):
            commands.append(f"50 {y} m 250 {y} l S")
        for x in (50, 150, 250):
            commands.append(f"{x} 160 m {x} 110 l S")
        positions = ((55, 143), (155, 143), (55, 118), (155, 118))
        cells = ("code", "label", "E11.9", "Diabetes")
        for (x, y), value in zip(positions, cells):
            commands.append(f"BT /F1 10 Tf {x} {y} Td ({value}) Tj ET")
    else:
        commands.extend(f"BT /F1 10 Tf 50 {160 - n * 16} Td ({value}) Tj ET" for n, value in enumerate(texts))
    stream = ("\n".join(commands)).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(pdf)


@pytest.mark.required_format
def test_xlsx_real_workbook_removes_banner_fills_merges_and_deduplicates_headers():
    tables = TableDecomposer().decompose(_xlsx_bytes(), SourceFormat.XLSX, "conditions.xlsx")

    assert len(tables) == 1
    table = tables[0]
    assert table.name == "conditions"
    assert table.headers == ["Code", "Code_2", "Label"]
    assert table.rows == [
        {"Code": "E11.9", "Code_2": "E11.9-alt", "Label": "Diabetes"},
        {"Code": "E11.9", "Code_2": "F00", "Label": "Dementia"},
    ]
    assert table.metadata["banner_row_removed"] is True
    plan = SourcePlanningService().plan_source(_xlsx_bytes(), filename="conditions.xlsx")
    assert plan.plan.format_detected is SourceFormat.XLSX
    assert plan.plan.tables[0].headers == table.headers


@pytest.mark.required_format
def test_docx_real_table_fills_vertical_merge_and_paragraph_fallback():
    decomposer = TableDecomposer()
    table = decomposer.decompose(_docx_bytes(), SourceFormat.DOCX, "conditions.docx")[0]
    fallback = decomposer.decompose(
        _docx_bytes(with_table=False), SourceFormat.DOCX, "notes.docx"
    )[0]

    assert table.headers == ["Domain", "Code"]
    assert [row["Domain"] for row in table.rows] == ["Condition", "Condition"]
    assert [row["Code"] for row in table.rows] == ["E11.9", "F00"]
    assert fallback.metadata == {"docx_source": "paragraphs"}
    assert fallback.rows == [
        {"section": "Conditions", "text": "E11.9 maps to diabetes. F00 maps to dementia."}
    ]
    planned = SourcePlanningService().plan_source(_docx_bytes(), filename="conditions.docx")
    assert planned.plan.format_detected is SourceFormat.DOCX
    assert planned.plan.tables[0].headers == table.headers


@pytest.mark.required_format
def test_pdf_real_table_and_text_fallback():
    pytest.importorskip("pdfplumber")
    decomposer = TableDecomposer()
    table = decomposer.decompose(_minimal_pdf((), table=True), SourceFormat.PDF, "codes.pdf")[0]
    fallback = decomposer.decompose(
        _minimal_pdf(("Conditions", "E11.9 is diabetes."), table=False),
        SourceFormat.PDF,
        "notes.pdf",
    )[0]

    assert table.headers == ["code", "label"]
    assert table.rows == [{"code": "E11.9", "label": "Diabetes"}]
    assert fallback.metadata == {"pdf_source": "text"}
    assert "E11.9 is diabetes." in fallback.rows[0]["text"]
    planned = SourcePlanningService().plan_source(
        _minimal_pdf((), table=True), filename="codes.pdf"
    )
    assert planned.plan.format_detected is SourceFormat.PDF
    assert planned.plan.tables[0].headers == table.headers


@pytest.mark.parametrize(
    ("source_format", "content", "filename"),
    [
        (SourceFormat.XLSX, b"not an xlsx", "x.xlsx"),
        (SourceFormat.DOCX, b"not a docx", "x.docx"),
        (SourceFormat.PDF, b"not a pdf", "x.pdf"),
    ],
)
def test_corrupt_document_returns_no_tables(source_format, content, filename):
    assert TableDecomposer().decompose(content, source_format, filename) == []


@pytest.mark.parametrize(
    ("source_format", "content", "filename"),
    [
        (SourceFormat.XLSX, lambda: _empty_xlsx(), "empty.xlsx"),
        (SourceFormat.DOCX, lambda: _empty_docx(), "empty.docx"),
        (SourceFormat.PDF, lambda: _minimal_pdf((), table=False), "empty.pdf"),
    ],
)
def test_empty_document_returns_no_tables(source_format, content, filename):
    assert TableDecomposer().decompose(content(), source_format, filename) == []


def _empty_xlsx() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    output = io.BytesIO()
    openpyxl.Workbook().save(output)
    return output.getvalue()


def _empty_docx() -> bytes:
    docx = pytest.importorskip("docx")
    output = io.BytesIO()
    docx.Document().save(output)
    return output.getvalue()


@pytest.mark.parametrize(
    ("source_format", "module_name", "hint"),
    [
        (SourceFormat.XLSX, "openpyxl", "groundworkers[xlsx]"),
        (SourceFormat.DOCX, "docx", "groundworkers[docx]"),
        (SourceFormat.PDF, "pdfplumber", "groundworkers[pdf]"),
    ],
)
def test_missing_optional_parser_reports_install_hint(monkeypatch, source_format, module_name, hint):
    original_import = builtins.__import__

    def missing_parser(name, *args, **kwargs):
        if name == module_name:
            raise ImportError(f"blocked {module_name} for contract test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_parser)
    with pytest.raises(GroundworkersError) as excinfo:
        TableDecomposer().decompose(b"x", source_format, "x")
    assert excinfo.value.code == "MISSING_DEPENDENCY"
    assert hint in excinfo.value.message

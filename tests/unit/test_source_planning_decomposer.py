

from groundworkers.services.source_planning.decomposer import TableDecomposer
from groundworkers.services.source_planning.decomposers import ddl_, xml_
from groundworkers.services.source_planning.models import SourceFormat


def test_table_decomposer_routes_csv_and_preserves_metadata():
    decomposer = TableDecomposer()

    tables = decomposer.decompose(
        b"\xef\xbb\xbfcode,label\nA,Alpha\nB,Beta\n",
        SourceFormat.CSV,
        "demo.csv",
    )

    assert len(tables) == 1
    table = tables[0]
    assert table.name == "demo"
    assert table.headers == ["code", "label"]
    assert table.rows[0]["code"] == "A"
    assert table.metadata["encoding_used"] == "utf-8-sig"


def test_table_decomposer_routes_json_to_multiple_candidate_tables():
    decomposer = TableDecomposer()
    content = b'{"records":[{"code":"A","label":"Alpha","nested":[{"id":"1","value":"x"},{"id":"2","value":"y"}]},{"code":"B","label":"Beta","nested":[{"id":"3","value":"z"},{"id":"4","value":"w"}]}]}'

    tables = decomposer.decompose(content, SourceFormat.JSON, "records.json")

    names = {table.name for table in tables}
    assert "records" in names
    assert "nested" in names


def test_ddl_decomposer_parses_pk_and_fk_constraints():
    content = b"""
    CREATE TABLE patient (
      patient_id INT PRIMARY KEY,
      provider_id INT,
      provider_name VARCHAR(50),
      CONSTRAINT fk_provider FOREIGN KEY (provider_id) REFERENCES provider(provider_id)
    );
    """

    tables = ddl_.decompose(content, "schema.sql")

    assert len(tables) == 1
    table = tables[0]
    assert table.name == "patient"
    assert table.headers == ["column_name", "data_type", "is_pk", "fk_table"]
    assert table.rows[0]["column_name"] == "patient_id"
    assert table.rows[0]["is_pk"] == "YES"
    provider_row = next(row for row in table.rows if row["column_name"] == "provider_id")
    assert provider_row["fk_table"] == "provider.provider_id"


def test_recursive_xml_flattening_emits_path_columns():
    content = b"""
    <root>
      <record id="1">
        <code system="SYS_A">A</code>
        <details>
          <description>Alpha</description>
          <preferred-term>Alpha label</preferred-term>
          <effective>
            <date>2026-01-01</date>
          </effective>
        </details>
        <external_ref resource="http://example.test/ref-a">
          <code>REF001</code>
        </external_ref>
      </record>
      <record id="2">
        <code system="SYS_A">B</code>
        <details>
          <description>Beta</description>
          <preferred-term>Beta label</preferred-term>
        </details>
        <external_ref resource="http://example.test/ref-a">
          <code>REF002</code>
        </external_ref>
      </record>
    </root>
    """

    table = next(
        table for table in xml_.decompose(content, "records.xml") if table.metadata["xml_record_tag"] == "record"
    )

    assert table.name == "record"
    assert table.row_count == 2
    assert table.headers == [
        "@id",
        "code.@system",
        "code",
        "details.description",
        "details.preferred-term",
        "details.effective.date",
        "external_ref.@resource",
        "external_ref.code",
    ]
    assert table.rows[0]["@id"] == "1"
    assert table.rows[0]["details.description"] == "Alpha"
    assert table.rows[0]["details.effective.date"] == "2026-01-01"
    assert table.rows[0]["external_ref.code"] == "REF001"
    assert table.rows[1]["details.effective.date"] == ""


def test_repeated_descendants_are_aggregated_without_overwrite():
    content = b"""
    <root>
      <record>
        <code system="A">111</code>
        <code system="B">222</code>
        <refs>
          <ref href="#1" />
          <ref href="#2" />
        </refs>
      </record>
      <record>
        <code system="C">333</code>
      </record>
    </root>
    """

    table = next(
        table for table in xml_.decompose(content, "repeated.xml") if table.metadata["xml_record_tag"] == "record"
    )

    assert table.rows[0]["code"] == "111 | 222"
    assert table.rows[0]["code.@system"] == "A | B"
    assert table.rows[0]["refs.ref.@href"] == "#1 | #2"
    assert table.rows[1]["code"] == "333"

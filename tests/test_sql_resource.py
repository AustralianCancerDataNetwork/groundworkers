from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

from groundworkers.base.sql import SQLResource, SQLTextSearchResource
from groundworkers.config import FullTextConfig, SqlResourceConfig


def build_engine_and_table():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    metadata = MetaData()
    table = Table(
        "concept",
        metadata,
        Column("concept_id", Integer, primary_key=True),
        Column("concept_name", String),
        Column("concept_code", String),
        Column("domain_id", String),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            [
                {"concept_id": 1, "concept_name": "Lung cancer", "concept_code": "C34", "domain_id": "Condition"},
                {"concept_id": 2, "concept_name": "Breast cancer", "concept_code": "C50", "domain_id": "Condition"},
            ],
        )
    return engine


def test_sql_resource_lookup_and_list():
    engine = build_engine_and_table()
    config = SqlResourceConfig(
        table="concept",
        primary_key="concept_id",
        allowed_filter_fields=["domain_id"],
        display_fields=["concept_id", "concept_name"],
    )
    resource = SQLResource(engine, "concept", config)
    detail = resource.get(1)
    assert detail.item == {"concept_id": 1, "concept_name": "Lung cancer"}
    listing = resource.list(filters={"domain_id": "Condition"})
    assert listing.total == 2
    assert listing.items[0]["concept_name"] == "Lung cancer"


def test_sql_text_search_resource_falls_back_to_like():
    engine = build_engine_and_table()
    config = SqlResourceConfig(table="concept", primary_key="concept_id")
    fulltext = FullTextConfig(table="concept", search_fields=["concept_name", "concept_code"])
    resource = SQLTextSearchResource(engine, "concept_search", config, fulltext)
    result = resource.search("Lung")
    assert result.items
    assert result.items[0].payload["concept_name"] == "Lung cancer"

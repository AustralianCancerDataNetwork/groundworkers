from __future__ import annotations

from datetime import date

import pytest
from oa_configurator import CDMDatabaseConfig, ConnectionConfig, Resolver
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Relationship
from omop_alchemy.config import create_cdm_engines
from omop_alchemy.cross_database import cdm_sessionmaker
from omop_graph.extensions.omop_alchemy import RelationshipMapping

from groundworkers.services.graph import GraphService
from tests.support.stack_config import build_cdm_stack


@pytest.mark.parametrize("split", [False, True])
def test_classified_edges_read_primary_mapping_and_vocab_edges(
    tmp_path, split: bool
) -> None:
    stack = build_cdm_stack(sqlite_path=str(tmp_path / "primary.db"))
    if split:
        stack.connections["vocab_db"] = ConnectionConfig(
            dialect="sqlite", database_name=str(tmp_path / "vocabulary.db")
        )
        stack.databases["cdm_db"] = CDMDatabaseConfig(
            connection="cdm_main", vocab_connection="vocab_db"
        )
    resolved = Resolver(stack).resolve_database("cdm_db")
    primary, vocab = create_cdm_engines(resolved, register_claims=False)
    RelationshipMapping.__table__.create(primary)
    Concept.__table__.create(vocab)
    Concept_Relationship.__table__.create(vocab)
    with primary.begin() as connection:
        connection.execute(
            RelationshipMapping.__table__.insert(),
            {
                "relationship_id": "Is a",
                "predicate_kind": "Hierarchy",
                "predicate_subkind": "Taxonomic - up",
            },
        )
    with vocab.begin() as connection:
        connection.execute(
            Concept.__table__.insert(),
            {
                "concept_id": 2,
                "concept_name": "Target",
                "domain_id": "Condition",
                "vocabulary_id": "SNOMED",
                "concept_class_id": "Clinical Finding",
                "standard_concept": "S",
                "concept_code": "2",
                "valid_start_date": date(2000, 1, 1),
                "valid_end_date": date(2099, 12, 31),
            },
        )
        connection.execute(
            Concept_Relationship.__table__.insert(),
            {
                "concept_id_1": 1,
                "concept_id_2": 2,
                "relationship_id": "Is a",
                "valid_start_date": date(2000, 1, 1),
                "valid_end_date": date(2099, 12, 31),
            },
        )

    session_factory = cdm_sessionmaker(resolved, primary=primary, vocab=vocab)
    try:
        with session_factory() as session:
            rows = GraphService._query_classified_edges(
                session,
                1,
                "Hierarchy",
                None,
                False,
                10,
                outbound=True,
            )
        assert rows == [
            {
                "relationship_id": "Is a",
                "predicate_subkind": "Taxonomic - up",
                "target_concept_id": 2,
                "target_concept_name": "Target",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "concept_class_id": "Clinical Finding",
                "standard_concept": "S",
                "valid": True,
            }
        ]
    finally:
        primary.dispose()
        if vocab is not primary:
            vocab.dispose()

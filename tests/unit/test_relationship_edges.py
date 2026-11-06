from datetime import date

import pytest
from oa_configurator import CDMDatabaseConfig, ConnectionConfig, Resolver
from omop_alchemy.cdm.model.vocabulary import Concept_Relationship
from omop_alchemy.config import create_cdm_engines
from omop_alchemy.cross_database import cdm_sessionmaker

from groundworkers.adapters.omop_graph import OmopGraphAdapter
from tests.support.stack_config import build_cdm_stack


@pytest.mark.parametrize("split", [False, True])
def test_relationship_edges_filters_orders_and_limits_active_outbound_rows(
    tmp_path, split
):
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
    try:
        Concept_Relationship.__table__.create(vocab)
        with vocab.begin() as connection:
            connection.execute(
                Concept_Relationship.__table__.insert(),
                [
                    {
                        "concept_id_1": source,
                        "concept_id_2": target,
                        "relationship_id": relationship,
                        "invalid_reason": invalid,
                        "valid_start_date": date(2000, 1, 1),
                        "valid_end_date": date(2099, 12, 31),
                    }
                    for source, target, relationship, invalid in [
                        (1, 9, "Box of", ""),
                        (1, 4, "Tradename of", " "),
                        (1, 4, "Box of", None),
                        (1, 3, "Box of", ""),
                        (1, 2, "Box of", "D"),
                        (1, 2, "Is a", ""),
                        (2, 1, "Box of", ""),
                    ]
                ],
            )
        session_factory = cdm_sessionmaker(resolved, primary=primary, vocab=vocab)
        adapter = OmopGraphAdapter(primary, cdm_session_factory=session_factory)
        expected = [
            {
                "target_concept_id": target,
                "relationship_id": relationship,
                "valid": True,
            }
            for target, relationship in [
                (3, "Box of"),
                (4, "Box of"),
                (4, "Tradename of"),
                (9, "Box of"),
            ]
        ]

        assert adapter.relationship_edges(1, ["Tradename of", "Box of"], 10) == expected
        assert (
            adapter.relationship_edges(1, ["Tradename of", "Box of"], 2) == expected[:2]
        )
    finally:
        primary.dispose()
        if vocab is not primary:
            vocab.dispose()

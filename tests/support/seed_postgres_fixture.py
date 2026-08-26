"""Seed the compact OMOP vocabulary used by PostgreSQL integration tests.

This is deliberately a fixture, not a substitute for loading an OMOP vocabulary.
The integration suite checks Groundworkers' database contract and graph behaviour;
it must therefore be repeatable and independent of a developer's local vocabulary.
"""

from __future__ import annotations

import os
from datetime import date

from omop_alchemy.cdm.model.vocabulary import (  # noqa: F401
    Concept,
    Concept_Ancestor,
    Concept_Class,
    Concept_Relationship,
    Concept_Synonym,
    Domain,
    Relationship,
    Vocabulary,
)
from omop_graph.extensions.omop_alchemy import (  # noqa: F401
    RelationshipClass,
    RelationshipMapping,
)
from orm_loader.helpers import bootstrap
from sqlalchemy import create_engine, text

# Importing the vocabulary models before bootstrapping registers their tables with
# orm-loader's shared metadata.
DATABASE_URL_ENV = "GROUNDWORKERS_TEST_DATABASE_URL"


def _database_url() -> str:
    value = os.getenv(DATABASE_URL_ENV)
    if not value:
        raise RuntimeError(f"{DATABASE_URL_ENV} must be set")
    return value


def seed() -> None:
    engine = create_engine(_database_url(), future=True)
    bootstrap(engine, create=True)

    concepts = [
        (201826, "Type 2 diabetes mellitus", "Condition", "SNOMED", "Clinical Finding", "S", "44054006"),
        (201827, "Diabetes mellitus", "Condition", "SNOMED", "Clinical Finding", "S", "73211009"),
        (201828, "Type 1 diabetes mellitus", "Condition", "SNOMED", "Clinical Finding", "S", "46635009"),
        (201829, "Asthma", "Condition", "SNOMED", "Clinical Finding", "S", "195967001"),
        (201830, "Metformin", "Drug", "RxNorm", "Ingredient", "S", "6809"),
        (201831, "Appendectomy", "Procedure", "SNOMED", "Procedure", "S", "80146002"),
        (201832, "Type 2 diabetes mellitus, unspecified", "Condition", "ICD10CM", "Clinical Finding", None, "E11.9"),
        (201833, "Source diabetes mellitus", "Condition", "LOCAL", "Clinical Finding", None, "SOURCE-DM"),
        (201834, "Biguanide", "Drug", "ATC", "Classification", "C", "A10BA"),
        (201835, "Type Concept", "Type Concept", "CDM", "Type Concept", "S", "Type Concept"),
        (201836, "Metadata field", "Metadata", "CDM", "Field", "S", "metadata-field"),
    ]

    domains = [
        ("Condition", "Condition", 201826),
        ("Drug", "Drug", 201830),
        ("Procedure", "Procedure", 201831),
        ("Type Concept", "Type Concept", 201835),
        ("Metadata", "Metadata", 201836),
    ]
    vocabularies = [
        ("SNOMED", "SNOMED-CT", "IHTSDO", "fixture" , 201826),
        ("ICD10CM", "ICD-10-CM", "WHO", "fixture", 201832),
        ("RxNorm", "RxNorm", "NLM", "fixture", 201830),
        ("ATC", "ATC", "WHO", "fixture", 201834),
        ("LOCAL", "Fixture source vocabulary", "Groundworkers", "fixture", 201833),
        ("CDM", "Common Data Model", "OHDSI", "5.4", 201835),
    ]
    concept_classes = [
        ("Clinical Finding", "Clinical Finding", 0),
        ("Ingredient", "Ingredient", 0),
        ("Procedure", "Procedure", 0),
        ("Classification", "Classification", 0),
        ("Type Concept", "Type Concept", 201835),
        ("Field", "Field", 201836),
    ]
    relationships = [
        ("Is a", "Is a", "1", "1", "Subsumes", 0),
        ("Subsumes", "Subsumes", "1", "0", "Is a", 0),
        ("Maps to", "Maps to", "0", "0", "Maps to", 0),
    ]
    relationship_classes = [
        ("Hierarchy", "Taxonomic – up", "Hierarchy up", "Inheritance", "Ancestor traversal"),
        ("Hierarchy", "Taxonomic – down", "Hierarchy down", "Refinement", "Descendant traversal"),
        ("Identity", "Exact equivalence", "Exact mapping", "Interchangeable", "Cross-vocabulary linking"),
    ]
    relationship_mappings = [
        ("Is a", "Hierarchy", "Taxonomic – up"),
        ("Subsumes", "Hierarchy", "Taxonomic – down"),
        ("Maps to", "Identity", "Exact equivalence"),
    ]
    concept_relationships = [
        (201826, 201827, "Is a"),
        (201828, 201827, "Is a"),
        (201829, 201827, "Is a"),
        (201832, 201826, "Maps to"),
        (201833, 201826, "Maps to"),
    ]
    ancestors = [
        (201826, 201826, 0, 0),
        (201827, 201826, 1, 1),
        (201827, 201828, 1, 1),
        (201827, 201829, 1, 1),
    ]
    synonyms = [
        (201826, "adult-onset diabetes", 0),
        (201830, "metformin hydrochloride", 0),
    ]
    start = date(1970, 1, 1)
    end = date(2099, 12, 31)

    with engine.begin() as connection:
        # The OMOP reference tables contain a deliberate cycle: concepts point
        # at domain/vocabulary/class rows, while those rows point at concepts.
        # The real vocabulary loader handles this cycle; this tiny fixture uses
        # the PostgreSQL test superuser to load the same compact data safely.
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("SET session_replication_role = replica"))
        try:
            connection.execute(
                text(
                    "INSERT INTO domain "
                    "(domain_id, domain_name, domain_concept_id) "
                    "VALUES (:domain_id, :domain_name, :domain_concept_id)"
                ),
                [
                    {
                        "domain_id": domain_id,
                        "domain_name": domain_name,
                        "domain_concept_id": concept_id,
                    }
                    for domain_id, domain_name, concept_id in domains
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO vocabulary "
                    "(vocabulary_id, vocabulary_name, vocabulary_reference, "
                    "vocabulary_version, vocabulary_concept_id) "
                    "VALUES (:vocabulary_id, :vocabulary_name, :vocabulary_reference, "
                    ":vocabulary_version, :vocabulary_concept_id)"
                ),
                [
                    {
                        "vocabulary_id": vocabulary_id,
                        "vocabulary_name": vocabulary_name,
                        "vocabulary_reference": reference,
                        "vocabulary_version": version,
                        "vocabulary_concept_id": concept_id,
                    }
                    for vocabulary_id, vocabulary_name, reference, version, concept_id in vocabularies
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO concept_class "
                    "(concept_class_id, concept_class_name, concept_class_concept_id) "
                    "VALUES (:concept_class_id, :concept_class_name, :concept_class_concept_id)"
                ),
                [
                    {
                        "concept_class_id": class_id,
                        "concept_class_name": class_name,
                        "concept_class_concept_id": concept_id,
                    }
                    for class_id, class_name, concept_id in concept_classes
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO relationship "
                    "(relationship_id, relationship_name, is_hierarchical, "
                    "defines_ancestry, reverse_relationship_id, relationship_concept_id) "
                    "VALUES (:relationship_id, :relationship_name, :is_hierarchical, "
                    ":defines_ancestry, :reverse_relationship_id, :relationship_concept_id)"
                ),
                [
                    {
                        "relationship_id": relationship_id,
                        "relationship_name": relationship_name,
                        "is_hierarchical": is_hierarchical,
                        "defines_ancestry": defines_ancestry,
                        "reverse_relationship_id": reverse_relationship_id,
                        "relationship_concept_id": concept_id,
                    }
                    for relationship_id, relationship_name, is_hierarchical, defines_ancestry, reverse_relationship_id, concept_id in relationships
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO relationship_class "
                    "(predicate_kind, predicate_subkind, description, semantics, inference) "
                    "VALUES (:predicate_kind, :predicate_subkind, :description, :semantics, :inference)"
                ),
                [
                    {
                        "predicate_kind": predicate_kind,
                        "predicate_subkind": predicate_subkind,
                        "description": description,
                        "semantics": semantics,
                        "inference": inference,
                    }
                    for predicate_kind, predicate_subkind, description, semantics, inference in relationship_classes
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO relationship_mapping "
                    "(relationship_id, predicate_kind, predicate_subkind) "
                    "VALUES (:relationship_id, :predicate_kind, :predicate_subkind)"
                ),
                [
                    {
                        "relationship_id": relationship_id,
                        "predicate_kind": predicate_kind,
                        "predicate_subkind": predicate_subkind,
                    }
                    for relationship_id, predicate_kind, predicate_subkind in relationship_mappings
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO concept "
                    "(concept_id, concept_name, domain_id, vocabulary_id, concept_class_id, "
                    "standard_concept, concept_code, valid_start_date, valid_end_date, invalid_reason) "
                    "VALUES (:concept_id, :concept_name, :domain_id, :vocabulary_id, :concept_class_id, "
                    ":standard_concept, :concept_code, :valid_start_date, :valid_end_date, NULL)"
                ),
                [
                    {
                        "concept_id": concept_id,
                        "concept_name": concept_name,
                        "domain_id": domain_id,
                        "vocabulary_id": vocabulary_id,
                        "concept_class_id": concept_class_id,
                        "standard_concept": standard_concept,
                        "concept_code": concept_code,
                        "valid_start_date": start,
                        "valid_end_date": end,
                    }
                    for concept_id, concept_name, domain_id, vocabulary_id, concept_class_id, standard_concept, concept_code in concepts
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO concept_relationship "
                    "(concept_id_1, concept_id_2, relationship_id, valid_start_date, valid_end_date, invalid_reason) "
                    "VALUES (:concept_id_1, :concept_id_2, :relationship_id, :valid_start_date, :valid_end_date, NULL)"
                ),
                [
                    {
                        "concept_id_1": concept_id_1,
                        "concept_id_2": concept_id_2,
                        "relationship_id": relationship_id,
                        "valid_start_date": start,
                        "valid_end_date": end,
                    }
                    for concept_id_1, concept_id_2, relationship_id in concept_relationships
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO concept_ancestor "
                    "(ancestor_concept_id, descendant_concept_id, min_levels_of_separation, max_levels_of_separation) "
                    "VALUES (:ancestor_concept_id, :descendant_concept_id, :min_levels, :max_levels)"
                ),
                [
                    {
                        "ancestor_concept_id": ancestor_id,
                        "descendant_concept_id": descendant_id,
                        "min_levels": min_levels,
                        "max_levels": max_levels,
                    }
                    for ancestor_id, descendant_id, min_levels, max_levels in ancestors
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO concept_synonym "
                    "(concept_id, concept_synonym_name, language_concept_id) "
                    "VALUES (:concept_id, :concept_synonym_name, :language_concept_id)"
                ),
                [
                    {
                        "concept_id": concept_id,
                        "concept_synonym_name": synonym,
                        "language_concept_id": language_concept_id,
                    }
                    for concept_id, synonym, language_concept_id in synonyms
                ],
            )
            connection.execute(
                text(
                    "ALTER TABLE concept "
                    "ADD COLUMN IF NOT EXISTS concept_name_tsvector tsvector"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE concept_synonym "
                    "ADD COLUMN IF NOT EXISTS concept_synonym_name_tsvector tsvector"
                )
            )
            connection.execute(
                text(
                    "UPDATE concept SET concept_name_tsvector = "
                    "to_tsvector('english', concept_name)"
                )
            )
            connection.execute(
                text(
                    "UPDATE concept_synonym SET concept_synonym_name_tsvector = "
                    "to_tsvector('english', concept_synonym_name)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_concept_name_tsvector "
                    "ON concept USING gin (concept_name_tsvector)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_concept_synonym_name_tsvector "
                    "ON concept_synonym USING gin (concept_synonym_name_tsvector)"
                )
            )
        finally:
            connection.execute(text("SET session_replication_role = DEFAULT"))


if __name__ == "__main__":
    seed()

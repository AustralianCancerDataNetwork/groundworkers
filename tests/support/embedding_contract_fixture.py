from __future__ import annotations

import numpy as np
from omop_emb.backends import open_vector_store_writer
from omop_emb.backends.embedding_table import ConceptEmbeddingRecord
from omop_emb.backends.index_config import FlatIndexConfig

MODEL_NAME = "groundworkers-contract:v1"
OTHER_MODEL_NAME = "groundworkers-secondary:v1"


def seed_embedding_contract(config) -> None:
    if config.vector_store is None:
        raise AssertionError("embedding fixture needs a configured vector store")
    records = (
        ConceptEmbeddingRecord(201826, "Condition", "SNOMED", True, True),
        ConceptEmbeddingRecord(201827, "Condition", "SNOMED", True, True),
        ConceptEmbeddingRecord(201828, "Condition", "SNOMED", True, True),
        ConceptEmbeddingRecord(201830, "Drug", "RxNorm", True, True),
        ConceptEmbeddingRecord(201834, "Drug", "ATC", False, True),
        ConceptEmbeddingRecord(201837, "Condition", "LOCAL", False, True),
        ConceptEmbeddingRecord(201838, "Condition", "SNOMED", True, False),
    )
    vectors = np.asarray(
        [
            [1.0, 0.0, 0.0],
            [0.95, 0.05, 0.0],
            [0.0, 1.0, 0.0],
            [0.85, 0.15, 0.0],
            [0.8, 0.2, 0.0],
            [0.9, 0.1, 0.0],
            [0.99, 0.01, 0.0],
        ],
        dtype=np.float32,
    )
    writer = open_vector_store_writer(config.vector_store)
    try:
        for model_name, model_records, model_vectors in (
            (MODEL_NAME, records, vectors),
            (OTHER_MODEL_NAME, records[:1], vectors[:1]),
        ):
            if not writer.get_registered_models(model_name=model_name):
                writer.register_model(
                    model_name=model_name,
                    provider_type="ollama",
                    index_config=FlatIndexConfig(),
                    dimensions=3,
                )
            writer.upsert_embeddings(
                model_name=model_name,
                records=model_records,
                embeddings=model_vectors,
            )
    finally:
        writer.close()

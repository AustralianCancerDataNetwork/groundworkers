"""oa-configurator 1.x stack fixtures shared across migration tests."""

from __future__ import annotations

from typing import Literal

from oa_configurator import (
    CDMDatabaseConfig,
    ConnectionConfig,
    GenericDatabaseConfig,
    ModelConfig,
    ProviderConfig,
    StackConfig,
    VectorStoreConfig,
)

EmbeddingBackend = Literal["sqlitevec", "pgvector"]
InvalidReference = Literal[
    "missing_cdm",
    "wrong_cdm_kind",
    "missing_model",
    "missing_vector_store",
]


def build_cdm_stack(
    *,
    connection_name: str = "cdm_main",
    database_name: str = "cdm_db",
    sqlite_path: str = ":memory:",
    schema_name: str = "main",
    vocab_schema: str | None = "main",
    results_schema: str | None = None,
    groundworkers: dict[str, object] | None = None,
) -> StackConfig:
    """Build the smallest valid Groundworkers CDM-only stack."""

    tool = {
        "cdm_db": database_name,
        **(groundworkers or {}),
    }
    return StackConfig(
        connections={
            connection_name: ConnectionConfig(
                dialect="sqlite",
                database_name=sqlite_path,
            )
        },
        databases={
            database_name: CDMDatabaseConfig(
                connection=connection_name,
                schema_name=schema_name,
                vocab_schema=vocab_schema,
                results_schema=results_schema,
            )
        },
        tools={"groundworkers": tool},
    )


def build_embedding_stack(
    backend: EmbeddingBackend = "sqlitevec",
    *,
    provider_name: str = "embedding_provider",
    model_entry_name: str = "embedding_model",
    model_name: str = "nomic-embed-text:latest",
    vector_store_name: str = "embedding_store",
    vector_database_name: str = "embedding_db",
    vector_connection_name: str = "embedding_main",
) -> StackConfig:
    """Build a valid stack with a named provider, model, and vector store."""

    stack = build_cdm_stack(
        groundworkers={
            "embedding_model_name": model_entry_name,
            "vector_store_name": vector_store_name,
        }
    )
    if backend == "sqlitevec":
        connection = ConnectionConfig(
            dialect="sqlite",
            database_name="embeddings.db",
        )
        schema_name = "main"
    else:
        connection = ConnectionConfig(
            dialect="postgresql+psycopg",
            host="postgres.example.test",
            port=5432,
            user="groundworkers",
            password="fixture-password",
            database_name="embeddings",
        )
        schema_name = "groundworkers"

    stack.connections[vector_connection_name] = connection
    stack.databases[vector_database_name] = GenericDatabaseConfig(
        connection=vector_connection_name,
        schema_name=schema_name,
    )
    stack.providers[provider_name] = ProviderConfig(
        provider="ollama",
        base_url="http://models.example.test",
    )
    stack.models[model_entry_name] = ModelConfig(
        provider=provider_name,
        model=model_name,
        embeddings=True,
    )
    stack.vector_stores[vector_store_name] = VectorStoreConfig(
        backend_type=backend,
        database=vector_database_name,
        faiss_cache_dir="faiss-cache",
    )
    return stack


def build_invalid_reference_stack(issue: InvalidReference) -> StackConfig:
    """Build a structurally valid stack with one deliberate reference error."""

    if issue in {"missing_model", "missing_vector_store"}:
        stack = build_embedding_stack()
    else:
        stack = build_cdm_stack()

    tool = stack.tools["groundworkers"]
    if issue == "missing_cdm":
        tool["cdm_db"] = "missing_cdm"
    elif issue == "wrong_cdm_kind":
        stack.databases["cdm_db"] = GenericDatabaseConfig(
            connection="cdm_main",
            schema_name="main",
        )
    elif issue == "missing_model":
        tool["embedding_model_name"] = "missing_model"
    elif issue == "missing_vector_store":
        tool["vector_store_name"] = "missing_vector_store"
    else:  # pragma: no cover - protected by the Literal annotation
        raise ValueError(f"Unknown invalid-reference fixture: {issue}")
    return stack

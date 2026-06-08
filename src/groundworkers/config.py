from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


class OmopGraphConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    db_url: str
    vocab_schema: str = "omop_vocab"
    emb_model_name: str | None = None
    # Minimum proportion of query tokens that must appear in the matched concept
    # name for a fulltext (FTS) result to be accepted.  FTS results below this
    # threshold are silently dropped; if *all* FTS results are dropped the tier
    # is treated as empty and grounding falls through to the embedding tier.
    # Range [0.0, 1.0]; 0.0 disables the filter (legacy behaviour).
    # A value of 0.5 means at least half the query words must be present.
    min_fulltext_overlap: float = 0.0

    @field_validator("min_fulltext_overlap")
    @classmethod
    def validate_overlap(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("min_fulltext_overlap must be between 0.0 and 1.0")
        return v

    @field_validator("vocab_schema")
    @classmethod
    def validate_schema_name(cls, value: str) -> str:
        if SCHEMA_NAME_PATTERN.fullmatch(value):
            return value
        raise ValueError("schema names must contain only letters, numbers, and underscores")


class OmopEmbConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    db_url: str | None = None
    backend_type: str = "sqlitevec"
    db_path: str | None = None
    default_model_name: str | None = None
    faiss_cache_dir: str | None = None
    api_base: str | None = None
    api_key: str | None = None

    @model_validator(mode="after")
    def validate_enabled_backend(self) -> OmopEmbConfig:
        if not self.enabled:
            return self
        if not self.db_url and not self.db_path:
            raise ValueError("enabled omop_emb config requires db_url or db_path")
        if self.api_base and not self.api_key:
            raise ValueError("omop_emb.api_key is required when api_base is configured")
        return self

    @property
    def configured_api_credentials(self) -> tuple[str, str] | None:
        if self.api_base is None:
            return None
        if self.api_key is None:
            raise ValueError("omop_emb.api_key is required when api_base is configured")
        return self.api_base, self.api_key

    @property
    def required_db_url(self) -> str:
        if self.db_url is None:
            raise ValueError("omop_emb.db_url is required for this configuration")
        return self.db_url

    @property
    def required_db_path(self) -> str:
        if self.db_path is None:
            raise ValueError("omop_emb.db_path is required for this configuration")
        return self.db_path


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "openai-compatible"
    api_base: str | None = None
    api_key: str | None = None
    default_model_name: str | None = None

    @model_validator(mode="after")
    def validate_enabled_config(self) -> "LLMConfig":
        if not self.enabled:
            return self
        if self.api_key is not None and not self.api_key.strip():
             raise ValueError("llm.api_key must be a non-empty string when provided")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: str = "groundworkers"
    omop_graph: OmopGraphConfig | None = None
    omop_emb: OmopEmbConfig | None = None
    llm: LLMConfig | None = None

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def describe(self) -> dict[str, Any]:
        def _mask(d: dict[str, Any]) -> dict[str, Any]:
            if d.get("api_key"):
                d = {**d, "api_key": "***"}
            return d

        return {
            "app_name": self.app_name,
            "omop_graph": self.omop_graph.model_dump() if self.omop_graph else None,
            "omop_emb": _mask(self.omop_emb.model_dump()) if self.omop_emb else None,
            "llm": _mask(self.llm.model_dump()) if self.llm else None,
        }

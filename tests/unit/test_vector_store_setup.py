from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from groundskeeping.configurator import ConfigWizardController, MutationOperation
from oa_configurator import ConnectionConfig, load_stack_config
from oa_configurator.io import save_stack_config

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.configuration_provider import (
    STORE_LOCATION_CDM,
    STORE_LOCATION_SEPARATE,
    GroundworkersConfigMutationService,
    vector_store_setup_workflow,
)
from groundworkers.application.setup.databases import resolve_database_targets
from groundworkers.config import GroundworkersConfig
from groundworkers.tui.presenters.database import (
    EMBEDDING_TARGET_KEY,
    DatabasePresenter,
)
from tests.support.stack_config import build_cdm_stack


def _stack_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), path)
    return path


def _drive(path: Path, *, location: str = STORE_LOCATION_CDM, **overrides: object):
    service = GroundworkersConfigMutationService(config_path=path)
    controller = ConfigWizardController(
        vector_store_setup_workflow(MutationOperation.CREATE), service
    )
    controller.start()
    controller.submit({"store_location": location})
    controller.submit(
        {
            "store_entry_name": overrides.get("store_entry_name", "embeddings"),
            "backend_type": overrides.get("backend_type", "sqlitevec"),
            "faiss_cache_dir": overrides.get("faiss_cache_dir"),
        }
    )
    if location == STORE_LOCATION_SEPARATE:
        controller.submit(
            {
                "store_database_name": overrides.get(
                    "store_database_name", "embedding_db"
                ),
                "store_connection_name": overrides.get(
                    "store_connection_name", "cdm_main"
                ),
                "store_schema_name": overrides.get("store_schema_name"),
            }
        )
    return controller


def test_the_journey_creates_the_store_its_database_and_the_reference(
    tmp_path: Path,
) -> None:
    """The embedding tier needs vector_store_name as well as embedding_model_name,
    and nothing in the console wrote it before."""
    path = _stack_path(tmp_path)

    assert _drive(path, location=STORE_LOCATION_SEPARATE).apply().status.value == (
        "applied"
    )

    stack = load_stack_config(path)
    config = GroundworkersConfig.validate_candidate(stack)
    assert config.vector_store_name == "embeddings"
    assert stack.vector_stores["embeddings"].database == "embedding_db"
    assert stack.databases["embedding_db"].kind == "generic"
    saved = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "schema_name" not in saved["databases"]["embedding_db"]


def test_the_default_location_puts_the_vectors_in_the_cdm_database(
    tmp_path: Path,
) -> None:
    """The rest of the tier already reads the CDM, so accepting the defaults
    should not scatter the vectors somewhere else."""
    path = _stack_path(tmp_path)

    assert _drive(path).apply().status.value == "applied"

    stack = load_stack_config(path)
    derived = stack.databases["cdm_db_vectors"]
    cdm = stack.databases["cdm_db"]
    assert stack.vector_stores["embeddings"].database == "cdm_db_vectors"
    assert derived.kind == "generic"
    assert derived.connection == cdm.connection
    assert derived.schema_name == cdm.cdm_schema
    assert set(stack.connections) == {"cdm_main"}


@pytest.mark.parametrize(
    ("dialect", "backend", "cdm_schema", "vocab_schema", "store_schema"),
    (
        ("sqlite", "sqlitevec", None, None, None),
        ("postgresql", "pgvector", "clinical", "vocabulary", "groundworkers"),
    ),
)
def test_cdm_store_location_round_trips_when_the_wizard_reopens(
    tmp_path: Path,
    dialect: str,
    backend: str,
    cdm_schema: str | None,
    vocab_schema: str | None,
    store_schema: str | None,
) -> None:
    path = tmp_path / f"{dialect}.toml"
    save_stack_config(
        build_cdm_stack(
            sqlite_path=str(tmp_path / "cdm.db"),
            cdm_schema=cdm_schema,
            vocab_schema=vocab_schema,
        ),
        path,
    )

    assert _drive(path, backend_type=backend).apply().status.value == "applied"

    stack = load_stack_config(path)
    store_database = stack.databases["cdm_db_vectors"]
    assert store_database.schema_name == store_schema
    reopened = ConfigWizardController(
        vector_store_setup_workflow(MutationOperation.UPDATE),
        GroundworkersConfigMutationService(config_path=path),
    )
    snapshot = reopened.start()
    location = next(
        field for field in snapshot.step.fields if field.key == "store_location"
    )
    assert location.default == STORE_LOCATION_CDM


@pytest.mark.parametrize("same_connection", [False, True])
def test_separate_store_location_round_trips_on_either_connection(
    tmp_path: Path, same_connection: bool
) -> None:
    path = tmp_path / "config.toml"
    stack = build_cdm_stack(cdm_schema="clinical", vocab_schema="vocabulary")
    if not same_connection:
        stack.connections["vectors"] = ConnectionConfig(
            dialect="postgresql+psycopg",
            host="vectors.example.test",
            port=5432,
            user="groundworkers",
            database_name="vectors",
        )
    save_stack_config(stack, path)
    database_name = "cdm_db_vectors" if same_connection else "separate_vectors"
    connection_name = "cdm_main" if same_connection else "vectors"

    assert (
        _drive(
            path,
            location=STORE_LOCATION_SEPARATE,
            backend_type="pgvector",
            store_database_name=database_name,
            store_connection_name=connection_name,
            store_schema_name="vectors",
        )
        .apply()
        .status.value
        == "applied"
    )

    reopened = ConfigWizardController(
        vector_store_setup_workflow(MutationOperation.UPDATE),
        GroundworkersConfigMutationService(config_path=path),
    )
    location = next(
        field for field in reopened.start().step.fields if field.key == "store_location"
    )
    assert location.default == STORE_LOCATION_SEPARATE
    saved = load_stack_config(path)
    store_database = saved.databases[database_name]
    assert store_database.connection == connection_name
    assert store_database.schema_name == "vectors"


def test_the_cdm_location_needs_no_database_answers(tmp_path: Path) -> None:
    """The database step is the whole cost of the opt-in, so it must not be asked
    of everyone."""
    path = _stack_path(tmp_path)
    service = GroundworkersConfigMutationService(config_path=path)
    controller = ConfigWizardController(
        vector_store_setup_workflow(MutationOperation.CREATE), service
    )
    controller.start()

    controller.submit({"store_location": STORE_LOCATION_CDM})
    transition = controller.submit(
        {
            "store_entry_name": "embeddings",
            "backend_type": "sqlitevec",
            "faiss_cache_dir": None,
        }
    )

    assert transition.issues == ()
    assert transition.snapshot.can_apply is True


def test_the_backend_choice_is_carried_through(tmp_path: Path) -> None:
    path = _stack_path(tmp_path)

    _drive(path, location=STORE_LOCATION_SEPARATE, backend_type="pgvector").apply()

    stack = load_stack_config(path)
    assert stack.vector_stores["embeddings"].backend_type == "pgvector"


@pytest.mark.parametrize("alias", ["relative", "tilde", "symlink", "hardlink", "separate"])
def test_duckdb_store_setup_compares_physical_files(
    tmp_path: Path, monkeypatch, alias
) -> None:
    cdm_path = tmp_path / "cdm.duckdb"
    cdm_path.touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    if alias in {"symlink", "hardlink"}:
        linked = tmp_path / "alias.duckdb"
        if alias == "hardlink":
            linked.hardlink_to(cdm_path)
        else:
            linked.symlink_to(cdm_path)
        store_path = str(linked)
    else:
        store_path = {
            "relative": "./cdm.duckdb",
            "tilde": "~/cdm.duckdb",
            "separate": str(tmp_path / "emb.duckdb"),
        }[alias]
    if alias == "separate":
        Path(store_path).touch()
    stack = build_cdm_stack()
    stack.connections["cdm_main"] = ConnectionConfig(
        dialect="duckdb", database_name=str(cdm_path)
    )
    stack.connections["emb"] = ConnectionConfig(
        dialect="duckdb", database_name=store_path
    )
    config_path = tmp_path / "config.toml"
    save_stack_config(stack, config_path)
    controller = ConfigWizardController(
        vector_store_setup_workflow(MutationOperation.CREATE),
        GroundworkersConfigMutationService(config_path),
    )
    controller.start()
    assert not controller.submit({"store_location": STORE_LOCATION_SEPARATE}).issues
    assert not controller.submit(
        {"store_entry_name": "vectors", "backend_type": "duckdbvss"}
    ).issues
    result = controller.submit(
        {"store_database_name": "emb", "store_connection_name": "emb"}
    )
    if alias == "separate":
        assert not result.issues
        assert result.snapshot.can_apply
    else:
        assert [issue.field_key for issue in result.issues] == ["store_connection_name"]
        assert (
            "absolute" if alias in {"relative", "tilde"} else "separate from the CDM"
        ) in result.issues[0].message
        assert not result.snapshot.can_apply


@pytest.mark.parametrize(
    "target", ["missing_parent", "directory", "memory", "home_alias"]
)
def test_duckdb_store_setup_rejects_invalid_file_targets(
    tmp_path: Path, monkeypatch, target
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    stack = build_cdm_stack()
    cdm = tmp_path / "cdm.duckdb"
    cdm.touch()
    embedding = tmp_path / "emb.duckdb"
    if target == "missing_parent":
        embedding = tmp_path / "missing" / "emb.duckdb"
    elif target == "directory":
        embedding.mkdir()
    elif target == "home_alias":
        embedding.touch()
    stack.connections["cdm_main"] = ConnectionConfig(
        dialect="duckdb", database_name=str(cdm)
    )
    stack.connections["emb"] = ConnectionConfig(
        dialect="duckdb",
        database_name=":memory:"
        if target == "memory"
        else "~/emb.duckdb"
        if target == "home_alias"
        else str(embedding),
    )
    path = tmp_path / "config.toml"
    save_stack_config(stack, path)
    before = path.read_bytes()
    controller = ConfigWizardController(
        vector_store_setup_workflow(MutationOperation.CREATE),
        GroundworkersConfigMutationService(path),
    )
    controller.start()
    assert not controller.submit({"store_location": STORE_LOCATION_SEPARATE}).issues
    assert not controller.submit(
        {"store_entry_name": "vectors", "backend_type": "duckdbvss"}
    ).issues
    result = controller.submit(
        {"store_database_name": "emb", "store_connection_name": "emb"}
    )
    assert [issue.field_key for issue in result.issues] == ["store_connection_name"]
    assert (
        "absolute" if target in {"memory", "home_alias"} else "existing"
    ) in result.issues[0].message
    assert not result.snapshot.can_apply
    assert path.read_bytes() == before
    assert target in {"directory", "home_alias"} or not embedding.exists()


def test_a_backend_the_cdm_cannot_serve_is_refused(tmp_path: Path) -> None:
    """pgvector against a SQLite CDM would only fail later, in omop-emb."""
    path = _stack_path(tmp_path)
    service = GroundworkersConfigMutationService(config_path=path)
    controller = ConfigWizardController(
        vector_store_setup_workflow(MutationOperation.CREATE), service
    )
    controller.start()
    controller.submit({"store_location": STORE_LOCATION_CDM})

    result = controller.submit(
        {
            "store_entry_name": "embeddings",
            "backend_type": "pgvector",
            "faiss_cache_dir": None,
        }
    )

    assert [issue.field_key for issue in result.issues] == ["backend_type"]


def test_the_schema_default_follows_the_cdm_rather_than_public(
    tmp_path: Path,
) -> None:
    """'public' is a PostgreSQL name, and it was offered whatever the dialect and
    whatever the CDM's own schema was."""
    path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(cdm_schema="omop", vocab_schema="omop"), path)

    _drive(path, location=STORE_LOCATION_SEPARATE).apply()

    stack = load_stack_config(path)
    store_name = GroundworkersConfig.validate_candidate(stack).vector_store_name
    assert store_name is not None
    store_database = stack.vector_stores[store_name].database
    assert stack.databases[store_database].schema_name == "groundworkers"


def test_a_blank_faiss_cache_is_left_unset(tmp_path: Path) -> None:
    """Optional, and meaningless without the embedding-faiss extra."""
    path = _stack_path(tmp_path)

    _drive(path, faiss_cache_dir="").apply()

    stack = load_stack_config(path)
    assert stack.vector_stores["embeddings"].faiss_cache_dir is None


def test_the_store_reuses_a_named_connection_rather_than_defining_one(
    tmp_path: Path,
) -> None:
    """Creating connections is the CDM journey's job; this one references."""
    path = _stack_path(tmp_path)

    _drive(path, location=STORE_LOCATION_SEPARATE).apply()

    stack = load_stack_config(path)
    assert set(stack.connections) == {"cdm_main"}
    assert stack.databases["embedding_db"].connection == "cdm_main"


def test_the_configured_store_then_appears_as_a_database_target(
    tmp_path: Path,
) -> None:
    """Once configured it is a real connection target, checked like the others."""
    path = _stack_path(tmp_path)
    _drive(path).apply()

    targets = resolve_database_targets(load_configuration(config_path=path))

    assert EMBEDDING_TARGET_KEY in {target.key for target in targets}


def test_an_unconfigured_store_still_shows_a_row(tmp_path: Path) -> None:
    """Otherwise there is nothing to select, and so no way to configure one."""
    snapshot = load_configuration(config_path=_stack_path(tmp_path))

    view = DatabasePresenter().landing(snapshot, resolve_database_targets(snapshot), ())

    row = next(row for row in view.rows if row.key == EMBEDDING_TARGET_KEY)
    assert "Not configured" in row.cells


def test_duckdb_store_configuration_accepts_a_new_file_without_creating_it(tmp_path):
    stack = build_cdm_stack()
    cdm = tmp_path / "cdm.duckdb"
    cdm.touch()
    embedding = tmp_path / "new-emb.duckdb"
    stack.connections["cdm_main"] = ConnectionConfig(
        dialect="duckdb", database_name=str(cdm)
    )
    stack.connections["emb"] = ConnectionConfig(
        dialect="duckdb", database_name=str(embedding)
    )
    path = tmp_path / "config.toml"
    save_stack_config(stack, path)
    controller = _drive(
        path,
        location=STORE_LOCATION_SEPARATE,
        backend_type="duckdbvss",
        store_connection_name="emb",
    )
    assert controller.review().snapshot.can_apply
    assert controller.apply().status.value == "applied"
    assert not embedding.exists()

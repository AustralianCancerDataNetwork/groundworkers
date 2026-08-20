"""Groundworkers: read-only agentive access to OMOP vocabularies and concepts."""

from groundworkers._env import load_environment

# Before anything else in the package, and before any import of oa_configurator:
# `oa_configurator.loader` reads OA_CONFIG_PATH once, at its own import, so this
# is the only point at which a recorded config location can still take effect.
# Import-time side effect, which a library should not have -- Groundworkers is an
# application, and the alternative is a server that silently reads a different
# config from the console that configured it.
load_environment()

__all__ = ["__version__"]

__version__ = "0.1.0"

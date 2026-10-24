"""Prompt packs for the OHDSI Prompt Registry that ship with Groundworkers.

Each direct child directory is one registry pack, published through the
``ohdsi_prompt_registry.packs`` entry point. A pack lives here when Groundworkers
owns the contract it describes: ``groundcrew-source-column-classification``
reviews columns against Groundworkers' ``ColumnRole`` vocabulary. The name keeps
its ``groundcrew-`` prefix so existing prompt names and overrides stay stable.
"""

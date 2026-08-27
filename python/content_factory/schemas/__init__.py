"""Typed source of truth (section 9).

Pydantic models here author every cross-language contract. ``scripts/export_schemas.py`` emits
JSON Schema 2020-12 for each model in :data:`content_factory.schemas.registry.SCHEMA_REGISTRY`;
``packages/content-schema-ts`` turns those into TypeScript declarations and compiled Ajv
validators. CI fails on drift.
"""

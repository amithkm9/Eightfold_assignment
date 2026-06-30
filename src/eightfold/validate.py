"""Validate the PROJECTED output against the requested schema.

For a custom config we check each declared field's `required`/`type`. For the default
schema we re-validate against the canonical pydantic model. Validation runs on the
*output* (not just the canonical record) so the config's contract is actually enforced.
"""

from __future__ import annotations

from typing import Any

from .models import CanonicalProfile, OutputConfig
from .project import resolve_path


class SchemaValidationError(ValueError):
    pass


def _type_ok(value: Any, type_name: str | None) -> bool:
    if type_name is None or value is None:
        return True
    checks = {
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "string[]": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
    }
    check = checks.get(type_name)
    return check(value) if check else True


def validate_output(output: dict, config: OutputConfig) -> dict:
    """Raise SchemaValidationError on any contract violation; else return output."""
    if not config.fields:
        # Default schema: the canonical model is the contract.
        try:
            CanonicalProfile.model_validate({**output, **_drop_optional(output)})
        except Exception as exc:  # noqa: BLE001
            raise SchemaValidationError(f"default schema validation failed: {exc}") from exc
        return output

    for spec in config.fields:
        # resolve_path returns None for both "missing" and "null"; for a required field
        # either is a violation, so this single check covers both.
        value = resolve_path(output, spec.path)
        if spec.required and value is None:
            raise SchemaValidationError(f"required field '{spec.path}' is missing or null")
        if not _type_ok(value, spec.type):
            raise SchemaValidationError(
                f"field '{spec.path}' expected {spec.type}, got {type(value).__name__}")
    return output


def _drop_optional(output: dict) -> dict:
    # Confidence/provenance may be toggled off; supply defaults so the model still
    # validates structurally without inventing data.
    patch: dict[str, Any] = {}
    if "provenance" not in output:
        patch["provenance"] = []
    if "overall_confidence" not in output:
        patch["overall_confidence"] = 0.0
    return patch

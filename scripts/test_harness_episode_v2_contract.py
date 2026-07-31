#!/usr/bin/env python3
"""Standard-library fixtures for the inactive harness episode v2 contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/darkexec-harness-episode.v2.schema.json").read_text())
FIXTURES = ROOT / "schemas/fixtures/darkexec-harness-episode.v2"


def resolve(schema: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not reference:
        return schema
    assert reference.startswith("#/$defs/")
    return SCHEMA["$defs"][reference.rsplit("/", 1)[1]]


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    schema = resolve(schema)
    if "const" in schema:
        assert value == schema["const"], f"{path}: const"
    if "enum" in schema:
        assert value in schema["enum"], f"{path}: enum"
    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        checks = {
            "null": value is None,
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
        }
        assert any(checks[kind] for kind in types), f"{path}: type"
    if value is None:
        return
    if isinstance(value, str):
        if "minLength" in schema:
            assert len(value) >= schema["minLength"], f"{path}: minLength"
        if "pattern" in schema:
            assert re.fullmatch(schema["pattern"], value), f"{path}: pattern"
    if isinstance(value, int) and "minimum" in schema:
        assert value >= schema["minimum"], f"{path}: minimum"
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - set(value)
        assert not missing, f"{path}: missing {sorted(missing)}"
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            assert not extra, f"{path}: extra {sorted(extra)}"
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], f"{path}.{key}")


def main() -> None:
    complete = json.loads((FIXTURES / "complete.json").read_text())
    unknown = json.loads((FIXTURES / "unknown.json").read_text())
    validate(complete, SCHEMA)
    validate(unknown, SCHEMA)
    assert unknown["measurements"]["target"]["startedAt"] is None
    assert unknown["measurements"]["harness"]["modelCallCount"] is None
    assert unknown["measurements"]["operatorInteractions"]["correctionCount"] is None
    assert unknown["identities"]["exposure"]["state"] == "unknown"

    leakage = json.loads((FIXTURES / "raw-leakage.invalid.json").read_text())
    try:
        validate(leakage, SCHEMA)
        raise AssertionError("raw leakage fixture unexpectedly passed")
    except AssertionError as exc:
        assert "extra" in str(exc) or "missing" in str(exc)
    for forbidden in ("prompt", "trajectory", "resultText"):
        assert forbidden not in SCHEMA["properties"]

    emitter = (ROOT / "bin/darkexec").read_text()
    assert '"schema": "darkexec.harness-episode/v1"' in emitter
    assert '"schema": "darkexec.harness-episode/v2"' not in emitter
    print("inactive harness episode v2 contract fixtures passed")


if __name__ == "__main__":
    main()

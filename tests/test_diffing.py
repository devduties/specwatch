import json
from pathlib import Path

from specwatch.diffing import compare_specs
from specwatch.specs import parse_spec

ROOT = Path(__file__).parents[1]


def load_example(name: str) -> dict:
    return parse_spec((ROOT / "examples" / name).read_bytes()).document


def test_example_pair_reports_breaking_and_additive_changes() -> None:
    findings = compare_specs(load_example("before.json"), load_example("after.json"))
    codes = {item.code for item in findings}
    assert {
        "operation.removed",
        "parameter.required_added",
        "response.status_removed",
        "schema.property_removed",
        "operation.added",
    } <= codes
    assert any(item.severity == "breaking" for item in findings)
    assert any(item.severity == "info" for item in findings)


def test_additive_optional_response_field_is_not_breaking() -> None:
    before = {
        "openapi": "3.1.0",
        "paths": {
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    }
    after = json.loads(json.dumps(before))
    after["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "properties"
    ]["label"] = {"type": "string"}
    findings = compare_specs(before, after)
    assert findings == []


def test_local_refs_are_compared_and_recursive_refs_terminate() -> None:
    before = {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "next": {"$ref": "#/components/schemas/Node"},
                    },
                }
            }
        },
        "paths": {
            "/nodes": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Node"}
                                }
                            },
                        }
                    }
                }
            }
        },
    }
    after = json.loads(json.dumps(before))
    del after["components"]["schemas"]["Node"]["properties"]["id"]
    findings = compare_specs(before, after)
    assert any(item.path.endswith(".id") for item in findings)

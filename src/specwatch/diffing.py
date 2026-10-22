"""Compatibility checks for common OpenAPI changes.

This is a deliberately explainable subset of OpenAPI compatibility analysis,
not a formal proof that every consumer remains compatible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _operations(
    document: dict[str, Any],
) -> dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]]:
    operations: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    for path, path_item in document.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS and isinstance(operation, dict):
                operations[(method.lower(), path)] = (path_item, operation)
    return operations


def _resolve_local_ref(document: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict) or not isinstance(value.get("$ref"), str):
        return value
    ref = value["$ref"]
    if not ref.startswith("#/"):
        return value
    current: Any = document
    try:
        for token in ref[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            current = current[token]
    except (KeyError, TypeError):
        return value
    if isinstance(current, dict):
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        return {**current, **siblings}
    return current


def _schema_properties(document: dict[str, Any], schema: Any) -> dict[str, Any]:
    schema = _resolve_local_ref(document, schema)
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties", {})
    return properties if isinstance(properties, dict) else {}


def _parameters(
    path_item: dict[str, Any], operation: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    combined: dict[tuple[str, str], dict[str, Any]] = {}
    for parameter in [
        *(path_item.get("parameters", []) or []),
        *(operation.get("parameters", []) or []),
    ]:
        if not isinstance(parameter, dict):
            continue
        name, location = parameter.get("name"), parameter.get("in")
        if isinstance(name, str) and isinstance(location, str):
            combined[(location, name)] = parameter
    return combined


def _request_schema(document: dict[str, Any], operation: dict[str, Any]) -> Any:
    request_body = _resolve_local_ref(document, operation.get("requestBody"))
    if not isinstance(request_body, dict):
        return None
    content = request_body.get("content", {})
    if not isinstance(content, dict):
        return None
    media = content.get("application/json") or next(iter(content.values()), {})
    return media.get("schema") if isinstance(media, dict) else None


def _response_schemas(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return {}
    schemas: dict[str, Any] = {}
    for status, raw_response in responses.items():
        response = _resolve_local_ref(document, raw_response)
        if not isinstance(response, dict):
            continue
        content = response.get("content", {})
        if not isinstance(content, dict):
            continue
        media = content.get("application/json") or next(iter(content.values()), {})
        if isinstance(media, dict) and "schema" in media:
            schemas[str(status)] = media["schema"]
    return schemas


def _compare_schema(
    old_document: dict[str, Any],
    new_document: dict[str, Any],
    old_schema: Any,
    new_schema: Any,
    location: str,
    *,
    removed_property_severity: str,
    depth: int = 0,
) -> list[Finding]:
    if depth >= 12:
        return []
    old_resolved = _resolve_local_ref(old_document, old_schema)
    new_resolved = _resolve_local_ref(new_document, new_schema)
    if not isinstance(old_resolved, dict) or not isinstance(new_resolved, dict):
        return []

    findings: list[Finding] = []
    old_type, new_type = old_resolved.get("type"), new_resolved.get("type")
    if old_type is not None and new_type is not None and old_type != new_type:
        findings.append(
            Finding(
                "breaking",
                "schema.type_changed",
                location,
                f"Schema type changed from {old_type} to {new_type}.",
            )
        )

    old_enum, new_enum = old_resolved.get("enum"), new_resolved.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed = [item for item in old_enum if item not in new_enum]
        if removed:
            findings.append(
                Finding(
                    "breaking",
                    "schema.enum_values_removed",
                    location,
                    f"Enum values were removed: {', '.join(map(str, removed))}.",
                )
            )

    old_properties = _schema_properties(old_document, old_resolved)
    new_properties = _schema_properties(new_document, new_resolved)
    old_required = set(old_resolved.get("required", []))
    new_required = set(new_resolved.get("required", []))
    for name in sorted(new_required - old_required):
        if name in new_properties:
            findings.append(
                Finding(
                    "breaking",
                    "schema.required_property_added",
                    f"{location}.{name}",
                    f"Property '{name}' became required.",
                )
            )
    for name in sorted(old_properties.keys() - new_properties.keys()):
        findings.append(
            Finding(
                removed_property_severity,
                "schema.property_removed",
                f"{location}.{name}",
                f"Property '{name}' was removed.",
            )
        )
    for name in sorted(old_properties.keys() & new_properties.keys()):
        findings.extend(
            _compare_schema(
                old_document,
                new_document,
                old_properties[name],
                new_properties[name],
                f"{location}.{name}",
                removed_property_severity=removed_property_severity,
                depth=depth + 1,
            )
        )
    return findings


def compare_specs(old_document: dict[str, Any], new_document: dict[str, Any]) -> list[Finding]:
    """Return findings for common breaking and additive changes."""
    findings: list[Finding] = []
    old_ops, new_ops = _operations(old_document), _operations(new_document)

    for method, path in sorted(old_ops.keys() - new_ops.keys()):
        findings.append(
            Finding(
                "breaking",
                "operation.removed",
                f"{method.upper()} {path}",
                "Operation was removed.",
            )
        )
    for method, path in sorted(new_ops.keys() - old_ops.keys()):
        findings.append(
            Finding("info", "operation.added", f"{method.upper()} {path}", "Operation was added.")
        )

    for method, path in sorted(old_ops.keys() & new_ops.keys()):
        old_path_item, old_op = old_ops[(method, path)]
        new_path_item, new_op = new_ops[(method, path)]
        operation_path = f"{method.upper()} {path}"

        old_parameters = _parameters(old_path_item, old_op)
        new_parameters = _parameters(new_path_item, new_op)
        for key in sorted(new_parameters.keys() - old_parameters.keys()):
            parameter = new_parameters[key]
            if parameter.get("required") is True:
                findings.append(
                    Finding(
                        "breaking",
                        "parameter.required_added",
                        operation_path,
                        f"Required {key[0]} parameter '{key[1]}' was added.",
                    )
                )

        old_body = _resolve_local_ref(old_document, old_op.get("requestBody"))
        new_body = _resolve_local_ref(new_document, new_op.get("requestBody"))
        if (
            isinstance(new_body, dict)
            and new_body.get("required") is True
            and not (isinstance(old_body, dict) and old_body.get("required") is True)
        ):
            findings.append(
                Finding(
                    "breaking",
                    "request_body.required_added",
                    operation_path,
                    "The request body became required.",
                )
            )

        old_request, new_request = (
            _request_schema(old_document, old_op),
            _request_schema(new_document, new_op),
        )
        if old_request is not None and new_request is not None:
            findings.extend(
                _compare_schema(
                    old_document,
                    new_document,
                    old_request,
                    new_request,
                    f"{operation_path} request",
                    removed_property_severity="breaking",
                )
            )

        old_responses, new_responses = old_op.get("responses", {}), new_op.get("responses", {})
        if isinstance(old_responses, dict) and isinstance(new_responses, dict):
            for status in sorted(old_responses.keys() - new_responses.keys(), key=str):
                findings.append(
                    Finding(
                        "breaking",
                        "response.status_removed",
                        operation_path,
                        f"Response '{status}' was removed.",
                    )
                )
        old_response_schemas = _response_schemas(old_document, old_op)
        new_response_schemas = _response_schemas(new_document, new_op)
        for status in sorted(old_response_schemas.keys() & new_response_schemas.keys()):
            findings.extend(
                _compare_schema(
                    old_document,
                    new_document,
                    old_response_schemas[status],
                    new_response_schemas[status],
                    f"{operation_path} response {status}",
                    removed_property_severity="breaking",
                )
            )

    return findings

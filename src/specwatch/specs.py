"""Parse and validate the useful parts of OpenAPI documents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import yaml

from specwatch.errors import InvalidSpecDocument


@dataclass(frozen=True, slots=True)
class ParsedSpec:
    title: str
    version: str
    document: dict[str, Any]


def parse_spec(content: bytes, content_type: str = "") -> ParsedSpec:
    """Decode JSON or YAML and validate the minimum OpenAPI document shape."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidSpecDocument("The specification must be UTF-8 encoded.") from exc

    try:
        if "json" in content_type.lower():
            document = json.loads(text)
        else:
            try:
                document = json.loads(text)
            except json.JSONDecodeError:
                document = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise InvalidSpecDocument("The response is not valid JSON or YAML.") from exc

    if not isinstance(document, dict):
        raise InvalidSpecDocument("The OpenAPI document must be a JSON/YAML object.")

    version = document.get("openapi")
    if not isinstance(version, str):
        raise InvalidSpecDocument("Only OpenAPI 3.x documents are supported.")
    if not version.startswith("3."):
        raise InvalidSpecDocument(f"OpenAPI {version} is not supported; use OpenAPI 3.x.")

    info = document.get("info")
    paths = document.get("paths")
    if not isinstance(info, dict) or not isinstance(info.get("title"), str):
        raise InvalidSpecDocument("The document must include info.title.")
    if not isinstance(paths, dict):
        raise InvalidSpecDocument("The document must include a paths object.")

    return ParsedSpec(
        title=info["title"],
        version=str(info.get("version", "unknown")),
        document=document,
    )

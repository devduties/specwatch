import pytest

from specwatch.errors import InvalidSpecDocument
from specwatch.specs import parse_spec


def test_parse_json_openapi_document() -> None:
    parsed = parse_spec(
        b'{"openapi":"3.1.0","info":{"title":"Demo","version":"1"},"paths":{}}',
        "application/json",
    )
    assert parsed.title == "Demo"
    assert parsed.version == "1"


def test_parse_yaml_openapi_document() -> None:
    parsed = parse_spec(
        b"openapi: 3.0.3\ninfo:\n  title: Demo\n  version: '2'\npaths: {}\n",
        "application/yaml",
    )
    assert parsed.title == "Demo"
    assert parsed.version == "2"


@pytest.mark.parametrize(
    "content, message",
    [
        (b"[]", "object"),
        (b'{"swagger":"2.0","info":{"title":"old"},"paths":{}}', "3.x"),
        (b'{"openapi":"3.1.0","info":{},"paths":{}}', "info.title"),
        (b"not: [valid", "valid JSON or YAML"),
    ],
)
def test_reject_invalid_documents(content: bytes, message: str) -> None:
    with pytest.raises(InvalidSpecDocument, match=message):
        parse_spec(content)

import pytest

from specwatch.errors import InvalidSpecURL
from specwatch.fetcher import validate_url_shape


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/openapi.json",
        "file:///etc/passwd",
        "https://user:secret@api.example.com/openapi.json",
        "https://api.example.com:8443/openapi.json",
        "https://api.example.com/openapi.json#section",
    ],
)
def test_reject_unsafe_or_unsupported_url_shapes(url: str) -> None:
    with pytest.raises(InvalidSpecURL):
        validate_url_shape(url)


def test_accept_standard_https_url() -> None:
    assert validate_url_shape("https://api.example.com/openapi.json") == "api.example.com"

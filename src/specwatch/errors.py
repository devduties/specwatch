"""Expected domain errors surfaced by the API."""


class SpecWatchError(Exception):
    """Base class for expected application errors."""


class InvalidSpecURL(SpecWatchError):
    """The URL is unsafe or unsupported."""


class SpecFetchError(SpecWatchError):
    """A remote specification could not be retrieved."""


class InvalidSpecDocument(SpecWatchError):
    """The response is not a supported OpenAPI document."""

"""Tool implementations, grouped by resource per spec/02-tools.md."""

from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable, TypeVar

from ..redaction import redact

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def redacted(fn: F) -> F:
    """Apply the generic credential-redaction pass to a tool's return value.

    Stacked on every tool (see spec/03-conventions.md "Security / redaction")
    rather than special-cased per resource type, since the confirmed live
    finding was that project documents (not just connections) can carry
    embedded live credentials.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        result = await fn(*args, **kwargs)
        return redact(result)

    return wrapper  # type: ignore[return-value]

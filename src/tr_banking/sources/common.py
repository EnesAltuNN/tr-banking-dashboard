"""Helpers shared by source clients: HTTP retries and raw response archiving."""

import logging
import re
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
REDACTED = "***REDACTED***"


class SourceApiError(RuntimeError):
    """A source could not be reached or answered with an error status."""


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    label: str,
    error_cls: type[SourceApiError] = SourceApiError,
    max_retries: int = 2,
    retry_wait: float = 2.0,
    forbidden_hint: str = "",
    redact: Sequence[str] = (),
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying network errors and transient statuses; fail fast on the rest.

    Retrying a 403 or 400 would not help, so those raise immediately with a clear message.
    """
    attempts = max_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            problem = f"network error: {exc!r}"
        else:
            if response.status_code == httpx.codes.OK:
                return response
            if response.status_code not in TRANSIENT_STATUS_CODES:
                message = _describe_error(response, label, forbidden_hint)
                raise error_cls(redact_text(message, redact))
            problem = f"HTTP {response.status_code}"
        if attempt < attempts:
            logger.warning("%s: attempt %d/%d failed (%s)", label, attempt, attempts, problem)
            time.sleep(retry_wait * attempt)
    raise error_cls(f"{label} request failed after {attempts} attempts: {problem}")


def save_raw_response(
    directory: Path, content: bytes, name: str, *, redact: Sequence[str] = ()
) -> Path:
    """Write a response body to <directory>/<UTC timestamp>_<name>.json for debugging.

    Only the body is stored: never request headers, URLs with credentials or connection info.
    CI uploads this directory as a public artifact, so known secret values are masked too, in
    case a server ever echoes one back.
    """
    for secret in redact:
        if secret:
            content = content.replace(secret.encode(), REDACTED.encode())
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    # Series codes may contain characters Windows does not allow in file names (e.g. ':').
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
    path = directory / f"{stamp}_{safe_name}.json"
    path.write_bytes(content)
    logger.info("raw response saved to %s", path)
    return path


def redact_text(text: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def _describe_error(response: httpx.Response, label: str, forbidden_hint: str) -> str:
    if response.is_redirect:
        location = response.headers.get("location")
        return f"{label} redirected to {location!r}; the API URL may have changed"
    if response.status_code == httpx.codes.FORBIDDEN:
        return f"{label} returned 403 Forbidden{forbidden_hint}"
    return f"{label} returned HTTP {response.status_code}: {response.text[:200]}"

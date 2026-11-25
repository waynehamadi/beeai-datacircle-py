"""The calls to Datacircle's open API, https://api.datacircle.dev/v1 (its spec: https://api.datacircle.dev/v1/openapi.json):
one HTTPS GET each, no key, and what its answers are cut to for a model."""

import asyncio
import json
import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional

import httpx
from beeai_framework.tools import ToolError

BASE_URL = "https://api.datacircle.dev/v1"
VERSION = "0.1.0"
TIMEOUT = 30.0

# A plan holds about 90 keys (its terms of use, its aggregators...): these choose a plan, the rest is on its page.
PLAN_KEYS = ["api", "rank", "provider", "plan", "score", "perThousand", "notFoundPerThousand", "billing", "pay",
             "currency", "units", "unit", "strengths", "latencyMs", "page", "pricingPage", "docs"]
API_KEYS = ["api", "slug", "plans", "providers", "best", "bestPage", "sheetPage"]

Answer = Dict[str, Any]


class ApiError(Exception):
    """An answer of 4xx (an unknown slug, a wrong argument): the API's own words, given back as the tool's result."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


class DatacircleError(ToolError):
    """The API out of reach, busy or failing: a ToolError, which the agent gives the model, and which the tool's
    retry_options retry. status is the API's HTTP status, None when it gave none."""

    def __init__(self, message: str, status: Optional[int] = None, cause: Optional[BaseException] = None) -> None:
        super().__init__(message, cause=cause)
        self.status = status


def _words(status: int, body: bytes, reason: str) -> str:
    """A refusal's words: the API's error, or its status when it gave none."""
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if isinstance(parsed, Mapping) and isinstance(parsed.get("error"), str) and parsed["error"]:
        return parsed["error"]
    return f"{status} {reason}".strip()


async def get(path: str, params: Optional[Mapping[str, Any]] = None, base_url: Optional[str] = None) -> Any:
    """The API's JSON answer. A 4xx raises ApiError with the API's words, anything else that gives no answer a
    DatacircleError. A 429 is waited for once, a minute at most."""
    query = {key: str(value) for key, value in (params or {}).items() if value is not None and value != ""}
    url = (base_url or BASE_URL).rstrip("/") + path
    headers = {"Accept": "application/json", "User-Agent": f"beeai-datacircle/{VERSION}"}
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers) as client:
        for attempt in range(2):
            try:
                response = await client.get(url, params=query)
            except httpx.HTTPError as error:
                raise DatacircleError(f"Datacircle's API could not be reached: {str(error) or type(error).__name__}",
                                      cause=error) from None
            if response.status_code == 429 and attempt == 0:
                try:
                    wait = int(response.headers.get("Retry-After") or "")
                except ValueError:
                    wait = 60
                await asyncio.sleep(wait if 1 <= wait <= 60 else 60)
                continue
            if response.status_code == 429:
                raise DatacircleError("Datacircle's API answered 429 twice: wait a minute and call again", 429)
            if 400 <= response.status_code < 500:
                raise ApiError(response.status_code, _words(response.status_code, response.content,
                                                            response.reason_phrase))
            if response.status_code >= 300:
                raise DatacircleError(f"Datacircle's API answered {response.status_code} "
                                      f"{response.reason_phrase}".strip(), response.status_code)
            try:
                return response.json()
            except ValueError as error:
                raise DatacircleError(f"Datacircle's API gave no JSON: {error}", response.status_code) from None
    raise AssertionError("unreachable")


def objects(value: Any) -> List[Answer]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def brief(item: Mapping[str, Any], keys: List[str]) -> Answer:
    return {key: item[key] for key in keys if item.get(key) is not None}


def brief_plans(plans: Any) -> List[Answer]:
    return [brief(plan, PLAN_KEYS) for plan in objects(plans)]


def slugify(name: Any) -> str:
    """The API's rule for a slug: the name in lowercase letters and digits, the rest a hyphen."""
    plain = unicodedata.normalize("NFKD", str(name or ""))
    plain = "".join(char for char in plain if not unicodedata.combining(char)).lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")

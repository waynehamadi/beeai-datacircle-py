"""Offline tests: the tools against a stand-in for the API on 127.0.0.1, run by the framework's own Tool.run() and by the
plain functions."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from beeai_framework.tools import JSONToolOutput, ToolError, ToolInputValidationError, ToolRunOptions
from beeai_framework.tools.types import RetryOptions
from pydantic import ValidationError

from beeai_datacircle import (
    DatacircleBestPlansTool,
    DatacircleError,
    DatacircleListApisTool,
    DatacircleProviderTool,
    DatacircleSearchPlansTool,
    best_plans,
    datacircle_tools,
    list_apis,
    provider_ranks,
    search_plans,
)

PLAN = {"api": "Employee email", "rank": 1, "provider": "Hunter", "plan": "Starter", "score": 80.1, "perThousand": 4.9,
        "page": "https://datacircle.dev/x", "terms": "long", "aggregators": ["a"]}
APIS = [{"api": "Employee profile", "slug": "linkedin-profile", "plans": 3, "providers": 2, "extra": 1},
        {"api": "Employee email", "slug": "email-finder", "plans": 5, "providers": 4},
        {"api": "Company profile", "slug": "linkedin-company-data", "plans": 1, "providers": 1}]
SEEN = []
STATE = {"busy": 0, "down": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        SEEN.append((url.path, query, self.headers.get("User-Agent")))
        if STATE["busy"]:
            STATE["busy"] -= 1
            return self.reply(429, {"error": "too many calls"}, {"Retry-After": "1"})
        if STATE["down"]:
            STATE["down"] -= 1
            return self.reply(503, {"error": "down"})
        if url.path == "/v1/apis":
            return self.reply(200, APIS)
        if url.path == "/v1/apis/email-finder/best":
            if query.get("limit") == "99":
                return self.reply(400, {"error": "limit must be 1 or more and 50 or less"})
            return self.reply(200, {"api": "Employee email", "slug": "email-finder", "total": 5, "plans": [PLAN]})
        if url.path.startswith("/v1/apis/"):
            return self.reply(404, {"error": f"no API has the slug {url.path.split('/')[3]}", "slugs": ["email-finder"]})
        if url.path == "/v1/plans":
            api = query.get("api", "")
            if api and api not in ("email-finder", "Employee email"):
                return self.reply(400, {"error": "api must be one of: Employee email"})
            if query.get("query") == "bright-data":
                return self.reply(200, {"total": 0, "offset": 0, "plans": []})
            return self.reply(200, {"total": 1, "offset": int(query.get("offset", 0)), "plans": [PLAN]})
        if url.path == "/v1/providers/bright-data":
            return self.reply(200, {"provider": "Bright Data", "slug": "bright-data", "apis": [
                {"api": "Employee email", "slug": "email-finder", "bestPage": "p1", "plans": [dict(PLAN, rank=None)]},
                {"api": "Employee profile", "slug": "linkedin-profile", "rank": 9, "plans": [
                    dict(PLAN, rank=12, plan="Big"), dict(PLAN, rank=9, plan="Small")]}]})
        if url.path.startswith("/v1/providers/"):
            return self.reply(404, {"error": f"no provider has the slug {url.path.split('/')[3]}"})
        return self.reply(404, {"error": "not found"})


@pytest.fixture(scope="module")
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def run(tool, input, options=None):
    output = asyncio.run(_run(tool, input, options))
    assert isinstance(output, JSONToolOutput)
    assert json.loads(output.get_text_content()) == output.result
    return output.result


async def _run(tool, input, options):
    return await tool.run(input, options)


def test_the_four_tools(base):
    tools = datacircle_tools(base_url=base)
    assert [tool.name for tool in tools] == ["datacircle_list_apis", "datacircle_best_plans", "datacircle_search_plans",
                                             "datacircle_provider"]
    for tool in tools:
        schema = tool.input_schema.model_json_schema()
        assert schema["type"] == "object" and "https://datacircle.dev" in tool.description
        if hasattr(tool, "to_json_safe"):  # beeai-framework 0.1.31 and later
            assert tool.to_json_safe()["input_schema"] == schema


def test_list_apis(base):
    answer = run(DatacircleListApisTool(base_url=base), {"query": "profile", "limit": 1})
    assert answer == {"total": 2, "apis": [{"api": "Employee profile", "slug": "linkedin-profile", "plans": 3,
                                            "providers": 2}]}
    assert SEEN[-1][2].startswith("beeai-datacircle/")
    answer = run(DatacircleListApisTool(base_url=base), {"query": "Bright Data"})
    # "data" is a word of linkedin-company-data, but no API holds both: where a provider's plans are.
    assert answer["total"] == 1 and "datacircle_search_plans" in answer["note"]
    assert run(DatacircleListApisTool(base_url=base), {})["total"] == 3


def test_best_plans(base):
    answer = run(DatacircleBestPlansTool(base_url=base), {"slug": "Email Finder", "limit": "1", "mine": True})
    assert SEEN[-1][:2] == ("/v1/apis/email-finder/best", {"limit": "1"})
    assert answer["plans"] == [{key: value for key, value in PLAN.items() if key not in ("terms", "aggregators")}]
    answer = run(DatacircleBestPlansTool(base_url=base), {"slug": "Bright Data"})
    assert answer["error"].startswith("no API has the slug bright-data.") and "datacircle_search_plans" in answer["error"]
    assert "slug" in run(DatacircleBestPlansTool(base_url=base), {"slug": " - "})["error"]


def test_search_plans(base):
    answer = run(DatacircleSearchPlansTool(base_url=base), {"api": "email-finder", "query": " hunter ", "sort": "Price",
                                                            "limit": 5, "offset": 2})
    assert SEEN[-1][1] == {"api": "email-finder", "query": "hunter", "sort": "price", "limit": "5", "offset": "2"}
    assert answer["total"] == 1 and "note" not in answer
    # A provider's name sent as the API: searched as a name.
    answer = run(DatacircleSearchPlansTool(base_url=base), {"api": "Bright Data"})
    assert SEEN[-1][1] == {"query": "Bright Data"} and "No API is named" in answer["note"]
    # A slug sent as a name: searched again by its words.
    answer = run(DatacircleSearchPlansTool(base_url=base), {"query": "bright-data"})
    assert SEEN[-1][1] == {"query": "bright data"} and answer["total"] == 1 and "bright data" in answer["note"]
    # An unknown API with a query: the API's own words.
    answer = run(DatacircleSearchPlansTool(base_url=base), {"api": "nope", "query": "x"})
    assert answer == {"error": "api must be one of: Employee email"}


def test_provider(base):
    answer = run(DatacircleProviderTool(base_url=base), {"slug": "Bright Data"})
    assert [api["slug"] for api in answer["apis"]] == ["linkedin-profile", "email-finder"]
    assert answer["apis"][0]["best"]["plan"] == "Small" and answer["apis"][0]["plans"] == 2
    assert answer["total"] == 2 and answer["ranked"] == 1
    answer = run(DatacircleProviderTool(base_url=base), {"slug": "email-finder"})
    assert "datacircle_best_plans" in answer["error"]


def test_input_the_framework_refuses(base):
    count = len(SEEN)
    for tool, input in [(DatacircleBestPlansTool(base_url=base), {"limit": 3}),
                        (DatacircleBestPlansTool(base_url=base), {"slug": "email-finder", "limit": 99}),
                        (DatacircleSearchPlansTool(base_url=base), {"sort": "cost"}),
                        (DatacircleSearchPlansTool(base_url=base), {"offset": -1}),
                        (DatacircleListApisTool(base_url=base), {"limit": 0})]:
        with pytest.raises(ToolInputValidationError):
            asyncio.run(_run(tool, input, None))
    assert len(SEEN) == count


def test_busy_down_and_retries(base):
    STATE["busy"] = 1
    assert run(DatacircleBestPlansTool(base_url=base), {"slug": "email-finder"})["total"] == 5
    STATE["busy"] = 2
    with pytest.raises(ToolError) as caught:
        asyncio.run(_run(DatacircleBestPlansTool(base_url=base), {"slug": "email-finder"}, None))
    assert "429 twice" in caught.value.explain()
    STATE["down"] = 1
    with pytest.raises(ToolError) as caught:
        asyncio.run(_run(DatacircleBestPlansTool(base_url=base), {"slug": "email-finder"}, None))
    assert isinstance(caught.value, DatacircleError) and caught.value.status == 503
    STATE["down"] = 1
    options = ToolRunOptions(retry_options=RetryOptions(max_retries=1))
    assert run(DatacircleBestPlansTool(base_url=base), {"slug": "email-finder"}, options)["total"] == 5
    with pytest.raises(ToolError) as caught:
        asyncio.run(_run(DatacircleBestPlansTool(base_url="http://127.0.0.1:9/v1"), {"slug": "email-finder"}, None))
    assert "could not be reached" in caught.value.explain()


def test_clone_keeps_the_address(base):
    tool = DatacircleProviderTool(base_url=base)
    clone = asyncio.run(tool.clone())
    assert clone is not tool and clone.base_url == base and clone.name == tool.name
    assert run(clone, {"slug": "bright-data"})["provider"] == "Bright Data"


def test_plain_functions(base):
    assert asyncio.run(list_apis("email", 1, base_url=base))["apis"][0]["slug"] == "email-finder"
    assert asyncio.run(best_plans("email-finder", base_url=base))["plans"][0]["provider"] == "Hunter"
    assert asyncio.run(search_plans(query="hunter", sort="score", base_url=base))["total"] == 1
    assert asyncio.run(provider_ranks("Bright Data", base_url=base))["ranked"] == 1
    with pytest.raises(ValidationError):
        asyncio.run(best_plans("email-finder", 0, base_url=base))

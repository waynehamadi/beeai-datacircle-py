"""BeeAI Framework tools on Datacircle's open API: find the best GTM APIs. The API needs no key:
https://api.datacircle.dev/v1 (its spec: https://api.datacircle.dev/v1/openapi.json). More at https://datacircle.dev

Each tool is a Tool class with a pydantic input schema and a JSONToolOutput: given to an agent (RequirementAgent,
ReActAgent, ToolCallingAgent...), the agent sends its schema and runs the calls the model makes. An answer of 4xx (an
unknown slug, a wrong argument) is the tool's output as {"error": ...}, in the API's words, so that the model can call
again. Input that doesn't fit the schema is refused by the framework before any call (ToolInputValidationError), and the
API out of reach raises DatacircleError, a ToolError: the agent gives the model both, and retry_options retry the second.
"""

import json
import urllib.parse
from typing import Any, Dict, List, Literal, Optional, Self

from beeai_framework.context import RunContext
from beeai_framework.emitter import Emitter
from beeai_framework.tools import JSONToolOutput, Tool, ToolRunOptions
from pydantic import BaseModel, ConfigDict, Field, field_validator

from beeai_datacircle import _api
from beeai_datacircle._api import Answer, ApiError

LIST_APIS_ABOUT = ("Lists the GTM APIs on Datacircle's sheet (https://datacircle.dev). GTM means go-to-market. GTM APIs "
                   "are the APIs sales and marketing teams use. Gives each API's slug, how many plans and providers it "
                   "has and its best plan. Call it first to find an API's slug.")
BEST_PLANS_ABOUT = ("Gives an API's plans best first by their score out of 100 on Datacircle's benchmark "
                    "(https://datacircle.dev), each with its rank, provider, plan, $ per 1,000 calls and its page. "
                    "perThousand is a plan's price of 1,000 items in dollars, pay the plan's whole price in its currency.")
SEARCH_PLANS_ABOUT = ("Searches every plan on Datacircle's sheet (https://datacircle.dev), or one API's, by provider or "
                      "plan name, sorted by score, provider or price. Gives how many were found and up to limit of them. "
                      "perThousand is a plan's price of 1,000 items in dollars, pay the plan's whole price in its currency.")
PROVIDER_ABOUT = ("Gives each API one provider sells and its rank on each on Datacircle's benchmark "
                  "(https://datacircle.dev), with its best plan there, its best rank first. total is how many APIs it "
                  "sells, ranked on how many of them it has a rank. For an API's best providers, call datacircle_best_plans.")

# Tells a model that sent a provider's name to an API's tool where a provider's plans are.
FOR_PROVIDER = ("A provider's plans: datacircle_search_plans with its name as query, like bright data, or "
                "datacircle_provider.")

# Tells a model that sent an API's slug to the provider's tool where an API's best providers are.
FOR_API = "An API's best providers: datacircle_best_plans with the API's slug, like email-finder."


# Each input's schema, what the model is sent. A key left out or null takes its default. The framework checks the input
# against it before the call, and the agent gives the model what was wrong, so that it can call again. A number sent as
# a string is read as one. A key of the model's own is dropped.

class ListApisInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: Optional[str] = Field(default=None, description="Words of the API's name or slug, like email or linkedin "
                                 "profile: the APIs that hold the most of them come first. Left out for the sheet's order")
    limit: Optional[int] = Field(default=None, ge=1, le=200,
                                 description="How many APIs to give, 1 to 200. Left out for 20: the sheet has about 170")


class BestPlansInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str = Field(description="The API's slug, like email-finder or linkedin-profile, as datacircle_list_apis gives it")
    limit: Optional[int] = Field(default=None, ge=1, le=50, description="How many plans to give, 1 to 50. Left out for 10")


class SearchPlansInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api: Optional[str] = Field(default=None, description="Only this API's plans: its name or slug, like email-finder. "
                               "A provider's name goes in query, never here. Left out for every API")
    query: Optional[str] = Field(default=None, description="Words that must all appear in the provider's or plan's "
                                 "name, like bright data. Left out for every plan")
    sort: Optional[Literal["score", "provider", "price"]] = Field(
        default=None, description="score (best first, the default), provider (A to Z) or price ($ per 1,000, lowest first)")
    limit: Optional[int] = Field(default=None, ge=1, le=50, description="How many plans to give, 1 to 50. Left out for 10")
    offset: Optional[int] = Field(default=None, ge=0, description="How many found plans to skip first. Left out for 0")

    @field_validator("sort", mode="before")
    @classmethod
    def _lower(cls, value: Any) -> Any:
        return (value.strip().lower() or None) if isinstance(value, str) else value


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str = Field(description="The provider's slug, like bright-data, or its name, like Bright Data: the name is "
                      "made a slug")


# What each tool runs: the answer the model reads, as a dict.

async def _list_apis(input: ListApisInput, base_url: Optional[str]) -> Answer:
    try:
        apis = _api.objects(await _api.get("/apis", base_url=base_url))
    except ApiError as error:
        return {"error": str(error)}
    words = [word for word in (input.query or "").lower().replace("-", " ").split() if word]
    held = []
    for order, api in enumerate(apis):
        name = f"{api.get('api') or ''} {str(api.get('slug') or '').replace('-', ' ')}".lower()
        matched = sum(word in name for word in words)
        if matched or not words:
            held.append((matched, order, api))
    # The most words matched first, the sheet's order within each.
    held.sort(key=lambda found: (-found[0], found[1]))
    answer: Answer = {"total": len(held),
                      "apis": [_api.brief(api, _api.API_KEYS) for _, _, api in held[:input.limit or 20]]}
    if words and (not held or held[0][0] < len(words)):
        # A provider's name sent as an API's, like Bright Data, whose words match many APIs: where its plans are.
        answer["note"] = (f"No API's name holds every word of {json.dumps(input.query)}. A provider's plans: "
                          "datacircle_search_plans with its name as query, or datacircle_provider.")
    return answer


async def _best_plans(input: BestPlansInput, base_url: Optional[str]) -> Answer:
    api = _api.slugify(input.slug)
    if not api:
        return {"error": f"Give an API's slug, like email-finder: datacircle_list_apis gives every slug. {FOR_PROVIDER}"}
    try:
        answer = await _api.get(f"/apis/{urllib.parse.quote(api)}/best", {"limit": input.limit}, base_url)
    except ApiError as error:
        if error.status == 404:
            # A provider's name sent as the API's slug: the API's words, and where a provider's plans are.
            return {"error": f"{error}. datacircle_list_apis gives every API's slug. {FOR_PROVIDER}"}
        return {"error": str(error)}
    return {**answer, "plans": _api.brief_plans(answer.get("plans"))}


async def _search_plans(input: SearchPlansInput, base_url: Optional[str]) -> Answer:
    async def search(api: str, query: str) -> Answer:
        return await _api.get("/plans", {"api": api.strip(), "query": query.strip(), "sort": input.sort,
                                         "limit": input.limit, "offset": input.offset}, base_url)

    api, query, notes = input.api or "", input.query or "", []
    try:
        try:
            answer = await search(api, query)
        except ApiError as error:
            if not (error.status == 400 and "api must be" in str(error) and not query.strip()):
                raise
            # A provider's name sent as the API: the plans whose provider or plan name holds it.
            api, query = "", api
            answer = await search(api, query)
            notes.append(f"No API is named {json.dumps(input.api)}: these are the plans whose provider or plan name "
                         "holds it.")
        if answer.get("total") == 0 and "-" in query:
            # A slug sent as a name, like bright-data, which no name holds: its words, like bright data. A name with a
            # hyphen, like A-Leads, is found as it is first.
            words = " ".join(query.replace("-", " ").split())
            try:
                answer = await search(api, words)
                notes.append(f"No provider's or plan's name holds {json.dumps(query)}: these are the plans whose name "
                             f"holds {json.dumps(words)}.")
            except (ApiError, _api.DatacircleError):
                pass  # The first answer stands.
    except ApiError as error:
        return {"error": str(error)}
    return {**answer, "plans": _api.brief_plans(answer.get("plans")), **({"note": " ".join(notes)} if notes else {})}


def _best_plan(plans: List[Answer]) -> Optional[Answer]:
    """A provider's best plan on an API: its lowest rank, the first when none has a rank."""
    ranked = [plan for plan in plans if isinstance(plan.get("rank"), (int, float)) and not isinstance(plan["rank"], bool)]
    return min(ranked, key=lambda plan: plan["rank"]) if ranked else (plans[0] if plans else None)


async def _provider(input: ProviderInput, base_url: Optional[str]) -> Answer:
    provider = _api.slugify(input.slug)
    if not provider:
        return {"error": "Give a provider's slug or name, like bright-data or Bright Data"}
    try:
        answer = await _api.get(f"/providers/{urllib.parse.quote(provider)}", base_url=base_url)
    except ApiError as error:
        # An API's slug sent as a provider's, like email-finder: the API's words, and where an API's best providers are.
        return {"error": f"{error}. {FOR_API}" if error.status == 404 else str(error)}
    apis = []
    for entry in _api.objects(answer.get("apis")):
        plans = _api.objects(entry.get("plans"))
        kept: Answer = {**_api.brief(entry, ["api", "slug", "rank", "bestPage"]), "plans": len(plans)}
        best = _best_plan(plans)
        if best:
            kept["best"] = _api.brief(best, ["plan", "score", "perThousand", "page"])
        apis.append(kept)

    def rank(api: Answer) -> float:
        value = api.get("rank")
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else float("inf")

    # Its best rank first, the APIs where it has none last, in the API's order.
    apis.sort(key=rank)
    return {**answer, "apis": apis, "total": len(apis), "ranked": sum(rank(api) != float("inf") for api in apis)}


# The tools.

class _DatacircleTool(Tool[Any, ToolRunOptions, JSONToolOutput[Answer]]):
    """A Datacircle tool. options are the framework's own (cache); base_url is the API's address, BASE_URL when left out."""

    _namespace = "datacircle"

    def __init__(self, options: Optional[Dict[str, Any]] = None, *, base_url: Optional[str] = None) -> None:
        super().__init__(options)
        self.base_url = base_url

    def _create_emitter(self) -> Emitter:
        return Emitter.root().child(namespace=["tool", "datacircle", self._namespace], creator=self)

    async def clone(self) -> Self:
        tool = self.__class__(options=self.options, base_url=self.base_url)
        tool.name = self.name
        tool.description = self.description
        tool.input_schema = self.input_schema
        tool.middlewares.extend(self.middlewares)
        tool._cache = await self.cache.clone()
        return tool

    async def _answer(self, input: Any) -> Answer:
        raise NotImplementedError

    async def _run(self, input: Any, options: Optional[ToolRunOptions], context: RunContext) -> JSONToolOutput[Answer]:
        return JSONToolOutput(await self._answer(input))


class DatacircleListApisTool(_DatacircleTool):
    """datacircle_list_apis: every GTM API on the sheet, with its slug, how many plans and providers it has and its best
    plan (GET /apis)."""

    name = "datacircle_list_apis"
    description = LIST_APIS_ABOUT
    input_schema = ListApisInput
    _namespace = "list_apis"

    async def _answer(self, input: ListApisInput) -> Answer:
        return await _list_apis(input, self.base_url)


class DatacircleBestPlansTool(_DatacircleTool):
    """datacircle_best_plans: an API's plans best first by their score out of 100, each with its rank and price
    (GET /apis/{slug}/best)."""

    name = "datacircle_best_plans"
    description = BEST_PLANS_ABOUT
    input_schema = BestPlansInput
    _namespace = "best_plans"

    async def _answer(self, input: BestPlansInput) -> Answer:
        return await _best_plans(input, self.base_url)


class DatacircleSearchPlansTool(_DatacircleTool):
    """datacircle_search_plans: every plan, or an API's, by provider or plan name, sorted by score, provider or price
    (GET /plans)."""

    name = "datacircle_search_plans"
    description = SEARCH_PLANS_ABOUT
    input_schema = SearchPlansInput
    _namespace = "search_plans"

    async def _answer(self, input: SearchPlansInput) -> Answer:
        return await _search_plans(input, self.base_url)


class DatacircleProviderTool(_DatacircleTool):
    """datacircle_provider: each API one provider sells and its rank on each (GET /providers/{slug})."""

    name = "datacircle_provider"
    description = PROVIDER_ABOUT
    input_schema = ProviderInput
    _namespace = "provider"

    async def _answer(self, input: ProviderInput) -> Answer:
        return await _provider(input, self.base_url)


def datacircle_tools(options: Optional[Dict[str, Any]] = None, *,
                     base_url: Optional[str] = None) -> List[_DatacircleTool]:
    """The four tools, for an agent's tools: RequirementAgent(llm=llm, tools=datacircle_tools()). Each gets the same
    options and base_url."""
    return [DatacircleListApisTool(options, base_url=base_url), DatacircleBestPlansTool(options, base_url=base_url),
            DatacircleSearchPlansTool(options, base_url=base_url), DatacircleProviderTool(options, base_url=base_url)]


# The same calls without a model or the framework: each gives the answer the model would read, as a dict. An argument
# that doesn't fit raises pydantic's ValidationError.

async def list_apis(query: Optional[str] = None, limit: Optional[int] = None, *,
                    base_url: Optional[str] = None) -> Answer:
    """GET /apis: the APIs, as datacircle_list_apis gives them."""
    return await _list_apis(ListApisInput(query=query, limit=limit), base_url)


async def best_plans(slug: str, limit: Optional[int] = None, *, base_url: Optional[str] = None) -> Answer:
    """GET /apis/{slug}/best: an API's plans best first, as datacircle_best_plans gives them."""
    return await _best_plans(BestPlansInput(slug=slug, limit=limit), base_url)


async def search_plans(api: Optional[str] = None, query: Optional[str] = None, sort: Optional[str] = None,
                       limit: Optional[int] = None, offset: Optional[int] = None, *,
                       base_url: Optional[str] = None) -> Answer:
    """GET /plans: the plans found, as datacircle_search_plans gives them."""
    return await _search_plans(SearchPlansInput(api=api, query=query, sort=sort, limit=limit, offset=offset), base_url)


async def provider_ranks(slug: str, *, base_url: Optional[str] = None) -> Answer:
    """GET /providers/{slug}: where a provider ranks, as datacircle_provider gives it."""
    return await _provider(ProviderInput(slug=slug), base_url)

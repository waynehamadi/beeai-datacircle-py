"""BeeAI Framework tools on Datacircle's open API: find the best GTM APIs. No key, no account.
https://datacircle.dev, the API's spec at https://api.datacircle.dev/v1/openapi.json"""

from beeai_datacircle._api import BASE_URL, VERSION, ApiError, DatacircleError
from beeai_datacircle.tools import (
    BestPlansInput,
    DatacircleBestPlansTool,
    DatacircleListApisTool,
    DatacircleProviderTool,
    DatacircleSearchPlansTool,
    ListApisInput,
    ProviderInput,
    SearchPlansInput,
    best_plans,
    datacircle_tools,
    list_apis,
    provider_ranks,
    search_plans,
)

__version__ = VERSION

__all__ = [
    "BASE_URL",
    "ApiError",
    "BestPlansInput",
    "DatacircleBestPlansTool",
    "DatacircleError",
    "DatacircleListApisTool",
    "DatacircleProviderTool",
    "DatacircleSearchPlansTool",
    "ListApisInput",
    "ProviderInput",
    "SearchPlansInput",
    "__version__",
    "best_plans",
    "datacircle_tools",
    "list_apis",
    "provider_ranks",
    "search_plans",
]

# Datacircle tools for the BeeAI Framework

Find the best GTM APIs. We benchmark all the GTM APIs and we put the results on our website.

Our website: https://datacircle.dev

This package gives the [BeeAI Framework](https://github.com/i-am-bee/beeai-framework) (Python) four tools that call Datacircle's open API, https://api.datacircle.dev/v1. They work in BeeAI's agents: `RequirementAgent`, `ReActAgent`, `ToolCallingAgent`, `LiteAgent` and the agents of an `AgentWorkflow`. You need no key and no account.

| Tool | Class | What the tool gives | Call |
| --- | --- | --- | --- |
| `datacircle_list_apis` | `DatacircleListApisTool` | Every GTM API on Datacircle, with the API's slug, number of plans, number of providers and best plan | `GET /apis` |
| `datacircle_best_plans` | `DatacircleBestPlansTool` | One API's plans, sorted by score out of 100, best first. Each plan comes with a rank and a price per 1,000 calls | `GET /apis/{slug}/best` |
| `datacircle_search_plans` | `DatacircleSearchPlansTool` | Plans that match a provider or plan name, on all APIs or on one API. You can sort them by score, provider or price | `GET /plans` |
| `datacircle_provider` | `DatacircleProviderTool` | The APIs a provider sells, and the provider's rank on each API | `GET /providers/{slug}` |

## Install

```bash
pip install beeai-datacircle
```

beeai-datacircle works with `beeai-framework` 0.1.30 and later, on Python 3.11 to 3.13. Import `RequirementAgent` from `beeai_framework.agents.experimental` if your `beeai-framework` is older than 0.1.51.

## Use the tools in an agent

Give the tools to an agent:

```python
import asyncio

from beeai_framework.agents.requirement import RequirementAgent
from beeai_framework.backend import ChatModel
from beeai_datacircle import datacircle_tools


async def main():
    agent = RequirementAgent(llm=ChatModel.from_name("openai:gpt-5.5"), tools=datacircle_tools())
    response = await agent.run("Who are the 3 best providers for the email-finder API?")
    print(response.last_message.text)


asyncio.run(main())
```

`ReActAgent`, `ToolCallingAgent` and `LiteAgent` take the same `tools`. You can put them in one list with your own tools: `tools=[*datacircle_tools(), my_tool]`.

Each tool is a class. You can name a tool in a requirement:

```python
from beeai_framework.agents.requirement.requirements.conditional import ConditionalRequirement
from beeai_datacircle import DatacircleListApisTool, datacircle_tools

agent = RequirementAgent(
    llm=llm,
    tools=datacircle_tools(),
    requirements=[ConditionalRequirement(DatacircleListApisTool, force_at_step=1)],
)
```

To give the agent only some of the tools, create only those classes: `tools=[DatacircleBestPlansTool(), DatacircleSearchPlansTool()]`.

## Run a tool yourself

You can run each tool without a model, the same way an agent runs the tool:

```python
from beeai_datacircle import DatacircleBestPlansTool

output = await DatacircleBestPlansTool().run({"slug": "email-finder", "limit": 3})
print(output.result["plans"])
```

Or call the API without BeeAI. You get back the API's answer as a dict:

```python
from beeai_datacircle import best_plans

answer = await best_plans("email-finder", limit=3)
```

You can import three more functions: `list_apis()`, `search_plans()` and `provider_ranks()`.

## What the model reads

Each tool gives the model a JSON object as text. Each plan comes with a rank, provider, plan, score, price per 1,000 (`perThousand`, in dollars) and a page on datacircle.dev. The page on datacircle.dev lists every field.

You can give a provider's name in place of a slug. The tool turns `Bright Data` into `bright-data`.

The tool returns `{"error": "..."}` with the API's message when the API gives no data, for example for an unknown slug. BeeAI refuses input that does not fit the schema with a `ToolInputValidationError`. The agent then tells the model what was wrong. If the network fails, the tool raises `DatacircleError`, a subclass of `ToolError`. The agent passes the error to the model. The model can then call the tool again.

Each tool takes BeeAI's options, like `cache`, and BeeAI's run options, like `retry_options`. Each tool also takes `base_url`, the API's address. `datacircle_tools(options, base_url=...)` gives each tool the same options. For a proxy, set `HTTPS_PROXY`.

## Network

Each tool call makes one HTTPS GET request to https://api.datacircle.dev/v1 and nothing else. The request holds only the tool's own arguments: no key and nothing from your chats. The API takes up to 60 calls a minute from one IP address. The tool waits once, for a minute at most, when the API asks the tool to wait. The package needs only `beeai-framework`, `httpx` and `pydantic`. The package stores nothing.

## Links

- Datacircle: https://datacircle.dev
- The API: https://datacircle.dev/api
- The API's spec: https://api.datacircle.dev/v1/openapi.json
- What each key means: https://datacircle.dev/fields

MIT license.

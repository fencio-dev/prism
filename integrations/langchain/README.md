# langchain-prism

LangChain and LangGraph callback integration for [Prism](https://github.com/fencio-dev/prism) — policy enforcement for AI agents.

## Installation

```bash
pip install langchain-prism
```

## How it works

`PrismCallback` intercepts tool calls before execution via LangChain's `BaseCallbackHandler` interface. For each tool call, it forwards an intent event to the Prism data plane, which evaluates it against installed policies and returns ALLOW or DENY. A DENY raises a `PermissionError` that halts execution before the tool runs.

Enforcement operates at two levels:
- **Tool calls** — via `PrismCallback` (works for both LangChain and LangGraph)
- **LLM calls** — via `PrismModel` (wraps your chat model; required because LangGraph swallows callback exceptions from LLM hooks)

## Usage

Attach `PrismCallback` at invocation time — no changes to agent code required:

```python
# LangChain
agent.invoke(input, config={"callbacks": [callback]})

# LangGraph
graph.invoke(input, config={"callbacks": [callback]})
```

`PrismCallback` supports three configurations depending on how tool intent is inferred.

**With `model`** (standard — model infers action and resource from tool name):

```python
from prism.langchain import PrismCallback
from langchain_openai import ChatOpenAI

callback = PrismCallback(
    base_url="https://your-prism-instance",
    tenant_id="your-tenant-id",
    model=ChatOpenAI(model="gpt-4o-mini", temperature=0),
)
```

**With custom mappers** (deterministic — no `model` needed):

```python
callback = PrismCallback(
    base_url="https://your-prism-instance",
    tenant_id="your-tenant-id",
    action_mapper=lambda tool_name, tool_input: "deleting record",
    resource_mapper=lambda tool_name: "database",
)
```

**Mixed** (one mapper + `model` for the other):

```python
callback = PrismCallback(
    base_url="https://your-prism-instance",
    tenant_id="your-tenant-id",
    model=ChatOpenAI(model="gpt-4o-mini", temperature=0),
    action_mapper=lambda tool_name, tool_input: "deleting record",  # overrides model for action
    # resource inferred by model
)
```

## LLM-level enforcement

To enforce on LLM calls (not just tool calls), wrap your chat model with `PrismModel`:

```python
from prism.langchain import PrismCallback, PrismModel
from langchain_openai import ChatOpenAI

inner_llm = ChatOpenAI(model="gpt-4o")
callback = PrismCallback(base_url=..., tenant_id=..., model=ChatOpenAI(model="gpt-4o-mini", temperature=0))
llm = PrismModel(llm=inner_llm, callback=callback)
```

`PrismModel` enforces inside `_generate` / `_agenerate` before delegating to the inner LLM, bypassing the LangGraph callback-swallowing issue.

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_url` | `str` | required | Prism instance URL |
| `tenant_id` | `str` | required | Tenant identifier, sent as `X-Tenant-Id` |
| `model` | `BaseChatModel \| Callable \| None` | `None` | Model for intent inference. Required unless both mappers are provided |
| `api_key` | `str \| None` | `None` | Optional API key |
| `agent_id` | `str` | `"unknown"` | Agent identifier included in intent events |
| `principal_id` | `str` | `"unknown"` | Human user or service account on whose behalf the agent acts (audit only) |
| `enforcement_mode` | `str` | `"block"` | `"block"` raises `PermissionError` on DENY; `"warn"` logs and continues |
| `timeout` | `float` | `2.0` | Request timeout in seconds |
| `fail_open` | `bool` | `True` | If `True`, allows execution when Prism is unreachable; if `False`, raises |
| `action_mapper` | `Callable[[str, dict], str] \| None` | `None` | Overrides model inference for the `action` field |
| `resource_mapper` | `Callable[[str], str] \| None` | `None` | Overrides model inference for the `resource` field |

**Mapper precedence:**

| `action_mapper` | `resource_mapper` | `model` required? |
|---|---|---|
| provided | provided | No |
| provided | absent | Yes — used for resource |
| absent | provided | Yes — used for action |
| absent | absent | Yes — used for both |

## Lightweight model options

Use a small, fast model — intent inference is a simple classification task:

**OpenAI**
```python
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
```

**Anthropic**
```python
from langchain_anthropic import ChatAnthropic
model = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)
```

**Google**
```python
from langchain_google_genai import ChatGoogleGenerativeAI
model = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
```

**Custom callable** (bring your own inference function)
```python
def my_model(system_msg: str, user_msg: str) -> str:
    # Return raw JSON: {"action": "...", "resource": "..."}
    ...

model = my_model
```

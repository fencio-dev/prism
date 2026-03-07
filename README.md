<h1 align="center">Prism — Adaptive Runtime Enforcement for AI Agents</h1>

<p align="center">
  Semantic policy enforcement that learns. Block, modify, or escalate agent actions in real-time — and turn every decision into training data.
</p>

<div align="center">

[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/fencio-dev/prism?style=flat-square)](https://github.com/fencio-dev/prism/stargazers)

</div>

---

Prism is a local policy enforcement stack for AI agents. Instead of static rules or regex, it understands **intent** — mapping agent actions into a 128-dimensional semantic space and enforcing policies based on proximity to defined boundaries.

Every enforcement decision feeds a **closed-loop training pipeline**: reviewers label decisions in the UI, export labeled JSONL, and fine-tune the embedding model. The safety layer gets more precise over time.

## Quick Start

```bash
git clone https://github.com/fencio-dev/prism.git
cd prism

# Install all dependencies (Python + Rust)
make install

# Start the backend stack: data plane + MCP server + management plane
make run-all
```

In a second terminal:

```bash
cd ui
npm install
npm run dev
```

Open `http://localhost:5173` for the UI. Management Plane API runs on `http://localhost:8001`.

## How It Works

### The Flywheel

Prism learns from your production traffic through four phases:

1. **Observe** — Deploy policies in `dry_run` mode. Prism captures agent intents and flags semantic drift from baseline behavior.
2. **Review** — Use the UI to inspect decisions. Domain experts label each decision: Correct or Incorrect.
3. **Export** — Export validated feedback as labeled JSONL (`intent_text`, `anchor_text`, `similarity`, `threshold`, `feedback_score`, `rationale`).
4. **Evolve** — Fine-tune your embedding model on the exported dataset. Hot-swap the updated model back into the stack.

### AARM Outcomes

Prism implements the [AARM](https://aarm.dev) standard for every enforced intent:

| Outcome | Behavior |
|---------|----------|
| `ALLOW` | Intent verified safe, proceeds to execution |
| `DENY` | Intent blocked, agent is notified |
| `MODIFY` | Parameters rewritten before execution (e.g. mask PII, cap budget) |
| `STEP_UP` | High-risk action triggers human approval or step-up auth |
| `DEFER` | Decision offloaded to an external organizational governor |

## Architecture

| Component | Stack | Port |
|-----------|-------|------|
| Management Plane | FastAPI (Python) | 8001 |
| Data Plane | Rust gRPC | 50051 |
| MCP Server | Node.js | 3001 |
| Web UI | Vite + React | 5173 |

## Enforcement API

Integrate Prism into your agent's tool-calling loop:

**`POST /api/v2/enforce`**

```json
{
  "event_type": "tool_call",
  "id": "call_001",
  "ts": 1740998400.0,
  "identity": {
    "agent_id": "agent_123",
    "principal_id": "user_42",
    "actor_type": "agent"
  },
  "t": "github.pull_request",
  "op": "create pull request",
  "p": "open a PR from feature branch into main",
  "params": {
    "repo": "acme/platform",
    "base": "main",
    "head": "feature/prism"
  }
}
```

Add `?dry_run=true` to inspect the decision without enforcing it.

## Other APIs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/api/v2/policies` | Create and install a policy |
| `GET` | `/api/v2/policies` | List active policies |
| `GET` | `/api/v2/policies/{policy_id}` | Get a specific policy |
| `GET` | `/api/v2/telemetry/sessions` | List agent sessions |
| `GET` | `/api/v2/telemetry/sessions/{agent_id}` | Get session telemetry |

## Tail Logs

```bash
tail -F \
  data/logs/data-plane.log \
  data/logs/mcp-server.log \
  data/logs/management-plane.log \
| awk '
/data-plane\.log/       {tag="[DATA-PLANE]"; next}
/mcp-server\.log/       {tag="[MCP-SERVER]"; next}
/management-plane\.log/ {tag="[MGMT-PLANE]"; next}
{print tag " " $0}
'
```

## Configuration

- Backend environment: `management_plane/.env` (loaded automatically when present)
- Deployment template: `deployment/.env.example`
- Default embedding model: `redis/langcache-embed-v3-small` (384d, optimized for local inference)

## Integrations

### LangChain / LangGraph

Install `langchain-prism` to add Prism enforcement to any LangChain or LangGraph agent:

```bash
pip install langchain-prism
```

```python
from prism.langchain import PrismCallback
from langchain_openai import ChatOpenAI

callback = PrismCallback(
    base_url="http://localhost:8001",
    tenant_id="your-tenant-id",
    model=ChatOpenAI(model="gpt-4o-mini", temperature=0),
)

# Attach at invocation — no changes to agent code required
agent.invoke(input, config={"callbacks": [callback]})
graph.invoke(input, config={"callbacks": [callback]})
```

Tool calls are intercepted before execution. A DENY raises `PermissionError` and halts the agent. See [`integrations/langchain/`](integrations/langchain/) for full documentation.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, code style, and PR guidelines.

## License

MIT

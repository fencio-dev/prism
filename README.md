# Prism

Prism is a local policy enforcement stack with:
- Management Plane (FastAPI)
- Data Plane (Rust gRPC)
- MCP server
- Web UI

## Run Locally

```bash
git clone <repository-url>
cd guard

# Backend stack: data plane + MCP server + management plane
make run-all
```

In a second terminal:

```bash
cd ui
npm install
npm run dev
```

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

## Core Functionality

- Policies: create/update/toggle/delete tenant-scoped policies from the UI or API.
- Dry Run: send intent events to enforcement and inspect decision + per-slice evidence.
- Build Dataset: export reviewer feedback from dry runs as JSONL for model iteration.

## APIs You Can Use

- Enforcement API: `POST /api/v2/enforce`
- Policies Install API: `POST /api/v2/policies` (creates and installs a policy into the data plane)
- Other GET APIs:
  - `GET /health`
  - `GET /api/v2/policies`
  - `GET /api/v2/policies/{policy_id}`
  - `GET /api/v2/telemetry/sessions`
  - `GET /api/v2/telemetry/sessions/{agent_id}`

## Embeddings and Training

- Prism uses a SentenceTransformer embedding model in the management plane.
- The current encoder default in code is `redis/langcache-embed-v3-small`.
- Build Dataset downloads JSONL with labeled slice feedback (`intent_text`, `anchor_text`, `similarity`, `threshold`, `feedback_score`, `rationale`).
- You can use this JSONL to fine-tune or evaluate your own embedding model on Hugging Face or your own VPS training setup.

## Configuration Notes

- For local backend runtime, environment values are loaded from `management_plane/.env` when present.
- Example deployment env template is at `deployment/.env.example`.

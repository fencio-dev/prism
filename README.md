# Guard: Policy Enforcement with Semantic Canonicalization

Guard is an access control enforcement system that bridges variable vocabulary to canonical policy definitions using BERT-based canonicalization. It provides three REST APIs for semantic intent enforcement, policy management, and vocabulary debugging.

## Quick Start

### Prerequisites
- Python 3.12+
- Rust toolchain (for building data plane)
- `uv` package manager (recommended) or `pip`

### Setup & Run (First Time)

```bash
# Clone the repository
git clone <repository-url>
cd guard

# Install all dependencies and build both services
make install

# Set up environment variables
cp deployment/.env.example .env
# Edit .env with your configuration (DATA_PLANE_URL, BERT model paths, etc.)

# Run BOTH management plane (port 8000) and data plane (port 50051)
make run-all
```

The API will be available at `http://localhost:8000/api/v2/`

Logs are written to:
- Management Plane: `data/logs/management-plane.log`
- Data Plane: `data/logs/data-plane.log`

### Essential Make Commands

```bash
# Run both services with logging
make run-all

# Run management plane only
make run-mgmt

# Run data plane only
make run-data

# Run all tests
make test

# View all available commands
make help
```

### Using Docker Compose

Alternatively, use Docker Compose for containerized setup:

```bash
cd deployment
docker-compose -f docker-compose.production.yml up
```

---

## API Reference

Guard exposes **3 REST endpoints** for access control enforcement:

### 1. **POST /api/v2/enforce** - Enforcement with Canonicalization

Enforce an access intent with automatic vocabulary canonicalization.

**Use this when:** You want to check if an actor can perform an action on a resource, with automatic translation of non-standard terms to canonical vocabulary.

**Request:**
```json
{
  "action": "query",              // User's vocabulary (e.g., "query", "get", "read")
  "actor": {
    "id": "user-123",
    "type": "user"
  },
  "resource": {
    "type": "postgres_db",        // Non-canonical term
    "name": "customers"
  },
  "data": {
    "sensitivity": ["confidential"],
    "pii": false,
    "volume": "single"
  },
  "risk": {
    "authn": "required"
  }
}
```

**Response:**
```json
{
  "decision": "ALLOW",                          // or "DENY"
  "enforcement_latency_ms": 15.2,
  "metadata": {
    "request_id": "uuid",
    "canonicalization_trace": [
      {
        "field": "action",
        "raw_input": "query",
        "prediction": {
          "canonical": "read",
          "confidence": 0.95,
          "source": "bert_high"
        }
      },
      {
        "field": "resource_type",
        "raw_input": "postgres_db",
        "prediction": {
          "canonical": "database",
          "confidence": 0.92,
          "source": "bert_high"
        }
      }
    ]
  }
}
```

**Key Features:**
- Automatic canonicalization of `action`, `resource_type`, and `sensitivity` fields
- BERT-based vocabulary mapping with confidence scores
- Full trace of canonicalization decisions
- Sub-20ms enforcement latency
- Returns decision with confidence metadata

**Entry Points in Code:**
- Endpoint: `management_plane/app/endpoints/enforcement_v2.py:267-398`
- Canonicalization: `management_plane/app/services/canonicalizer.py`
- Encoding: `management_plane/app/services/intent_encoder.py`
- Data Plane: `management_plane/app/services/dataplane_client.py`

---

### 2. **POST /api/v2/canonicalize** - Debug Canonicalization

Show how a given intent would be canonicalized without enforcing it. Useful for testing vocabulary mappings and debugging canonicalization behavior.

**Use this when:** You want to understand how non-canonical terms map to canonical vocabulary, or debug vocabulary issues.

**Request:**
```json
{
  "action": "get_data",           // Non-canonical action
  "actor": {
    "id": "user-456",
    "type": "service"
  },
  "resource": {
    "type": "s3_bucket",          // Non-canonical resource
    "name": "logs"
  },
  "data": {
    "sensitivity": ["internal"],
    "pii": true,
    "volume": "batch"
  }
}
```

**Response:**
```json
{
  "canonical_intent": {
    "action": "read",
    "actor": {
      "id": "user-456",
      "type": "service"
    },
    "resource": {
      "type": "storage",          // Canonicalized
      "name": "logs"
    },
    "data": {
      "sensitivity": ["internal"],
      "pii": true,
      "volume": "batch"
    }
  },
  "canonicalization_trace": [
    {
      "field": "action",
      "raw_input": "get_data",
      "prediction": {
        "canonical": "read",
        "confidence": 0.88,
        "source": "bert_medium"
      }
    },
    {
      "field": "resource_type",
      "raw_input": "s3_bucket",
      "prediction": {
        "canonical": "storage",
        "confidence": 0.97,
        "source": "bert_high"
      }
    }
  ]
}
```

**Key Features:**
- Shows canonical intent and full canonicalization trace
- No enforcement - purely for debugging
- Indicates confidence levels and prediction source
- Helps iterate on vocabulary mappings

**Entry Points in Code:**
- Endpoint: `management_plane/app/endpoints/enforcement_v2.py:400-460`
- Canonicalization: `management_plane/app/services/canonicalizer.py`

---

### 3. **POST /api/v2/policies/install** - Install Policies with Canonicalization

Install or update access control policies with automatic canonicalization of policy definitions.

**Use this when:** You want to define new access control boundaries/policies, with automatic translation to canonical vocabulary.

**Request:**
```json
{
  "id": "boundary-1",
  "scope": {
    "tenantId": "tenant-1",
    "domains": ["api.example.com"]
  },
  "rules": {
    "aggregation": "AND",
    "weights": {
      "action": 1.0,
      "resource_type": 1.0,
      "sensitivity": 0.8,
      "risk": 0.5
    }
  },
  "constraints": {
    "action": {
      "allow": ["query", "retrieve"]      // Non-canonical actions
    },
    "resource": {
      "allow": ["postgres_db", "mysql"]   // Non-canonical resource types
    },
    "data": {
      "deny_sensitivity": ["secret"]
    },
    "risk": {
      "require": ["authn"]
    }
  }
}
```

**Response:**
```json
{
  "status": "installed",
  "boundary_id": "boundary-1",
  "request_id": "uuid",
  "canonicalization_trace": [
    {
      "field": "action",
      "raw_input": "query",
      "prediction": {
        "canonical": "read",
        "confidence": 0.95,
        "source": "bert_high"
      }
    },
    {
      "field": "resource_type",
      "raw_input": "postgres_db",
      "prediction": {
        "canonical": "database",
        "confidence": 0.92,
        "source": "bert_high"
      }
    }
  ],
  "installation_stats": {
    "rules_installed": 1,
    "evaluation_time_ms": 2.5
  }
}
```

**Key Features:**
- Canonicalizes policy constraints before installation
- Installs to Data Plane via gRPC
- Returns canonicalization trace for all translated terms
- Installation statistics from Data Plane

**Entry Points in Code:**
- Endpoint: `management_plane/app/endpoints/enforcement_v2.py:463-560`
- Canonicalization: `management_plane/app/services/canonicalizer.py`
- Policy Encoding: `management_plane/app/services/policy_encoder.py`
- Data Plane Installation: `management_plane/app/services/dataplane_client.py`

---

## Authentication

All three endpoints require authentication via one of:

1. **JWT Token** (via Authorization header):
   ```bash
   curl -H "Authorization: Bearer <jwt-token>" \
     -X POST http://localhost:8000/api/v2/enforce \
     -H "Content-Type: application/json" \
     -d @request.json
   ```

2. **API Key** (via x-api-key header):
   ```bash
   curl -H "x-api-key: <api-key>" \
     -X POST http://localhost:8000/api/v2/enforce \
     -H "Content-Type: application/json" \
     -d @request.json
   ```

3. **Custom Headers** (tenant_id, user_id):
   ```bash
   curl -H "tenant-id: tenant-1" \
     -H "user-id: user-123" \
     -X POST http://localhost:8000/api/v2/enforce \
     -H "Content-Type: application/json" \
     -d @request.json
   ```

---

## Understanding Canonicalization

The core innovation in Guard is **BERT-based vocabulary canonicalization**. This bridges the gap between how users naturally describe access intents and your canonical vocabulary.

### Canonical Vocabulary

Guard recognizes these canonical terms:

**Actions:**
- `read` - Query, retrieve, or get data
- `write` - Create or insert data
- `update` - Modify existing data
- `delete` - Remove data
- `execute` - Run operations
- `export` - Download or extract data

**Resource Types:**
- `database` - Relational or NoSQL databases
- `storage` - Object storage, file systems
- `api` - REST/GraphQL APIs
- `queue` - Message queues, pub/sub
- `cache` - In-memory stores

**Sensitivity Levels:**
- `public` - Unrestricted access
- `internal` - Company/organization level
- `secret` - Restricted, highly sensitive data

### Confidence Scores

Canonicalization predictions include confidence levels:

- **`bert_high`** (confidence ≥ 0.90): High confidence mapping from BERT model
- **`bert_medium`** (0.70-0.89): Medium confidence mapping
- **`exact_match`**: User provided canonical term (no translation needed)

Use these confidence scores to validate canonicalization decisions and adjust as needed.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    REST API Requests                     │
│  /api/v2/enforce | /api/v2/canonicalize | /api/v2/... │
└──────────────────────────┬──────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │ FastAPI App │ (management_plane/app/main.py)
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼────────┐  ┌──────▼────────┐  ┌────▼──────────┐
│ Authentication │  │Canonicalization│  │Data Validation│
│  (JWT/API Key) │  │   (BERT ONNX)  │  │  (Pydantic)   │
└────────────────┘  └──────┬────────┘  └───────────────┘
                           │
                    ┌──────▼──────────┐
                    │ Intent Encoding │ (sentence-transformers)
                    │  Policy Encoding│
                    └──────┬──────────┘
                           │
                    ┌──────▼──────────┐
                    │  Data Plane     │ (gRPC)
                    │  (Rust Service) │
                    └─────────────────┘
```

**Key Components:**

1. **REST API Layer** (`management_plane/app/endpoints/enforcement_v2.py`)
   - 3 public endpoints for enforcement, canonicalization, and policy installation
   - Request validation, response formatting
   
2. **Canonicalization Layer** (`management_plane/app/services/canonicalizer.py`)
   - BERT-based term canonicalization
   - Confidence scoring
   - Full trace logging
   
3. **Encoding Layer** (`management_plane/app/services/intent_encoder.py`, `policy_encoder.py`)
   - Converts canonical intents/policies to dense vectors
   - Uses sentence-transformers for semantic embedding
   - 128-dim intent vectors, 4×16×32 policy tensors
   
4. **Data Plane Interface** (`management_plane/app/services/dataplane_client.py`)
   - gRPC client for Rust Data Plane service
   - Enforces policies, installs rules
   - Returns decisions with evidence

---

## Development Guide

### Project Structure

```
management_plane/
├── app/
│   ├── main.py                 # FastAPI app, router registration
│   ├── settings.py             # Configuration & environment
│   ├── models.py               # Pydantic models
│   ├── auth.py                 # Authentication logic
│   ├── endpoints/
│   │   └── enforcement_v2.py    # 3 V2 endpoints
│   └── services/
│       ├── canonicalizer.py            # BERT canonicalization
│       ├── intent_encoder.py           # Intent encoding
│       ├── policy_encoder.py           # Policy encoding
│       ├── semantic_encoder.py         # Base encoder (sentence-transformers)
│       ├── dataplane_client.py         # gRPC client
│       ├── canonicalization_logger.py  # Async logging
│       └── policy_converter.py         # Policy conversion
├── models/
│   └── canonicalizer_tinybert_v1.0/   # BERT model & tokenizer
├── generated/
│   ├── rule_installation_pb2.py        # Generated protobuf messages
│   └── rule_installation_pb2_grpc.py   # Generated gRPC stubs
├── tests/
│   ├── test_canonicalizer.py
│   ├── test_canonical_slots.py
│   └── test_enforcement_proxy.py
└── pyproject.toml              # Dependencies

data_plane/
└── proto/
    └── rule_installation.proto # gRPC service definition
```

### Running Tests

```bash
cd management_plane

# Run all tests
pytest

# Run specific test file
pytest tests/test_canonicalizer.py

# Run with coverage
pytest --cov=app tests/
```

### Key Entry Points for Development

**Understanding Canonicalization Flow:**
1. Start: `management_plane/app/endpoints/enforcement_v2.py:315` (canonicalizer call)
2. Main logic: `management_plane/app/services/canonicalizer.py:359` (canonicalize method)
3. BERT inference: `management_plane/app/services/canonicalizer.py:208` (_classify_text method)

**Understanding Enforcement Flow:**
1. Start: `management_plane/app/endpoints/enforcement_v2.py:337` (intent encoding)
2. Encoding: `management_plane/app/services/intent_encoder.py:121` (encode method)
3. Data Plane: `management_plane/app/endpoints/enforcement_v2.py:344` (dataplane_client.enforce call)

**Understanding Policy Installation Flow:**
1. Start: `management_plane/app/endpoints/enforcement_v2.py:505` (canonicalize_boundary)
2. Encoding: `management_plane/app/endpoints/enforcement_v2.py:526` (policy_encoder.encode)
3. Installation: `management_plane/app/endpoints/enforcement_v2.py:534` (dataplane_client.install_policies)

---

## Configuration

Environment variables required (see `deployment/.env.example`):

```env
# Authentication
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-key
SUPABASE_JWT_SECRET=your-jwt-secret

# Data Plane
DATA_PLANE_URL=localhost:50051

# BERT Canonicalization
BERT_MODEL_PATH=models/canonicalizer_tinybert_v1.0/model_optimized.onnx
BERT_TOKENIZER_PATH=models/canonicalizer_tinybert_v1.0/tokenizer/
BERT_CONFIDENCE_HIGH=0.9
BERT_CONFIDENCE_MEDIUM=0.7

# Logging
CANONICALIZATION_LOG_DIR=logs/canonicalization
CANONICALIZATION_LOG_RETENTION_DAYS=90

# Embeddings
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

---

## Troubleshooting

### BERT Model Not Loading
- Ensure `BERT_MODEL_PATH` and `BERT_TOKENIZER_PATH` point to valid directories
- Check that ONNX Runtime is installed: `pip install onnxruntime>=1.17.0`
- Verify model file exists: `ls -lh management_plane/models/canonicalizer_tinybert_v1.0/model_optimized.onnx`

### Data Plane Connection Error
- Ensure Data Plane service is running at `DATA_PLANE_URL`
- Check gRPC connectivity: `grpcurl -plaintext list localhost:50051`

### Low Confidence Scores
- Review canonicalization traces to understand which terms are problematic
- Use the `/api/v2/canonicalize` debug endpoint to test vocabulary mappings
- Consider adding training data for edge cases

### High Latency
- Typical latency is 15-20ms per enforcement request
- Check Data Plane performance metrics
- Monitor BERT model inference time in logs

---

## Dependencies

### Runtime Requirements
- **FastAPI** - REST API framework
- **Pydantic** - Data validation
- **NumPy** - Numerical operations
- **onnxruntime** - BERT model inference
- **transformers** - Tokenization
- **sentence-transformers** - Intent/policy encoding
- **grpcio** - Data Plane communication
- **python-jose** - JWT handling
- **supabase** - API key validation

See `management_plane/pyproject.toml` for complete list and versions.

---

## Support

For issues, questions, or contributions:
- Check test files for usage examples
- Review canonicalization traces in responses
- Use `/api/v2/canonicalize` endpoint to debug vocabulary mappings

---

## License

[Add your license here]

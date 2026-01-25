# Request Enforcement Flow Trace

Trace of how an enforcement request flows from the SDK through the Management Plane API to the Rust Data Plane.

---

## High-Level Architecture Overview

```
SDK (Python) → Management Plane (FastAPI) → Data Plane (Rust gRPC) → Semantic Sandbox (Rust FFI)
```

---

## Detailed Request Flow

### Phase 1: SDK Initiates Enforcement Request

**Location:** `sdk/python/tupl/data_plane_client.py`

1. **Client calls `enforce()`** with an `IntentEvent`:
   - File: `data_plane_client.py:81-114`
   - The SDK validates that the intent includes required v1.3 fields (especially `layer`)
   - Serializes the `IntentEvent` to JSON using Pydantic's `model_dump_json()`

2. **gRPC request prepared**:
   - File: `data_plane_client.py:126-129`
   - Creates `EnforceRequest` protobuf message containing:
     - `intent_event_json`: JSON string of the IntentEvent
     - `intent_vector`: Optional pre-computed 128d vector (usually empty)
   - Adds authentication metadata if token is provided

3. **gRPC call to Data Plane**:
   - File: `data_plane_client.py:138-142`
   - Calls `DataPlaneStub.Enforce()` with timeout (default 5s)
   - Connection goes to Data Plane gRPC server (default: `localhost:50051`)

---

### Phase 2: Data Plane Receives Request

**Location:** `data_plane/bridge/src/grpc_server.rs`

4. **gRPC Server handles Enforce RPC**:
   - File: `grpc_server.rs:480-544`
   - Method: `DataPlaneService::enforce()`
   - Logs enforcement session start
   - Extracts `intent_event_json` from request
   - Checks for optional `intent_vector` override (128 floats)

5. **Delegates to Enforcement Engine**:
   - File: `grpc_server.rs:512-516`
   - Calls `self.enforcement_engine.enforce()`
   - Passes intent JSON and optional vector override

---

### Phase 3: Enforcement Engine Orchestrates Evaluation

**Location:** `data_plane/bridge/src/enforcement_engine.rs`

6. **Parse Intent & Start Telemetry**:
   - File: `enforcement_engine.rs:139-182`
   - Parses JSON to `IntentEvent` struct
   - Extracts `layer` field (e.g., "L4" for tool gateway)
   - Starts telemetry session for audit trail
   - Records `IntentReceived` event with timestamp

7. **Encode Intent to 128d Vector**:
   - File: `enforcement_engine.rs:185-244`
   - **If vector_override provided**: Use it directly (skip encoding)
   - **Otherwise**: Call Management Plane `/encode/intent` endpoint
   
   **HTTP Request to Management Plane:**
   ```rust
   POST {management_plane_url}/encode/intent
   Content-Type: application/json
   Body: <intent_event_json>
   ```
   
   - File: `enforcement_engine.rs:463-502`
   - Management Plane encodes intent using sentence-transformers
   - Returns `{"vector": [f32; 128]}`
   - Records `EncodingCompleted` telemetry event
   - **Fail-closed**: If encoding fails, immediately returns BLOCK decision

---

### Phase 4: Management Plane Encoding (Parallel Flow)

**Location:** `management_plane/app/encoding.py`

8. **Intent Encoding Pipeline**:
   - Extracts 4 semantic slots from IntentEvent:
     - **Action**: What operation is happening (e.g., "search", "delete")
     - **Resource**: What's being accessed (e.g., "database/users")
     - **Data**: Data classification (e.g., "pii", "internal")
     - **Risk**: Risk indicators (e.g., "high", "requires_auth")
   
9. **Embedding Generation**:
   - File: `encoding.py:35-50`
   - Uses `sentence-transformers/all-MiniLM-L6-v2` model (384 dims)
   - Generates embedding for each of 4 slot strings
   - Model is singleton (loaded once and cached)

10. **Dimensionality Reduction**:
    - File: `encoding.py:94-118`
    - Projects each 384d embedding → 32d using sparse random projection
    - Each slot gets its own projection matrix (seeds: 42, 43, 44, 45)
    - Concatenates 4×32d = 128d final vector
    - L2 normalizes for cosine similarity comparison

11. **Returns to Data Plane**:
    - Returns JSON: `{"vector": [f32; 128]}`

---

### Phase 5: Query Rules & Evaluate

**Back in Data Plane - Enforcement Engine**

12. **Query Rules for Layer**:
    - File: `enforcement_engine.rs:247-294`
    - Calls `get_rules_for_layer(layer)` → queries Bridge
    - File: `enforcement_engine.rs:505-539`
    - Parses layer string (e.g., "L4") to `LayerId::L4ToolGateway`
    - Queries all rule tables for that layer
    - Sorts rules by priority (higher priority first)
    - **Fail-closed**: If no rules found, returns BLOCK
    - Records `RulesQueried` telemetry event

13. **Rule-by-Rule Evaluation Loop**:
    - File: `enforcement_engine.rs:298-428`
    - Iterates through rules in priority order
    - For each rule:

      a. **Retrieve Pre-encoded Rule Anchors**:
         - File: `enforcement_engine.rs:315-322`
         - Gets `RuleVector` from Bridge cache (anchors were pre-computed at install time)
         - Contains 4 anchor sets (action, resource, data, risk)
         - Each anchor set has up to 16 anchors × 32 dims

      b. **Compare via Semantic Sandbox**:
         - File: `enforcement_engine.rs:325-326`
         - Calls `compare_with_sandbox()`
         - File: `enforcement_engine.rs:543-572`

---

### Phase 6: Rust Semantic Sandbox FFI Call

**Location:** `data_plane/semantic-sandbox/src/lib.rs`

14. **FFI Vector Comparison**:
    - File: `enforcement_engine.rs:555-569`
    - Builds `VectorEnvelope` struct:
      ```rust
      VectorEnvelope {
          intent: [f32; 128],           // Intent vector from encoding
          action_anchors: [[f32; 32]; 16],   // Rule's action anchors
          action_anchor_count: usize,
          resource_anchors: [[f32; 32]; 16], // Rule's resource anchors
          resource_anchor_count: usize,
          data_anchors: [[f32; 32]; 16],     // Rule's data anchors  
          data_anchor_count: usize,
          risk_anchors: [[f32; 32]; 16],     // Rule's risk anchors
          risk_anchor_count: usize,
          thresholds: [f32; 4],         // Per-slot thresholds
          weights: [f32; 4],            // Per-slot weights
          decision_mode: 0,             // 0=min, 1=weighted
          global_threshold: 0.0,
      }
      ```

15. **Semantic Sandbox Comparison**:
    - File: `lib.rs:42-47`
    - Calls `compare_vectors()` FFI function
    - Performs cosine similarity comparison:
      - For each of 4 slots (action, resource, data, risk):
        - Extracts intent's 32d sub-vector
        - Compares against all anchors in that slot
        - Takes max similarity score
        - Checks if similarity ≥ threshold
    - Decision logic:
      - **Min mode** (default): ALL 4 slots must pass their thresholds
      - **Weighted mode**: Weighted average must exceed global threshold
    - Returns `ComparisonResult`:
      ```rust
      ComparisonResult {
          decision: u8,              // 0=block, 1=allow
          slice_similarities: [f32; 4]  // Per-slot similarity scores
      }
      ```

---

### Phase 7: Short-Circuit Logic & Decision

**Back in Enforcement Engine**

16. **Check Rule Result**:
    - File: `enforcement_engine.rs:329-427`
    - Records `RuleEvidence` for this evaluation
    - Records detailed telemetry event
    
17. **Short-Circuit on BLOCK**:
    - File: `enforcement_engine.rs:374-426`
    - **If decision == 0 (BLOCK)**:
      - Logs blocking rule
      - Records `ShortCircuit` telemetry event
      - Marks last rule as short-circuited
      - Records `FinalDecision` with BLOCK
      - Completes telemetry session
      - **Immediately returns** `EnforcementResult` with BLOCK
      - **Skips remaining rules** (performance optimization)

18. **Continue if ALLOW**:
    - If decision == 1, continues to next rule
    - All rules must pass for overall ALLOW

19. **Final Decision**:
    - File: `enforcement_engine.rs:430-459`
    - **If all rules passed**: Returns ALLOW with averaged similarities
    - Records `FinalDecision` telemetry event
    - Completes telemetry session with duration metrics

---

### Phase 8: Response Propagation

**Data Plane gRPC Server**

20. **Build gRPC Response**:
    - File: `grpc_server.rs:518-543`
    - Logs final decision
    - Converts `EnforcementResult` to `EnforceResponse` protobuf:
      ```protobuf
      EnforceResponse {
          decision: i32,                    // 0=BLOCK, 1=ALLOW
          slice_similarities: [f32; 4],     // Per-slot scores
          rules_evaluated: i32,             // Number of rules checked
          evidence: [RuleEvidence],         // Per-rule evaluation details
      }
      ```
    - Returns to SDK client

---

**SDK Receives Response**

21. **Convert Response**:
    - File: `data_plane_client.py:166-198`
    - Maps gRPC `EnforceResponse` to Pydantic `ComparisonResult`
    - Converts `RuleEvidence` to `BoundaryEvidence` (legacy compatibility)
    - Returns to application code

22. **Application Decision**:
    - Application checks `result.decision`
    - **If 0 (BLOCK)**: Raises exception or logs violation
    - **If 1 (ALLOW)**: Proceeds with tool execution

---

## Key Performance Optimizations

1. **Pre-computed Rule Anchors**: Rules are encoded once at install time, not at enforcement time.
2. **Short-circuit Evaluation**: First BLOCK stops all further rule checking.
3. **Vector Caching**: Intent encoding uses singleton model with cached projection matrices.
4. **Lock-free Reads**: Data Plane uses lock-free concurrent data structures (20M+ QPS).
5. **Fail-closed Security**: All errors default to BLOCK.

---

## Telemetry & Observability

Throughout the flow, comprehensive telemetry is recorded at:
- **Data Plane**: `~/var/hitlogs/` - Complete enforcement sessions with microsecond timing.
- **Events tracked**:
  - Intent received
  - Encoding started/completed/failed
  - Rules queried
  - Each rule evaluation (started/completed)
  - Short-circuit events
  - Final decision
  - Performance metrics (encoding, query, evaluation durations)

---

## Critical File References

| Component | File | Key Functions |
|-----------|------|---------------|
| SDK Client | `sdk/python/tupl/data_plane_client.py` | `enforce()` (81-114) |
| gRPC Server | `data_plane/bridge/src/grpc_server.rs` | `enforce()` (480-544) |
| Enforcement Engine | `data_plane/bridge/src/enforcement_engine.rs` | `enforce()` (129-460) |
| Intent Encoding | `management_plane/app/encoding.py` | `encode_to_128d()` |
| Semantic Sandbox | `data_plane/semantic-sandbox/src/lib.rs` | `compare_vectors()` (42-47) |
| Rule Query | `enforcement_engine.rs` | `get_rules_for_layer()` (505-539) |

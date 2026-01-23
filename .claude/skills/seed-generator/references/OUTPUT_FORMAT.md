# Output Format Reference

This reference document specifies the exact JSON schema for seed dataset examples and validation requirements.

## Table of Contents

1. [JSONL Schema](#jsonl-schema)
2. [Field Specifications](#field-specifications)
3. [Validation Rules](#validation-rules)
4. [Valid Examples](#valid-examples)
5. [Invalid Examples](#invalid-examples)
6. [Quality Metrics](#quality-metrics)

---

## JSONL Schema

The seed dataset is stored in **JSONL format** (JSON Lines: one JSON object per line, no commas between lines).

### Complete Schema

```json
{
  "id": "string (UUID v4, required)",
  "raw_text": "string (non-empty, required)",
  "context": {
    "tool_name": "string or null (optional)",
    "tool_method": "string or null (optional)",
    "resource_location": "string or null (optional)"
  },
  "labels": {
    "action": "read|write|update|delete|execute|export (required)",
    "resource_type": "database|storage|api|queue|cache|null (required)",
    "sensitivity": "public|internal|secret|null (required)"
  },
  "source": "openapi-spec|toolbench|api-bank|synthetic|manual (required)",
  "source_detail": "string (required)",
  "reviewed": "boolean (required)"
}
```

---

## Field Specifications

### id
**Type**: string (UUID v4)
**Required**: Yes
**Description**: Unique identifier for the example. Must be a valid UUID v4.

**Generation**:
```python
import uuid
id = str(uuid.uuid4())  # e.g., "550e8400-e29b-41d4-a716-446655440000"
```

**Validation**:
- Must be valid UUID v4 format
- Must be unique across entire dataset (no duplicates)

---

### raw_text
**Type**: string
**Required**: Yes
**Constraints**:
- Non-empty (length > 0)
- Length ≤ 1000 characters (reasonable description length)
- Should be natural language (readable)

**Description**: The input text that will be classified. This is what the BERT classifier will learn to label.

**Examples**:
- "fetch all users from the database"
- "create a new issue in the repository"
- "invoke payment webhook"
- "list files in s3 bucket"

**Guidelines**:
- Write in natural language
- Describe the operation being performed
- Include relevant context (tool, resource, etc.)
- Avoid unnecessary punctuation
- Be concise but descriptive

---

### context.tool_name
**Type**: string or null
**Required**: No
**Description**: The name of the tool or API being invoked.

**Examples**:
- "github-api"
- "stripe-api"
- "database_query"
- "flight_search"
- null (if unknown)

**Guidelines**:
- Use lowercase with hyphens or underscores
- Match tool names from source (OpenAPI, ToolBench, etc.)
- null if not applicable or unknown

---

### context.tool_method
**Type**: string or null
**Required**: No
**Description**: The specific method or endpoint within the tool.

**Examples**:
- "GET /repos/{owner}/{repo}/issues"
- "POST /charges"
- "query"
- "select"
- null (if unknown)

**Guidelines**:
- For REST APIs: HTTP verb + path
- For RPC/SQL: method name or operation
- null if not applicable or unknown

---

### context.resource_location
**Type**: string or null
**Required**: No
**Description**: The location or deployment type of the resource being accessed.

**Examples**:
- "cloud"
- "local"
- "hybrid"
- "postgres-prod-us-east-1"
- null (if unknown)

**Guidelines**:
- Generic: "cloud", "local", "hybrid"
- Specific: database instance name, region, etc.
- null if not determinable from context

---

### labels.action
**Type**: string (enum)
**Required**: Yes
**Allowed values**: "read", "write", "update", "delete", "execute", "export"

**Description**: The canonical action being performed.

**Guidelines**:
- Use values from the canonical set exactly
- Refer to VOCABULARY.md for decision rules
- Never use synonyms; always map to canonical value
- Cannot be null

---

### labels.resource_type
**Type**: string (enum) or null
**Required**: Yes
**Allowed values**: "database", "storage", "api", "queue", "cache", or null

**Description**: The canonical resource type being accessed.

**Guidelines**:
- Use values from canonical set
- null if cannot be determined from context
- Refer to VOCABULARY.md for inference rules

---

### labels.sensitivity
**Type**: string (enum) or null
**Required**: Yes
**Allowed values**: "public", "internal", "secret", or null

**Description**: The expected sensitivity level of data being accessed.

**Guidelines**:
- Use canonical values
- null if cannot be determined
- Prefer "secret" if uncertain (conservative)
- Refer to VOCABULARY.md for inference rules

---

### source
**Type**: string (enum)
**Required**: Yes
**Allowed values**: "openapi-spec", "toolbench", "api-bank", "synthetic", "manual"

**Description**: The origin of this example.

**Usage**:
- "openapi-spec": Extracted from OpenAPI specifications
- "toolbench": From ToolBench dataset
- "api-bank": From API-Bank dataset
- "synthetic": Synthetically generated variation
- "manual": Manually curated by expert

---

### source_detail
**Type**: string
**Required**: Yes
**Description**: Details about the specific source (e.g., which API, which dataset version).

**Examples**:
- "stripe-api-v2024"
- "github-rest-api"
- "toolbench-2024-01"
- "api-bank-v1"
- "manual-security-expert"
- "synthetic-read-variations"

**Guidelines**:
- Specific enough to trace back to origin
- Include version/date if applicable
- Helps with traceability and deduplication

---

### reviewed
**Type**: boolean
**Required**: Yes
**Description**: Whether this example has been manually reviewed and confirmed.

**Meaning**:
- `true`: Example has been human-reviewed and is high-confidence
- `false`: Example is auto-generated or not yet reviewed

**Guidelines**:
- Start with `false` for extracted/generated examples
- Set to `true` after manual review
- High-confidence manual curation should start as `true`
- Production training may prioritize `reviewed: true` examples

---

## Validation Rules

### Structural Validation

1. **Valid JSON**: Must be valid JSON on single line
   ```
   ✓ {"id": "...", "raw_text": "...", ...}
   ✗ {"id": "...", 
        "raw_text": "..." }  // Line breaks not allowed in JSONL
   ```

2. **All required fields present**:
   ```
   Required: id, raw_text, labels.action, labels.resource_type, 
             labels.sensitivity, source, source_detail, reviewed
   ```

3. **No extra top-level fields**: Validator may warn on unexpected fields

### Field Validation

**id**:
- Must be valid UUID v4
- Must be unique (no duplicates in file)
- Example valid: "550e8400-e29b-41d4-a716-446655440000"

**raw_text**:
- Length > 0
- Length ≤ 1000
- Must be a string
- Example invalid: "", "   " (empty/whitespace)

**context**:
- If present, must be an object
- All internal fields optional
- Example valid: {} or null or {"tool_name": "foo"}

**labels.action**:
- Must be one of: "read", "write", "update", "delete", "execute", "export"
- Never null
- Case-sensitive
- Example invalid: "READ", "retrieve", null

**labels.resource_type**:
- Must be one of: "database", "storage", "api", "queue", "cache", null
- Can be null
- Case-sensitive
- Example invalid: "Database", "db", "REST"

**labels.sensitivity**:
- Must be one of: "public", "internal", "secret", null
- Can be null
- Case-sensitive
- Example invalid: "SECRET", "classified", "restricted"

**source**:
- Must be one of: "openapi-spec", "toolbench", "api-bank", "synthetic", "manual"
- Case-sensitive
- Example invalid: "OpenAPI-Spec", "open-api-spec"

**source_detail**:
- Must be a non-empty string
- No special requirements
- Example invalid: "", null

**reviewed**:
- Must be boolean (true or false)
- Example invalid: "true", 1, "false"

### Semantic Validation

1. **Consistency**: If source is "manual", `reviewed` is often true
2. **Completeness**: Neither null labels are ideal (but allowed)
3. **Quality**: raw_text should be meaningful (not "foo", "test", "x")

---

## Valid Examples

### Example 1: Complete Example (High Confidence)

```json
{"id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "raw_text": "fetch all users from the database", "context": {"tool_name": "database-api", "tool_method": "query", "resource_location": "postgres-prod"}, "labels": {"action": "read", "resource_type": "database", "sensitivity": "internal"}, "source": "openapi-spec", "source_detail": "postgres-rest-api-v2024", "reviewed": true}
```

**Why valid**:
- Valid UUID
- Clear, non-empty raw_text
- All context fields present and reasonable
- All labels set to canonical values
- Clear source and source_detail
- reviewed: true indicates human validation

---

### Example 2: Minimal Example (Auto-Generated)

```json
{"id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", "raw_text": "create new payment transaction", "context": {}, "labels": {"action": "write", "resource_type": null, "sensitivity": "secret"}, "source": "synthetic", "source_detail": "synthetic-payment-variations", "reviewed": false}
```

**Why valid**:
- Valid UUID
- Valid raw_text
- Context is empty object (all fields optional)
- Some labels can be null
- Clearly marked as not reviewed
- Synthetic source appropriate

---

### Example 3: Ambiguous but Valid

```json
{"id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e", "raw_text": "process the data", "context": {"tool_name": null, "tool_method": null, "resource_location": null}, "labels": {"action": "execute", "resource_type": null, "sensitivity": null}, "source": "toolbench", "source_detail": "toolbench-2024-01-generic", "reviewed": false}
```

**Why valid** (even with ambiguity):
- All fields properly formatted
- Action is not null (best effort)
- Null values allowed for context and some labels
- Honest about ambiguity (not reviewed)

---

## Invalid Examples

### Example 1: Invalid JSON

```
❌ {"id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "raw_text": "fetch users"
```
**Error**: Not complete JSON (missing closing brace)

---

### Example 2: Invalid UUID

```json
❌ {"id": "not-a-uuid", "raw_text": "fetch users", ...}
```
**Error**: id is not a valid UUID v4

---

### Example 3: Empty raw_text

```json
❌ {"id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "raw_text": "", ...}
```
**Error**: raw_text cannot be empty

---

### Example 4: Invalid action value

```json
❌ {"id": "...", ..., "labels": {"action": "retrieve", ...}}
```
**Error**: "retrieve" is not canonical. Must use "read"

---

### Example 5: Null action

```json
❌ {"id": "...", ..., "labels": {"action": null, "resource_type": "database", ...}}
```
**Error**: action cannot be null. Must always be specified.

---

### Example 6: Invalid resource_type value

```json
❌ {"id": "...", ..., "labels": {"action": "read", "resource_type": "db", ...}}
```
**Error**: "db" is not canonical. Must use "database" or null.

---

### Example 7: Invalid source

```json
❌ {"id": "...", ..., "source": "manual-curation"}
```
**Error**: source must be exactly "manual", not "manual-curation"

---

### Example 8: reviewed as string instead of boolean

```json
❌ {"id": "...", ..., "reviewed": "true"}
```
**Error**: reviewed must be boolean true, not string "true"

---

### Example 9: Missing required field

```json
❌ {"id": "...", "raw_text": "fetch users", ...}
// Missing: context, labels, source, source_detail, reviewed
```
**Error**: Multiple required fields missing

---

### Example 10: Multi-line (violates JSONL)

```
❌ {"id": "...",
    "raw_text": "fetch users",
    ...
}
```
**Error**: JSONL requires one JSON object per line

---

## Quality Metrics

### Expected Statistics

After generating a reasonable seed dataset, you should see:

**Action distribution** (relatively balanced):
```
read:    ~35-40%  (most operations are reads)
write:   ~20-25%  (many creation operations)
update:  ~15-20%  (update is common)
delete:  ~5-10%   (deletes are less common)
execute: ~10-15%  (function calls)
export:  ~5-10%   (data extractions)
```

**Resource type distribution** (if balanced):
```
api:       ~35%   (most common)
database:  ~25%   (structured data)
storage:   ~20%   (files/blobs)
queue:     ~10%   (messaging)
cache:     ~5%    (caching)
null:      ~5%    (unknown/ambiguous)
```

**Sensitivity distribution**:
```
public:    ~20%   (public APIs, data)
internal:  ~50%   (company data, auth required)
secret:    ~15%   (PII, credentials, financial)
null:      ~15%   (ambiguous, unknown)
```

**Source distribution** (after combining all):
```
openapi-spec: ~30%
toolbench:    ~20%
api-bank:     ~20%
synthetic:    ~20%
manual:       ~10%
```

**Review status**:
```
reviewed: true   ~15-25%  (manually validated)
reviewed: false  ~75-85%  (auto-extracted, pending review)
```

### Using validate_examples.py

Run validation to check:
```bash
python scripts/validate_examples.py data/seed/my_dataset.jsonl
```

Output shows:
- ✓ Total valid examples
- ✗ Validation errors with line numbers
- Summary statistics
- Warnings for potential issues

### Using category_stats.py

Check distribution:
```bash
python scripts/category_stats.py data/seed/my_dataset.jsonl
```

Output shows:
- Count per action label
- Count per resource_type label
- Count per sensitivity label
- Percentage balance
- Warnings for underrepresented categories

---

## Tips for Quality Output

1. **Validate early**: Run validate_examples.py after each batch to catch errors early

2. **Check distribution**: Run category_stats.py to ensure stratification

3. **Mix sources**: Don't rely on single source; combine for diversity

4. **Review selectively**: Mark ~10-20% as `reviewed: true` (high-confidence examples)

5. **Handle nulls carefully**: 
   - Never null on action (always label if possible)
   - Null on resource_type/sensitivity is OK if ambiguous
   - Prefer inferring over null

6. **Deduplicate**: Remove exact duplicates on raw_text before combining batches

7. **Clean text**: 
   - Normalize whitespace
   - Remove redundant punctuation
   - Keep text concise (under 200 chars ideal)

8. **Document sources**: Use source_detail for complete traceability

9. **Batch size**: Generate 100-500 examples per batch for easier tracking

10. **Combine carefully**:
    ```bash
    # Combine all batches
    cat data/seed/*.jsonl > data/seed/combined_final.jsonl
    
    # Validate combined
    python scripts/validate_examples.py data/seed/combined_final.jsonl
    
    # Check distribution
    python scripts/category_stats.py data/seed/combined_final.jsonl
    ```

---

For labeling guidance, see [VOCABULARY.md](VOCABULARY.md).
For data source information, see [DATA_SOURCES.md](DATA_SOURCES.md).

---
name: seed-generator
description: Generate labeled training examples for the BERT canonicalization classifier. Use to collect seed data from public APIs (OpenAPI specs), LLM tool-use datasets (ToolBench, API-Bank), or create synthetic variations. Outputs stratified JSONL with action, resource_type, and sensitivity labels.
compatibility: Requires Python 3.10+, internet access for external datasets
metadata:
  author: guard-team
  version: "1.0"
---

# Seed Generator Skill

Generate labeled training examples for the canonicalization classifier. This skill enables systematic collection of seed data from diverse sources to build a robust, unbiased training dataset for BERT-based vocabulary canonicalization.

## When to Use This Skill

Use this skill when you need to:

- **Build initial seed dataset** for the BERT canonicalization classifier (~50K-100K labeled examples)
- **Collect examples from a specific source** (OpenAPI specs, ToolBench, API-Bank, synthetic generation)
- **Maintain stratified distribution** across canonical labels (action, resource_type, sensitivity)
- **Generate synthetic variations** to handle uncommon synonyms and context variations
- **Validate and curate** examples before adding to the training set

## Overview: The 5 Data Sources

The skill leverages five complementary sources to build an unbiased, comprehensive seed dataset:

| Source | Weight | Count | Best For |
|--------|--------|-------|----------|
| OpenAPI Specs | 30% | ~15K examples | Real-world API patterns, diverse vocabularies |
| ToolBench | 20% | ~10K examples | LLM tool-use instructions, agent patterns |
| API-Bank | 20% | ~10K examples | API calling patterns in dialogue context |
| Synthetic Variations | 20% | ~10K examples | Synonyms, context variations, edge cases |
| Manual Curation | 10% | ~5K examples | Domain expertise, corner cases, ambiguities |

## Workflow: Step-by-Step Process

## Agent Workflow Options

When using this skill, you have two approaches for generating labeled examples:

### Option A: Script-Assisted Generation (Recommended for Agents)

Use `fetch_openapi.py` to extract raw examples, then apply labels in a second pass.

**Steps:**
1. Run: `python scripts/fetch_openapi.py <spec_url> --output examples_<datetime>.jsonl`
2. The script outputs examples with `labels: {action: null, resource_type: null, sensitivity: null}`
3. Run: `python scripts/label_inplace.py examples_<datetime>.jsonl.jsonl`
4. Review low-confidence labels and adjust using VOCABULARY.md
5. Update any remaining labels and keep the file as your final output

**Note:** This approach requires two passes, but standardizes spec fetching and parsing.

### Option B: Direct Generation

Generate JSONL examples directly without using the helper scripts. Use this when you cannot run the scripts or need tighter control over extraction.

**Steps:**
1. Fetch the OpenAPI spec using web fetch tools
2. Parse the JSON/YAML to extract operations
3. For each operation:
   - Generate `raw_text` from the summary/description
   - Apply labeling rules from VOCABULARY.md to determine action, resource_type, sensitivity
   - Create the complete JSONL entry with all fields populated
4. Output valid JSONL

**Example - Complete workflow for one operation:**

```json
// Input: GitHub API operation
{
  "path": "/repos/{owner}/{repo}/issues",
  "method": "POST",
  "summary": "Create an issue",
  "description": "Creates a new issue in the specified repository"
}

// Step 1: Generate raw_text
"create an issue in the specified repository"

// Step 2: Apply labeling rules (from VOCABULARY.md)
// - "create" keyword → action: "write"
// - External API endpoint → resource_type: "api"
// - "issue" is project data, not PII → sensitivity: "internal"

// Step 3: Output complete JSONL entry
{"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "raw_text": "create an issue in the specified repository", "context": {"tool_name": "github-api", "tool_method": "POST /repos/{owner}/{repo}/issues", "resource_location": null}, "labels": {"action": "write", "resource_type": "api", "sensitivity": "internal"}, "source": "openapi-spec", "source_detail": "github-rest-api-v2024", "reviewed": false}
```

### Step 1: Choose Your Data Source

Decide which source to target:

```
If you want:                          → Choose:
Real-world API operations            → OpenAPI Specs (Stripe, GitHub, AWS, etc.)
LLM tool-use patterns                → ToolBench dataset
API calling in dialogue               → API-Bank dataset
Cover edge cases & synonyms          → Synthetic generation
High-confidence baseline              → Manual curation
```

For a complete seed dataset, you'll cycle through all sources.

### Step 2: Extract Raw Text + Context

Extract the relevant information from your chosen source.

**From OpenAPI specs:**
```
operation_verb: "POST"
operation_path: "/repositories/{id}/issues"
description: "Create a new issue in the repository"

Raw text to label: "create a new issue in the repository"
Context: {
  "tool_name": "github-api",
  "tool_method": "POST /repositories/{id}/issues"
}
```

**From ToolBench:**
```
instruction: "retrieve all users from the customer database"
available_functions: [...]

Raw text to label: "retrieve all users from the customer database"
Context: {
  "tool_name": (inferred from available functions)
}
```

**From API-Bank:**
```
user_utterance: "show me all active accounts"
ai_response: "[GetAccounts(status='active')]"

Raw text to label: "show me all active accounts" + "active accounts"
Context: {
  "tool_name": "GetAccounts"
}
```

**From Synthetic Generation:**
Generate variations of canonical examples using templates:
```
Base example: "read user data from the database"
Variations:
- "fetch user data from postgres"
- "query the users table"
- "retrieve user records"
- "select all users"
```

### Step 3: Apply Labeling Rules

Use the detailed labeling rules in [VOCABULARY.md](references/VOCABULARY.md) to assign canonical labels.

**Three fields to label:**

1. **action** - What operation is being performed?
   - `read`: Retrieve/access data without modification
   - `write`: Create new data
   - `update`: Modify existing data
   - `delete`: Remove data
   - `execute`: Run functions/processes
   - `export`: Extract data to external destination

2. **resource_type** - What kind of resource is being accessed?
   - `database`: SQL, NoSQL, structured data stores
   - `storage`: Files, blobs, object storage (S3, GCS, etc.)
   - `api`: External service endpoints
   - `queue`: Message queues (SQS, Kafka, etc.)
   - `cache`: Caching systems (Redis, Memcached)
   - `null`: Unknown or context-dependent

3. **sensitivity** - How sensitive is the data likely to be?
   - `public`: Publicly accessible data
   - `internal`: Organization-only data
   - `secret`: Highly sensitive data (PII, credentials, etc.)
   - `null`: Cannot be determined from context

**Decision Tree Examples:**

```
Q: "query the users table"
├─ Action: "query" → "read" (reading data)
├─ Resource: "users table" + "table" keyword → "database"
└─ Sensitivity: "users" (personal data) → "internal"

Q: "upsert records in mongodb"
├─ Action: "upsert" (create or update) → "write" (treat as write)
├─ Resource: "mongodb" → "database"
└─ Sensitivity: "records" (unknown type) → "null" or "internal" if PII-like

Q: "invoke payment webhook"
├─ Action: "invoke" (trigger execution) → "execute"
├─ Resource: "webhook" (external) → "api"
└─ Sensitivity: "payment" (sensitive) → "secret"

Q: "list files in s3 bucket"
├─ Action: "list" → "read"
├─ Resource: "s3 bucket" → "storage"
└─ Sensitivity: depends on bucket content → "null" or infer from bucket name
```

See [VOCABULARY.md](references/VOCABULARY.md) for complete labeling rules and ambiguous case handling.

### Step 4: Generate JSONL Output

Format each labeled example as JSON and append to JSONL file (one JSON object per line).

**Required schema:**

```json
{
  "id": "unique-uuid-v4",
  "raw_text": "the raw text to classify",
  "context": {
    "tool_name": "string or null",
    "tool_method": "string or null",
    "resource_location": "string or null"
  },
  "labels": {
    "action": "read|write|update|delete|execute|export",
    "resource_type": "database|storage|api|queue|cache|null",
    "sensitivity": "public|internal|secret|null"
  },
  "source": "openapi-spec|toolbench|api-bank|synthetic|manual",
  "source_detail": "stripe-api-v2024 or toolbench-2024-01 etc.",
  "reviewed": false
}
```

**Example valid entries:**

```json
{"id": "seed-001", "raw_text": "fetch all users from postgres", "context": {"tool_name": "database_query", "tool_method": "query", "resource_location": null}, "labels": {"action": "read", "resource_type": "database", "sensitivity": "internal"}, "source": "openapi-spec", "source_detail": "postgres-rest-api", "reviewed": false}

{"id": "seed-002", "raw_text": "create new payment transaction", "context": {"tool_name": "stripe-api", "tool_method": "POST /charges", "resource_location": null}, "labels": {"action": "write", "resource_type": "api", "sensitivity": "secret"}, "source": "openapi-spec", "source_detail": "stripe-api-v2024", "reviewed": false}

{"id": "seed-003", "raw_text": "list all active subscriptions", "context": {"tool_name": null, "tool_method": null, "resource_location": null}, "labels": {"action": "read", "resource_type": null, "sensitivity": null}, "source": "toolbench", "source_detail": "toolbench-2024-01", "reviewed": false}
```

See [OUTPUT_FORMAT.md](references/OUTPUT_FORMAT.md) for complete schema validation rules.

### Step 5: Validate & Stratify

Use the provided Python scripts to validate and analyze your generated dataset:

**Validate examples:**
```bash
python scripts/validate_examples.py data/seed/my_examples.jsonl
```

This checks:
- ✓ Valid JSON format (one object per line)
- ✓ All required fields present
- ✓ Label values are canonical
- ✓ No duplicate IDs
- ✓ No empty raw_text

**Check category distribution:**
```bash
python scripts/category_stats.py data/seed/my_examples.jsonl
```

Output shows distribution across all categories. **Target**: roughly equal examples per canonical label (~8-10% per action, ~20% per resource_type, ~33% per sensitivity).

### Step 6: Human Review & Marking

After validation, review flagged examples:

- **Ambiguous labels**: Examples with multiple valid interpretations
- **Edge cases**: Examples at category boundaries
- **Low confidence**: Examples where the label is uncertain

Mark reviewed examples by updating the `reviewed` field to `true`:

```json
{"id": "seed-001", ..., "reviewed": true}
```

Reviewed examples become part of the high-confidence baseline for model training.

## Detailed Labeling Rules

See [VOCABULARY.md](references/VOCABULARY.md) for:
- Complete canonical vocabulary
- Explicit edge case rules (upsert, query, backup, etc.)
- Resource type inference from tool names
- Sensitivity inference from keywords
- Examples for each category

## Data Source Guides

See [DATA_SOURCES.md](references/DATA_SOURCES.md) for:
- How to access each source (URLs, credentials)
- Parsing instructions for each format
- Example extraction walkthroughs
- Tips for handling each source efficiently

## Output Format Reference

See [OUTPUT_FORMAT.md](references/OUTPUT_FORMAT.md) for:
- Complete JSONL schema
- Validation rules
- Valid/invalid examples
- Tips for quality examples

## Practical Examples

### Example 1: Generate from OpenAPI Specs

```
Goal: Generate 200 examples from the GitHub API

Steps:
1. Access GitHub OpenAPI spec (see DATA_SOURCES.md)
2. Extract operation verbs and descriptions:
   - GET /repos/{owner}/{repo}/issues → "retrieve repository issues"
   - POST /repos/{owner}/{repo}/issues → "create a new issue"
   - PATCH /repos/{owner}/{repo}/issues/{issue_number} → "update an issue"
   - DELETE /repos/{owner}/{repo}/issues/{issue_number} → "delete an issue"
3. Apply labeling rules:
   - GET → action: "read"
   - POST → action: "write"
   - PATCH → action: "update"
   - DELETE → action: "delete"
   - /repos → resource_type: "api"
4. Generate JSONL with 200 entries (stratified)
5. Validate with: python scripts/validate_examples.py
6. Check distribution with: python scripts/category_stats.py
7. Output: data/seed/github_api_200.jsonl
```

### Example 2: Generate Synthetic Variations

```
Goal: Create 100 synthetic variations to cover edge cases

Base examples (manually curated):
- "read data from database"
- "write data to file storage"
- "delete old records"

Variations for "read":
- "query the database"
- "fetch data from postgres"
- "retrieve user records"
- "select all items"
- "search the index"
- "lookup customer info"

Generate 5 variations per base example → 15 synthetic examples per base
With ~6-7 carefully selected bases → ~100 synthetic variations
```

### Example 3: Combine Sources for Balanced Dataset

```
Target: 50K total examples with balanced distribution

Plan:
- OpenAPI specs: 15K (30%)
  - 3K per source: Stripe, GitHub, AWS, Google Cloud, Twilio
- ToolBench: 10K (20%)
- API-Bank: 10K (20%)
- Synthetic: 10K (20%)
- Manual curation: 5K (10%)

Process:
1. Generate from each source separately
2. Use category_stats.py after each batch to track distribution
3. Adjust subsequent batches to balance underrepresented categories
4. Combine all outputs: cat data/seed/*.jsonl > data/seed/combined_50k.jsonl
5. Final validation: python scripts/validate_examples.py data/seed/combined_50k.jsonl
```

## Complete Worked Example: GitHub API

This example shows the full workflow from fetching a spec to outputting labeled examples.

### Step 1: Fetch the OpenAPI Spec

Fetch from: `https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json`

### Step 2: Extract 5 Operations

From the spec, extract operations like:

| Method | Path | Summary |
|--------|------|---------|
| GET | /repos/{owner}/{repo}/issues | List repository issues |
| POST | /repos/{owner}/{repo}/issues | Create an issue |
| PATCH | /repos/{owner}/{repo}/issues/{issue_number} | Update an issue |
| DELETE | /repos/{owner}/{repo}/issues/{issue_number}/lock | Unlock an issue |
| GET | /user | Get the authenticated user |

### Step 3: Apply Labeling Rules

For each operation, apply the decision trees from VOCABULARY.md:

**Example 1: GET /repos/{owner}/{repo}/issues**
- Raw text: "list repository issues"
- Action: "list" → **read** (retrieval operation)
- Resource: GitHub API endpoint → **api**
- Sensitivity: "issues" are project data → **internal**

**Example 2: POST /repos/{owner}/{repo}/issues**
- Raw text: "create an issue"
- Action: "create" → **write** (creating new data)
- Resource: GitHub API endpoint → **api**
- Sensitivity: "issue" is project data → **internal**

**Example 3: GET /user**
- Raw text: "get the authenticated user"
- Action: "get" → **read**
- Resource: GitHub API endpoint → **api**
- Sensitivity: "authenticated user" contains user info → **secret**

### Step 4: Generate JSONL Output

```jsonl
{"id": "gh-001", "raw_text": "list repository issues", "context": {"tool_name": "github-api", "tool_method": "GET /repos/{owner}/{repo}/issues", "resource_location": null}, "labels": {"action": "read", "resource_type": "api", "sensitivity": "internal"}, "source": "openapi-spec", "source_detail": "github-rest-api-2024", "reviewed": false}
{"id": "gh-002", "raw_text": "create an issue", "context": {"tool_name": "github-api", "tool_method": "POST /repos/{owner}/{repo}/issues", "resource_location": null}, "labels": {"action": "write", "resource_type": "api", "sensitivity": "internal"}, "source": "openapi-spec", "source_detail": "github-rest-api-2024", "reviewed": false}
{"id": "gh-003", "raw_text": "get the authenticated user", "context": {"tool_name": "github-api", "tool_method": "GET /user", "resource_location": null}, "labels": {"action": "read", "resource_type": "api", "sensitivity": "secret"}, "source": "openapi-spec", "source_detail": "github-rest-api-2024", "reviewed": false}
```

### Step 5: Validate

Run validation to check your output:
```bash
python scripts/validate_examples.py data/seed/github_examples.jsonl
```

## Quality Checklist

Before outputting your seed dataset, ensure:

- [ ] All examples have valid JSON format
- [ ] No duplicate IDs across the entire dataset
- [ ] `raw_text` is non-empty and meaningful
- [ ] Labels use only canonical values (from VOCABULARY.md)
- [ ] Distribution is roughly stratified (check with category_stats.py)
- [ ] At least 10% of examples have been manually reviewed
- [ ] No sensitive data (API keys, credentials) in raw_text
- [ ] Each example has proper source attribution
- [ ] Output is stored in `data/seed/` directory

## Helpful Scripts

The skill includes three helper scripts:

### validate_examples.py
```bash
python scripts/validate_examples.py <jsonl_file>
```
Validates each example in JSONL file. Reports:
- Schema validation errors
- Invalid label values
- Duplicate IDs
- Empty fields
- Summary statistics

### category_stats.py
```bash
python scripts/category_stats.py <jsonl_file>
```
Analyzes category distribution. Reports:
- Count per action label
- Count per resource_type label
- Count per sensitivity label
- Percentage balance
- Warnings for underrepresented categories

### fetch_openapi.py
```bash
python scripts/fetch_openapi.py <spec_url> [--output <output_file>]
```
Fetches and parses OpenAPI spec. Extracts:
- Operation verbs (GET, POST, PUT, DELETE, PATCH)
- Endpoint paths
- Operation descriptions
- Parameter information
Outputs raw text examples ready for labeling.

### label_inplace.py
```bash
python scripts/label_inplace.py <jsonl_file> [--dry-run] [--backup] [--overwrite]
```
Applies heuristic labeling rules in-place using `raw_text` and `context` fields.
Prints low-confidence warnings so you can review and adjust before validation.

See individual scripts for detailed usage.

## Tips & Best Practices

1. **Start small**: Generate 100-200 examples from one source first to get the feel for labeling rules
2. **Use scripts early**: Run validate_examples.py frequently during generation to catch errors early
3. **Check distribution**: Run category_stats.py after each batch to ensure stratification
4. **Mix sources**: Don't rely on a single source; diversity prevents overfitting to one API style
5. **Trust the vocabulary**: When in doubt, refer back to VOCABULARY.md labeling rules
6. **Mark reviews**: Always update `reviewed: true` when you manually curate an example
7. **Batch output**: Generate 100-500 examples per batch for easier review and tracking
8. **Document sources**: Keep `source` and `source_detail` fields accurate for traceability

## Next Steps

After generating your seed dataset:

1. **Combine batches**: Merge all JSONL files into single dataset
2. **Final validation**: Run full validation and distribution check
3. **Create train/val/test split**: Use 80/10/10 split for training
4. **Train BERT classifier**: Use output as training data for canonicalization model
5. **Production logging**: Monitor model on real intents, iterate with Phase 2 learning loop

---

For implementation details, see the documentation in the `references/` folder.

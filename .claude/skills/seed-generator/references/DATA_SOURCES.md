# Data Sources Guide

This reference document explains how to access and extract examples from each of the five data sources: OpenAPI Specs, ToolBench, API-Bank, Synthetic Variations, and Manual Curation.

## Table of Contents

1. [OpenAPI Specifications (30%)](#openapi-specifications-30)
2. [ToolBench Dataset (20%)](#toolbench-dataset-20)
3. [API-Bank Dataset (20%)](#api-bank-dataset-20)
4. [Synthetic Variations (20%)](#synthetic-variations-20)
5. [Manual Curation (10%)](#manual-curation-10)
6. [Source Prioritization](#source-prioritization)

---

## OpenAPI Specifications (30%)

**Target**: 15,000 examples from diverse public APIs

OpenAPI specs provide standardized, real-world API patterns from established services. Each spec contains operations (HTTP verbs) with descriptions that can be labeled.

### Accessing OpenAPI Specs

Popular APIs with publicly available OpenAPI specs:

| API | Spec URL | Operations | Notes |
|-----|----------|-----------|-------|
| **Stripe** | `https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json` | ~300 endpoints | Payment operations, comprehensive |
| **GitHub** | `https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json` | ~900 endpoints | Repository, issue, PR operations |
| **Twilio** | `https://raw.githubusercontent.com/twilio/twilio-oai/main/spec/json/twilio_api_v2010.json` | ~200 endpoints | Communications APIs |
| **PetStore (Demo)** | `https://petstore3.swagger.io/api/v3/openapi.json` | ~20 endpoints | Good for testing workflow |

**Note on AWS and Google Cloud:**
- AWS and Google Cloud provide per-service OpenAPI specs, not a single consolidated spec
- For AWS, find service-specific specs at: `https://github.com/aws/aws-sdk-js-v3/tree/main/codegen/sdk-codegen/aws-models`
- For Google Cloud, APIs are documented at: `https://github.com/googleapis/googleapis`
- Consider using 2-3 specific service specs (e.g., AWS S3, AWS Lambda) rather than attempting full coverage

### Extracting Examples from OpenAPI

**Format**: OpenAPI specs typically have this structure:

```json
{
  "openapi": "3.0.0",
  "paths": {
    "/repositories/{id}/issues": {
      "get": {
        "operationId": "list-issues",
        "summary": "List issues in a repository",
        "description": "Returns paginated list of issues...",
        "parameters": [...]
      },
      "post": {
        "operationId": "create-issue",
        "summary": "Create an issue",
        "description": "Creates a new issue in the repository...",
        "requestBody": {...}
      }
    }
  }
}
```

**Extraction process**:

1. **Find the spec URL** (often in GitHub repo or docs site)
2. **Download or fetch the spec** (usually JSON or YAML)
3. **Iterate through `paths`**:
   - For each path (e.g., `/repositories/{id}/issues`)
   - For each HTTP method (GET, POST, PUT, DELETE, PATCH)
4. **Extract for each operation**:
   - HTTP verb: GET, POST, PATCH, DELETE
   - Path: `/repositories/{id}/issues`
   - Description or summary text
5. **Generate raw text**:
   ```
   action_verb = HTTP method to action mapping:
     GET → "read" / "retrieve" / "fetch" / "list"
     POST → "create" / "add" / "insert"
     PUT/PATCH → "update" / "modify"
     DELETE → "delete" / "remove"
   
   raw_text = f"{action_verb} {description}"
   Example: "list issues in a repository"
   ```

**Example extraction from GitHub API**:

```
Operation: GET /repos/{owner}/{repo}/issues
Summary: List repository issues
Description: Returns paginated list of issues...

Generated raw_text: "list issues in a repository"
Context: {
  "tool_name": "github-api",
  "tool_method": "GET /repos/{owner}/{repo}/issues"
}
Labels: {
  "action": "read",
  "resource_type": "api",
  "sensitivity": "internal"
}
```

### Tips for OpenAPI Extraction

1. **Start with small APIs** (Twilio, PagerDuty) with ~50-100 endpoints
2. **Use tools**: JSON parsing tools (jq, Python json module) to automate extraction
3. **Handle path parameters**: `/repos/{id}/issues` is one endpoint (id is parameter)
4. **Combine summary + description**: Use both for richer context
5. **Skip webhooks**: Webhook endpoints are less useful (often duplicates)
6. **Watch for duplicates**: Same operation may appear with different paths

---

## ToolBench Dataset (20%)

**Target**: 10,000 examples from LLM tool-use scenarios

ToolBench is a large-scale benchmark for tool-use in LLMs. It contains 1.6M multi-turn dialogues with tool use.

### Accessing ToolBench

**Repository**: https://github.com/OpenBMB/ToolBench

**Download options**:

Option 1: Clone the repository
```bash
git clone https://github.com/OpenBMB/ToolBench.git
cd ToolBench
# Dataset files in: data/
```

Option 2: Download from Hugging Face
```
# Check Hugging Face repo: https://huggingface.co/OpenBMB
# Download via: huggingface_hub library
```

### Understanding ToolBench Format

ToolBench data is organized as multi-turn dialogues:

```json
{
  "conversations": [
    {
      "user": "Can you help me search for flights?",
      "assistant": "I can help with that. Let me search for flights for you.",
      "tool_calls": [
        {
          "tool_name": "flight_search",
          "tool_input": {
            "origin": "SFO",
            "destination": "LAX",
            "date": "2024-01-15"
          }
        }
      ]
    }
  ],
  "available_tools": [
    {
      "name": "flight_search",
      "description": "Search for available flights between two airports",
      "parameters": [...]
    }
  ]
}
```

### Extracting Examples from ToolBench

**Extraction process**:

1. **Get the user instruction**: "Can you help me search for flights?"
2. **Get the tool being called**: "flight_search"
3. **Infer the action**:
   - "search" → read
   - "book" → write
   - "cancel" → delete
4. **Infer resource type** from tool name:
   - "flight_search" → api
   - "database_query" → database
   - "file_upload" → storage
5. **Generate raw_text**:
   ```
   raw_text = user instruction
   Example: "search for flights"
   ```

**Example extraction from ToolBench**:

```
Instruction: "Search for the cheapest flights from New York to Los Angeles on January 15"
Tool: flight_search
Tool input: origin=JFK, destination=LAX, date=2024-01-15

Raw text: "search for the cheapest flights from new york to los angeles"
Context: {
  "tool_name": "flight_search"
}
Labels: {
  "action": "read",
  "resource_type": "api",
  "sensitivity": "public"
}
```

### Tips for ToolBench Extraction

1. **Focus on single-tool examples**: Ignore complex multi-step dialogues initially
2. **Extract user instructions only**: Not assistant responses
3. **Clean text**: Remove punctuation, normalize whitespace
4. **Skip edge cases**: Complex instructions with multiple operations
5. **Sample strategically**: ToolBench has 1.6M examples; sample across tool categories
6. **Get tool diversity**: Sample different tool types (flight, hotel, weather, etc.)

---

## API-Bank Dataset (20%)

**Target**: 10,000 examples from API calling patterns in dialogue

API-Bank contains realistic dialogue examples with API calling patterns. Focus on the mapping between user intent and API calls.

### Accessing API-Bank

**Dataset**: Hugging Face - `liminghao1630/API-Bank`

**Access methods**:

Option 1: Use Hugging Face Datasets library
```python
from datasets import load_dataset
dataset = load_dataset("liminghao1630/API-Bank")
```

Option 2: Download from web interface
```
https://huggingface.co/datasets/liminghao1630/API-Bank
```

### Understanding API-Bank Format

API-Bank data represents dialogue-API interactions:

```json
{
  "user_utterance": "Show me all active accounts",
  "api_name": "GetAccounts",
  "api_parameters": {
    "status": "active"
  },
  "api_response": [
    {"id": 1, "name": "Account A", "status": "active"},
    {"id": 2, "name": "Account B", "status": "active"}
  ],
  "api_description": "Get list of accounts with optional filtering"
}
```

### Extracting Examples from API-Bank

**Extraction process**:

1. **Get user utterance**: "Show me all active accounts"
2. **Get API name**: "GetAccounts"
3. **Infer action** from API name and parameters:
   - GetX, ListX, ShowX → read
   - CreateX, AddX, PostX → write
   - UpdateX, PatchX, ModifyX → update
   - DeleteX, RemoveX → delete
4. **Infer resource type** from API name:
   - Contains "Account", "User", "Customer" → could be database or api
   - Look at API description for hints
5. **Generate raw_text**:
   ```
   raw_text = user utterance
   Example: "show me all active accounts"
   ```

**Example extraction from API-Bank**:

```
User utterance: "Get all healthcare providers in New York"
API: GetHealthcareProviders
Parameters: location=New York, include_details=true

Raw text: "get all healthcare providers in new york"
Context: {
  "tool_name": "GetHealthcareProviders"
}
Labels: {
  "action": "read",
  "resource_type": "api",
  "sensitivity": "internal"
}
```

### Tips for API-Bank Extraction

1. **Use the user_utterance as raw_text**: Capture natural language patterns
2. **Infer from API names**: Standard naming conventions (Get, Create, Update, Delete)
3. **Handle abbreviations**: API names may be abbreviated (Acct, Prov, etc.)
4. **Sample across domains**: Healthcare, finance, travel, e-commerce
5. **Check sensitivity from context**: API documentation sometimes indicates sensitivity
6. **Clean utterances**: Normalize text, remove personal identifiers

---

## Synthetic Variations (20%)

**Target**: 10,000 examples through template-based generation

Synthetic variations allow you to generate multiple ways of expressing the same action/resource combination, improving coverage of synonyms and phrasings.

### Creating Synthetic Variations

**Method**: Template-based generation with human review

**Process**:

1. **Select a base example** (from OpenAPI, ToolBench, or manual)
2. **Extract the semantic components**:
   - Action type
   - Resource type
   - Context
3. **Generate variations** using templates
4. **Review for quality** (human validation)

### Variation Templates by Action

#### Read Variations
```
Base: "read user data from database"

Templates:
- "retrieve {resource} from {location}"       → "retrieve user data from database"
- "fetch {resource}"                          → "fetch user data"
- "query the {resource_type}"                 → "query the users table"
- "list all {resource}s"                      → "list all users"
- "search for {resource}"                     → "search for users"
- "get {resource} information"                → "get user information"
- "look up {resource} records"                → "look up user records"
- "select {resource} entries"                 → "select user entries"
```

#### Write Variations
```
Base: "create a new user record"

Templates:
- "add {resource} to {location}"              → "add user to database"
- "create a new {resource}"                   → "create a new user"
- "insert {resource} into {location}"         → "insert user into database"
- "save {resource} data"                      → "save user data"
- "register a {resource}"                     → "register a user"
- "write {resource} information"              → "write user information"
- "submit new {resource}"                     → "submit new user"
```

#### Update Variations
```
Base: "update user profile"

Templates:
- "modify {resource}"                         → "modify user"
- "change {resource} settings"                → "change user settings"
- "edit {resource} information"               → "edit user information"
- "update {resource} details"                 → "update user details"
- "revise {resource} entry"                   → "revise user entry"
```

#### Delete Variations
```
Base: "delete old records"

Templates:
- "remove {resource}"                         → "remove old records"
- "drop {resource} from {location}"           → "drop old records from database"
- "delete {resource} entries"                 → "delete old record entries"
- "purge {resource}"                          → "purge old records"
```

### Example Synthetic Generation

**Base example**:
```
Raw text: "query users table"
Action: read
Resource: database
Sensitivity: internal
```

**Generate 10 variations**:
1. "retrieve all users"
2. "fetch user records"
3. "list users from database"
4. "search for users"
5. "query the database for users"
6. "get all user data"
7. "select users from table"
8. "lookup user information"
9. "retrieve user list"
10. "find all users"

**All receive the same labels**:
```json
{
  "action": "read",
  "resource_type": "database",
  "sensitivity": "internal"
}
```

### Tips for Synthetic Generation

1. **Start with high-confidence base examples**: Only generate from examples you're certain about
2. **Generate 5-10 variations per base**: Provides good diversity without explosion
3. **Mix paraphrasings**: Different word orders, synonyms, phrasing lengths
4. **Maintain semantic meaning**: Don't change the action or resource type
5. **Review before including**: Have a human validate each batch
6. **Track provenance**: Mark as "synthetic" in source field
7. **Don't over-generate**: 10K synthetic examples is enough; focus on diversity over quantity

---

## Manual Curation (10%)

**Target**: 5,000 examples through expert review

Manual curation captures edge cases, domain expertise, and high-confidence baseline examples.

### Manual Curation Process

**Step 1: Identify Candidate Examples**

Sources:
- Edge cases from other sources (ambiguous examples)
- Domain-specific expertise (medical, financial, etc.)
- Corner cases (rare operations, unusual combinations)
- Examples with PII/security implications (reviewed in private)

**Step 2: Label with High Confidence**

Ensure labels are:
- Unambiguous
- Well-documented
- Reviewed by domain expert (if applicable)

**Step 3: Add to Seed Dataset**

Mark with `reviewed: true` to indicate high confidence:

```json
{
  "id": "manual-001",
  "raw_text": "reset user password in the authentication system",
  "context": {
    "tool_name": "auth-service",
    "tool_method": "POST /users/{id}/password/reset"
  },
  "labels": {
    "action": "update",
    "resource_type": "api",
    "sensitivity": "secret"
  },
  "source": "manual",
  "source_detail": "security-expert-review",
  "reviewed": true
}
```

### Example Manual Curation Areas

| Area | Description | Examples |
|------|-------------|----------|
| **Security operations** | Password resets, credential rotation, MFA | "reset user password", "revoke API key" |
| **Regulatory compliance** | GDPR, HIPAA, SOX | "right to be forgotten", "audit trail query" |
| **Ambiguous cases** | Operations that could be multiple actions | "backup and restore" (export vs write) |
| **Domain-specific** | Financial, medical, legal domain operations | "transfer funds", "write prescription" |
| **Error handling** | Special operations for error conditions | "rollback transaction", "retry failed operation" |

### Tips for Manual Curation

1. **Focus on high-confidence examples**: Don't include ambiguous cases here
2. **Document rationale**: Add notes for why you labeled something a certain way
3. **Get expert review**: For domain-specific examples, involve domain experts
4. **Prioritize completeness**: Ensure you cover all action/resource/sensitivity combinations
5. **Mark reviewed**: Always set `reviewed: true` for manually curated examples
6. **Set reasonable target**: 5,000 examples (10% of 50K) is manageable for human review
7. **Batch for review**: Group by domain or category for efficient expert review

---

## Source Prioritization

### Phase 1: Get to 10K Examples (Quick Start)

If you want to build a basic seed dataset quickly:

1. **Week 1**: Start with **Manual Curation** (5K examples)
   - Identify your domain's key operations
   - Curate high-confidence baseline
   
2. **Week 2**: Add **OpenAPI Specs** (3K examples)
   - Pick 3 major APIs (Stripe, GitHub, AWS)
   - Extract 1K from each
   
3. **Week 3**: Add **API-Bank** (2K examples)
   - Sample across different domains
   - Focus on realistic dialogue patterns

**Result**: 10K diverse examples in 3 weeks

### Phase 2: Scale to 50K Examples

Once you have initial seed dataset working:

1. **Expand OpenAPI** (→ 15K total)
   - Add more APIs (Google Cloud, Slack, Twilio, PagerDuty)
   - Deeper extraction from existing APIs
   
2. **Expand ToolBench** (→ 10K total)
   - Sample more diverse tool types
   - Include more dialogue variations
   
3. **Expand API-Bank** (→ 10K total)
   - More domains and use cases
   
4. **Synthetic Variations** (→ 10K total)
   - Generate based on identified gaps in coverage
   
5. **Finalize Manual Curation** (→ 5K total)
   - Edge cases, security operations, regulatory compliance

**Result**: 50K-100K balanced, stratified dataset

### Balancing Across Sources

Use `category_stats.py` to monitor distribution:

```bash
# After each batch:
python scripts/category_stats.py data/seed/batch_1.jsonl

# Combine and check overall:
cat data/seed/*.jsonl > data/seed/combined.jsonl
python scripts/category_stats.py data/seed/combined.jsonl
```

Adjust subsequent batches to balance underrepresented categories:
- If "execute" action is rare → prioritize execute examples in next batch
- If "database" resource is overrepresented → balance with more api/storage
- If "secret" sensitivity is underrepresented → add security/payment examples

---

## Quality Assurance

After extracting from any source, validate with:

```bash
python scripts/validate_examples.py data/seed/source_examples.jsonl
```

This checks:
- ✓ Valid JSON format
- ✓ Required fields present
- ✓ Label values are canonical
- ✓ No duplicate IDs
- ✓ No empty raw_text fields

Fix any errors before combining with other sources.

---

## Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Spec/dataset access blocked | Check authentication, use free tier/public versions |
| Extraction too slow | Automate with Python scripts, batch process |
| Labels inconsistent | Use VOCABULARY.md decision trees, apply consistently |
| Distribution imbalanced | Use category_stats.py, adjust batch selections |
| Too many duplicates | Deduplicate on raw_text, keep unique examples |
| Low quality examples | Validate early with scripts, discard low-confidence |

---

For detailed labeling guidance, see [VOCABULARY.md](VOCABULARY.md).
For output format requirements, see [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md).

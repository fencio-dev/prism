# Seed Generator Skill

A comprehensive Agent Skill for generating labeled training examples for the BERT canonicalization classifier in Guard's Phase 1 data collection pipeline.

## Quick Start

This skill helps you systematically collect seed data from 5 diverse sources (OpenAPI specs, ToolBench, API-Bank, synthetic variations, and manual curation) to build a robust training dataset for vocabulary canonicalization.

### What This Skill Does

- **Provides clear workflow** for labeling examples with canonical terms (action, resource_type, sensitivity)
- **Documents 5 data sources** with access instructions and extraction patterns
- **Includes detailed labeling rules** covering edge cases and ambiguous scenarios
- **Bundles Python scripts** for validation, distribution analysis, and OpenAPI parsing
- **Tracks review status** for quality assurance of training data

### File Structure

```
seed-generator/
├── SKILL.md                     # Main instructions (start here)
├── README.md                    # This file
├── references/
│   ├── VOCABULARY.md            # Canonical vocabulary & labeling rules
│   ├── DATA_SOURCES.md          # How to access & extract from 5 sources
│   └── OUTPUT_FORMAT.md         # JSONL schema & validation rules
└── scripts/
    ├── validate_examples.py     # Validate examples against schema
    ├── category_stats.py        # Analyze label distribution
    ├── fetch_openapi.py         # Extract from OpenAPI specs
    └── requirements.txt         # Python dependencies
```

## Using This Skill

### 1. Read SKILL.md First

The main [SKILL.md](SKILL.md) file contains:
- When to use this skill
- 5-source overview and target sizes
- Step-by-step workflow (extract → label → validate → review)
- Practical examples for each source
- Quality checklist

### 2. Reference the Documentation

While generating examples, keep these open:

- **[VOCABULARY.md](references/VOCABULARY.md)**: Decision trees and rules for assigning labels
  - Action mapping (read, write, update, delete, execute, export)
  - Resource type inference from tool names
  - Sensitivity levels and inference rules
  
- **[DATA_SOURCES.md](references/DATA_SOURCES.md)**: How to access and extract from each source
  - OpenAPI specs (30% of data)
  - ToolBench dataset (20%)
  - API-Bank dataset (20%)
  - Synthetic variations (20%)
  - Manual curation (10%)
  
- **[OUTPUT_FORMAT.md](references/OUTPUT_FORMAT.md)**: Exact JSONL schema
  - Required and optional fields
  - Validation rules
  - Valid/invalid examples

### 3. Use the Scripts

Three Python scripts help with generation and quality assurance:

#### validate_examples.py
Validates generated JSONL against the schema:

```bash
python scripts/validate_examples.py data/seed/my_examples.jsonl
```

Checks:
- Valid JSON format (one object per line)
- All required fields present
- Label values are canonical
- No duplicate IDs
- No empty raw_text

#### category_stats.py
Analyzes label distribution to ensure stratification:

```bash
python scripts/category_stats.py data/seed/my_examples.jsonl
```

Reports:
- Count per action label (read, write, update, etc.)
- Count per resource_type (database, storage, api, etc.)
- Count per sensitivity (public, internal, secret)
- Source breakdown
- Review status percentage

#### fetch_openapi.py
Helper to extract examples from OpenAPI specs:

```bash
python scripts/fetch_openapi.py https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json \
  --output stripe_examples.jsonl --limit 200
```

Extracts:
- Operation verbs (GET, POST, PATCH, DELETE)
- Endpoint paths
- Descriptions as raw_text
- Generates incomplete examples (ready for labeling)

### Install Dependencies

```bash
pip install -r scripts/requirements.txt
```

This installs:
- `pyyaml`: For parsing YAML OpenAPI specs
- `jsonschema`: For advanced validation (optional)

## Output Format

Examples are stored in JSONL format (one JSON object per line):

```json
{"id": "550e8400-e29b-41d4-a716-446655440000", "raw_text": "fetch all users from the database", "context": {"tool_name": "database_query", "tool_method": "query", "resource_location": null}, "labels": {"action": "read", "resource_type": "database", "sensitivity": "internal"}, "source": "openapi-spec", "source_detail": "postgres-rest-api-v2024", "reviewed": false}
```

See [OUTPUT_FORMAT.md](references/OUTPUT_FORMAT.md) for complete schema details.

## Recommended Workflow

### Phase 1: Quick Start (1 week)
1. **Manual Curation** (5K examples)
   - High-confidence baseline examples from domain expertise
   - Use `reviewed: true` to mark
   
2. **OpenAPI Specs** (3K examples)
   - Start with Stripe and GitHub APIs
   - Use `fetch_openapi.py` to extract
   
3. **Combine & Validate**
   - Merge outputs into single file
   - Run `validate_examples.py` and `category_stats.py`

### Phase 2: Scale (2-3 weeks)
1. **Expand OpenAPI** (→ 15K total)
   - Add AWS, Google Cloud, Twilio, PagerDuty
   
2. **Add ToolBench** (10K examples)
   - Sample from diverse tool categories
   
3. **Add API-Bank** (10K examples)
   - Real dialogue-based API calling patterns
   
4. **Synthetic Variations** (10K examples)
   - Generate variations for identified gaps
   - Template-based generation with human review
   
5. **Finalize & Stratify**
   - Target: 50K-100K balanced examples
   - Use `category_stats.py` to guide batch selection
   - Ensure ~10-15% reviewed examples

## Key Concepts

### Canonical Labels

The classifier learns to map diverse input vocabularies to these canonical values:

**Actions** (6 categories):
- `read`: Retrieve/access data
- `write`: Create new data
- `update`: Modify existing data
- `delete`: Remove data
- `execute`: Run functions/processes
- `export`: Extract to external destination

**Resource Types** (5 categories + null):
- `database`: SQL, NoSQL stores
- `storage`: Files, object storage
- `api`: External service endpoints
- `queue`: Message queues
- `cache`: In-memory caching
- `null`: Cannot determine from context

**Sensitivity** (3 categories + null):
- `public`: Publicly accessible data
- `internal`: Organization-only data
- `secret`: Highly sensitive (PII, credentials, etc.)
- `null`: Cannot determine from context

### Bias Mitigation

To build an unbiased dataset:
- Use stratified sampling across all 5 sources
- Balance examples across canonical categories
- Track distribution with `category_stats.py`
- Adjust subsequent batches to fix imbalances
- Keep source attribution for traceability

### Quality Assurance

- Use `reviewed: true` only for manually validated examples
- Start with `reviewed: false` for auto-extracted/generated examples
- Target ~10-20% reviewed examples (high-confidence baseline)
- Run validation after each batch
- Address validation errors before combining batches

## Example Usage

### Generate from GitHub API

```bash
# 1. Extract from OpenAPI (note: outputs unlabeled examples)
python scripts/fetch_openapi.py https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json \
  --api-name github-api --output github_raw.jsonl --limit 100

# 2. Apply labels using VOCABULARY.md rules (agent does this step)
# The script outputs examples with null labels - you must apply labeling rules

# 3. Validate
python scripts/validate_examples.py github_raw.jsonl

# 4. Check distribution
python scripts/category_stats.py github_raw.jsonl

# 5. Combine with other sources
cat data/seed/*.jsonl > data/seed/combined_all.jsonl
python scripts/validate_examples.py data/seed/combined_all.jsonl
```

## For Teams

If multiple people are generating examples:

1. **Assign sources**: Person A → OpenAPI, Person B → ToolBench, etc.
2. **Batch processing**: Each generates 100-500 examples
3. **Prefix batches**: `data/seed/batch_01_openapi.jsonl`, `batch_02_toolbench.jsonl`
4. **Weekly review**: 
   - Validate each batch
   - Check distribution trends
   - Identify labeling inconsistencies
5. **Combine regularly**: Keep running total for monitoring progress

## Tips & Best Practices

1. **Start small**: Generate 100-200 examples first to learn the labeling rules
2. **Reference VOCABULARY.md early**: Build consistent labeling muscle
3. **Use scripts early**: Run `validate_examples.py` frequently
4. **Check distribution**: Run `category_stats.py` after each batch
5. **Mark reviews**: Update `reviewed: true` when manually curated
6. **Batch output**: Generate 100-500 examples per batch
7. **Mix sources**: Diversity prevents overfitting to one API style
8. **Document rationale**: For ambiguous labels, add notes in your workflow

## Troubleshooting

**Q: How do I label an ambiguous example?**  
A: Refer to the decision trees in [VOCABULARY.md](references/VOCABULARY.md). If still unsure, default to `null` for resource_type/sensitivity (but action should always be set).

**Q: My distribution is imbalanced**  
A: Use `category_stats.py` output to identify underrepresented categories. Focus your next batch on those categories.

**Q: How do I extract from ToolBench/API-Bank?**  
A: See [DATA_SOURCES.md](references/DATA_SOURCES.md) for detailed extraction walkthrough for each source.

**Q: Should I use `reviewed: true` for all examples?**  
A: No. Use `true` only for manually verified examples (~10-20% of dataset). Auto-extracted examples start as `false`.

**Q: Can I use this skill across tools?**  
A: Yes! This is an Agent Skill that works with any AI agent supporting the SKILL.md format.

## Next Steps

After generating your seed dataset:

1. **Combine all batches**: `cat data/seed/*.jsonl > data/seed/final_dataset.jsonl`
2. **Final validation**: `python scripts/validate_examples.py data/seed/final_dataset.jsonl`
3. **Check distribution**: `python scripts/category_stats.py data/seed/final_dataset.jsonl`
4. **Create splits**: Use 80/10/10 for train/val/test
5. **Train BERT**: Use dataset as training input for canonicalization model (Phase 1)
6. **Monitor production**: Implement Phase 2 learning loop with production logs

## Support

For detailed guidance:
- **How to label**: See [VOCABULARY.md](references/VOCABULARY.md) decision trees
- **How to extract**: See [DATA_SOURCES.md](references/DATA_SOURCES.md) source guides
- **Schema details**: See [OUTPUT_FORMAT.md](references/OUTPUT_FORMAT.md)
- **Main workflow**: See [SKILL.md](SKILL.md)

---

**Status**: Ready to use | **Last Updated**: January 2025

# Codebase Cleanup Guide

## Overview

This guide documents the cleanup of deprecated code from the Guard project. The codebase now focuses exclusively on 3 V2 API endpoints with BERT-based canonicalization.

## Active V2 API Endpoints

The only 3 APIs the project currently supports:

1. **POST /api/v2/enforce** - Enforcement with automatic canonicalization
   - Location: `management_plane/app/endpoints/enforcement_v2.py:267-398`
   - Canonicalizes → Encodes → Enforces → Logs

2. **POST /api/v2/canonicalize** - Debug canonicalization endpoint
   - Location: `management_plane/app/endpoints/enforcement_v2.py:400-460`
   - Canonicalizes only (no enforcement)

3. **POST /api/v2/policies/install** - Policy installation with canonicalization
   - Location: `management_plane/app/endpoints/enforcement_v2.py:463-560`
   - Canonicalizes → Encodes → Installs

All other code is deprecated.

## Files to Delete

### Completely Deprecated Directories (2 directories)

These are standalone projects with no dependencies needed for v2 APIs:

- **`console/`** (~100 files)
  - React-based frontend UI
  - Completely replaced by client-side implementations
  - Size: ~10MB

- **`sdk/`** (~30 files)
  - Python and Tupl SDKs
  - Client libraries for older API versions
  - Size: ~5MB

### Deprecated V1 Endpoints (7 files)

These endpoints are not part of the v2 API specification:

- `management_plane/app/endpoints/enforcement.py` - V1 enforcement (replaced by v2)
- `management_plane/app/endpoints/intents.py` - Intent comparison (not in v2 spec)
- `management_plane/app/endpoints/boundaries.py` - Boundary management (not in v2 spec)
- `management_plane/app/endpoints/telemetry.py` - Telemetry (not in v2 spec)
- `management_plane/app/endpoints/encoding.py` - V1.3 encoding (replaced by v2)
- `management_plane/app/endpoints/agents.py` - Agent policies (not in v2 spec)
- `management_plane/app/endpoints/auth.py` - Standalone auth (uses main auth.py instead)

### Deprecated Services (2 files)

These services are not used by the v2 endpoints:

- `management_plane/app/services/vocabulary.py`
  - Old vocabulary-based encoding system
  - Completely replaced by BERT canonicalization
  - No longer needed

- `management_plane/app/database.py`
  - Database wrapper for v1 endpoints
  - Not used by v2 endpoints

### Deprecated Tests (13 files)

Tests for deprecated endpoints and services:

- `management_plane/tests/test_endpoints_agents.py` - Agent endpoint tests
- `management_plane/tests/test_encoding_endpoints.py` - V1.3 encoding tests
- `management_plane/tests/test_encoding.py` - V1 encoding tests
- `management_plane/tests/test_applicability_filter.py` - Old filtering logic
- `management_plane/tests/test_deny_semantics.py` - Old semantics
- `management_plane/tests/test_e2e_real_encoding.py` - V1 encoding E2E
- `management_plane/tests/test_llm_anchor_generation.py` - Old LLM logic
- `management_plane/tests/test_nl_policy_parser.py` - Old parser
- `management_plane/tests/test_policy_templates.py` - Old templates
- `management_plane/tests/test_rule_anchors.py` - Old anchor logic
- `management_plane/tests/test_v1_1_similarity.py` - V1.1 tests
- `management_plane/tests/test_phase2_integration.py` - Old phase
- `management_plane/tests/test_performance.py` - General performance tests (can keep)

### Vocabulary Files (2 files)

No longer used with BERT canonicalization:

- `vocabulary.yaml` - Root-level canonical vocabulary
- `sdk/python/vocabulary.yaml` - SDK vocabulary (will be deleted with sdk/)

### Old Model Files (1 file)

Keep optimized version only:

- `management_plane/models/canonicalizer_tinybert_v1.0/model.onnx`
  - Non-optimized ONNX model
  - Keep: `model_optimized.onnx` (28.5MB, actually used)

## Files to Keep

### Application Core
- ✅ `management_plane/app/main.py` - FastAPI app initialization
- ✅ `management_plane/app/settings.py` - Configuration
- ✅ `management_plane/app/models.py` - Data models
- ✅ `management_plane/app/auth.py` - Authentication
- ✅ `management_plane/app/endpoints/enforcement_v2.py` - **THE 3 V2 ENDPOINTS**

### V2 Services (BERT + Encoding)
- ✅ `management_plane/app/services/canonicalizer.py` - BERT canonicalization
- ✅ `management_plane/app/services/canonicalization_logger.py` - Logging
- ✅ `management_plane/app/services/intent_encoder.py` - Intent encoding
- ✅ `management_plane/app/services/policy_encoder.py` - Policy encoding
- ✅ `management_plane/app/services/semantic_encoder.py` - Base encoder
- ✅ `management_plane/app/services/canonical_slots.py` - Slot serialization
- ✅ `management_plane/app/services/dataplane_client.py` - gRPC client
- ✅ `management_plane/app/services/policy_converter.py` - Policy conversion

### gRPC/Protobuf
- ✅ `data_plane/proto/rule_installation.proto` - Service contract
- ✅ `management_plane/app/generated/rule_installation_pb2.py` - Generated
- ✅ `management_plane/app/generated/rule_installation_pb2_grpc.py` - Generated

### ML Models
- ✅ `management_plane/models/canonicalizer_tinybert_v1.0/model_optimized.onnx`
- ✅ `management_plane/models/canonicalizer_tinybert_v1.0/label_maps.json`
- ✅ `management_plane/models/canonicalizer_tinybert_v1.0/tokenizer/` (all files)

### V2 Tests
- ✅ `management_plane/tests/test_canonicalizer.py` - BERT canonicalizer tests
- ✅ `management_plane/tests/test_canonical_slots.py` - Slot tests
- ✅ `management_plane/tests/test_enforcement_proxy.py` - V2 endpoint tests
- ✅ `management_plane/tests/test_auth.py` - Auth tests
- ✅ `management_plane/tests/test_header_auth.py` - Header auth tests
- ✅ `management_plane/tests/test_semantic_encoders.py` - Encoder tests
- ✅ `management_plane/tests/test_layered_flow_e2e.py` - E2E tests

### Infrastructure
- ✅ `management_plane/pyproject.toml` - Dependencies
- ✅ `management_plane/uv.lock` - Locked versions
- ✅ `deployment/` - Docker & deployment files
- ✅ `README.md` - Project documentation

## Cleanup Steps

### 1. Run Dry-Run First (Recommended)

```bash
cd /Users/sid/Projects/guard
./cleanup-deprecated.sh --dry-run
```

This will show what would be deleted without making any changes.

### 2. Review the Dry-Run Output

Verify that:
- Only deprecated files are listed
- No files from "Files to Keep" section appear
- The summary makes sense

### 3. Run Actual Cleanup

```bash
cd /Users/sid/Projects/guard
./cleanup-deprecated.sh
```

This will:
- Delete all deprecated files/directories
- Create a backup list at `cleanup-backup-TIMESTAMP.txt`
- Check git status
- Verify no imports of deleted code
- Print summary

### 4. Post-Cleanup Verification

After running the cleanup script:

```bash
cd /Users/sid/Projects/guard/management_plane

# 1. Run tests to ensure nothing broke
python -m pytest tests/ -v

# 2. Check for any import errors
python -c "from app.main import app; print('✓ App imports successfully')"

# 3. Verify no references to deleted modules
grep -r "from app.endpoints.enforcement import" . || echo "✓ No V1 enforcement imports"
grep -r "from app.services.vocabulary import" . || echo "✓ No vocabulary imports"
```

### 5. Commit Changes

```bash
cd /Users/sid/Projects/guard
git add -A
git commit -m "cleanup: remove deprecated code not used by v2 APIs

- Remove console/ and sdk/ directories (completely deprecated)
- Remove V1 endpoints: enforcement, intents, boundaries, telemetry, encoding, agents
- Remove deprecated services: vocabulary, database
- Remove deprecated tests for V1 endpoints
- Remove vocabulary.yaml files (replaced by BERT)
- Keep only model_optimized.onnx (remove duplicate)

Focus now exclusively on 3 V2 APIs:
  - POST /api/v2/enforce
  - POST /api/v2/canonicalize
  - POST /api/v2/policies/install"
```

## Rollback Instructions

If something goes wrong, you can restore files from git:

```bash
# View what was deleted
cat cleanup-backup-TIMESTAMP.txt

# Restore entire project to previous state
git reset --hard HEAD~1

# Or selectively restore specific files
git restore <file-path>
```

## Impact Analysis

### Files Deleted
- **Directories:** 2 (console/, sdk/)
- **Endpoint files:** 7
- **Service files:** 2
- **Test files:** 13
- **Config files:** 2 (vocabulary.yaml files)
- **Model files:** 1 (non-optimized ONNX)
- **Total:** ~27 files, ~15MB

### Size Reduction
- **Before:** ~500+ files
- **After:** ~100 critical files
- **Reduction:** ~80% of files removed

### No Breaking Changes Expected
✅ Active V2 endpoints don't import deleted code
✅ Services are cleanly separated
✅ Tests for V2 endpoints are kept
✅ All dependencies are preserved
✅ Configuration remains unchanged

## Timeline

| Phase | Action | Time |
|-------|--------|------|
| 1 | Run dry-run and review | 5 min |
| 2 | Run cleanup script | 1 min |
| 3 | Run tests | 5-10 min |
| 4 | Review output and fix issues | 10-30 min |
| 5 | Commit to git | 2 min |
| **Total** | | **~30-50 min** |

## Support

If you encounter issues:

1. Check the backup file: `cleanup-backup-TIMESTAMP.txt`
2. Review the cleanup script output
3. Check test failures: `python -m pytest tests/ -v --tb=short`
4. Restore from git if needed: `git reset --hard HEAD~1`

---

**Generated:** 2026-01-25
**Project:** Guard (LLM Security Policy Enforcement)
**Cleanup Focus:** Consolidation to 3 V2 APIs with BERT canonicalization

#!/usr/bin/env python3
"""
Validates seed dataset examples against the required schema.

Usage:
    python validate_examples.py <jsonl_file>

Example:
    python validate_examples.py data/seed/examples.jsonl

Output:
    - Summary of valid/invalid examples
    - List of errors with line numbers
    - Statistics about the dataset
"""

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Canonical values
VALID_ACTIONS = {"read", "write", "update", "delete", "execute", "export"}
VALID_RESOURCE_TYPES = {"database", "storage", "api", "queue", "cache"}
VALID_SENSITIVITIES = {"public", "internal", "secret"}
VALID_SOURCES = {"openapi-spec", "toolbench", "api-bank", "synthetic", "manual"}


class ValidationError:
    """Represents a validation error for an example."""

    def __init__(self, line_num: int, field: str, message: Optional[str]):
        self.line_num = line_num
        self.field = field
        self.message = message or "Unknown error"

    def __str__(self) -> str:
        return f"Line {self.line_num} [{self.field}]: {self.message}"


def validate_uuid(value: Any) -> Tuple[bool, Optional[str]]:
    """Validate UUID v4 format."""
    if not isinstance(value, str):
        return False, "Must be a string"

    try:
        uuid_obj = uuid.UUID(value, version=4)
        return True, None
    except (ValueError, AttributeError):
        return False, "Must be a valid UUID v4"


def validate_raw_text(value: Any) -> Tuple[bool, Optional[str]]:
    """Validate raw_text field."""
    if not isinstance(value, str):
        return False, "Must be a string"

    if len(value) == 0:
        return False, "Cannot be empty"

    if len(value.strip()) == 0:
        return False, "Cannot be only whitespace"

    if len(value) > 1000:
        return False, f"Length {len(value)} exceeds max 1000 characters"

    return True, None


def validate_action(value: Any) -> Tuple[bool, Optional[str]]:
    """Validate action label."""
    if value is None:
        return False, "Cannot be null"

    if not isinstance(value, str):
        return False, "Must be a string"

    if value not in VALID_ACTIONS:
        return False, f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}. Got: {value}"

    return True, None


def validate_resource_type(value: Any) -> Tuple[bool, Optional[str]]:
    """Validate resource_type label."""
    if value is None:
        return True, None  # null is allowed

    if not isinstance(value, str):
        return False, "Must be a string or null"

    if value not in VALID_RESOURCE_TYPES:
        allowed = ", ".join(sorted(VALID_RESOURCE_TYPES))
        return False, f"Must be one of: {allowed}, or null. Got: {value}"

    return True, None


def validate_sensitivity(value: Any) -> Tuple[bool, Optional[str]]:
    """Validate sensitivity label."""
    if value is None:
        return True, None  # null is allowed

    if not isinstance(value, str):
        return False, "Must be a string or null"

    if value not in VALID_SENSITIVITIES:
        allowed = ", ".join(sorted(VALID_SENSITIVITIES))
        return False, f"Must be one of: {allowed}, or null. Got: {value}"

    return True, None


def validate_source(value: Any) -> Tuple[bool, Optional[str]]:
    """Validate source field."""
    if not isinstance(value, str):
        return False, "Must be a string"

    if value not in VALID_SOURCES:
        return False, f"Must be one of: {', '.join(sorted(VALID_SOURCES))}"

    return True, None


def validate_source_detail(value: Any) -> Tuple[bool, Optional[str]]:
    """Validate source_detail field."""
    if not isinstance(value, str):
        return False, "Must be a string"

    if len(value) == 0:
        return False, "Cannot be empty"

    return True, None


def validate_reviewed(value: Any) -> Tuple[bool, Optional[str]]:
    """Validate reviewed field."""
    if not isinstance(value, bool):
        return False, f"Must be boolean (true/false), not {type(value).__name__}"

    return True, None


def validate_example(example: Any, line_num: int) -> List[ValidationError]:
    """Validate a single example against the schema."""
    errors: List[ValidationError] = []

    # Top-level must be dict
    if not isinstance(example, dict):
        return [ValidationError(line_num, "root", "Must be a JSON object")]

    # Required top-level fields
    required_fields = ["id", "raw_text", "labels", "source", "source_detail", "reviewed"]
    for field in required_fields:
        if field not in example:
            errors.append(ValidationError(line_num, field, "Required field missing"))

    # Validate id
    if "id" in example:
        is_valid, msg = validate_uuid(example["id"])
        if not is_valid:
            errors.append(ValidationError(line_num, "id", msg))

    # Validate raw_text
    if "raw_text" in example:
        is_valid, msg = validate_raw_text(example["raw_text"])
        if not is_valid:
            errors.append(ValidationError(line_num, "raw_text", msg))

    # Validate context (optional)
    if "context" in example:
        context = example["context"]
        if context is not None and not isinstance(context, dict):
            errors.append(ValidationError(line_num, "context", "Must be object or null"))
        # No further validation of context fields (all optional)

    # Validate labels
    if "labels" in example:
        labels = example["labels"]
        if not isinstance(labels, dict):
            errors.append(ValidationError(line_num, "labels", "Must be an object"))
        else:
            # action (required)
            if "action" not in labels:
                errors.append(ValidationError(line_num, "labels.action", "Required field missing"))
            else:
                is_valid, msg = validate_action(labels["action"])
                if not is_valid:
                    errors.append(ValidationError(line_num, "labels.action", msg))

            # resource_type (required, but can be null)
            if "resource_type" not in labels:
                errors.append(ValidationError(line_num, "labels.resource_type", "Required field missing"))
            else:
                is_valid, msg = validate_resource_type(labels["resource_type"])
                if not is_valid:
                    errors.append(ValidationError(line_num, "labels.resource_type", msg))

            # sensitivity (required, but can be null)
            if "sensitivity" not in labels:
                errors.append(ValidationError(line_num, "labels.sensitivity", "Required field missing"))
            else:
                is_valid, msg = validate_sensitivity(labels["sensitivity"])
                if not is_valid:
                    errors.append(ValidationError(line_num, "labels.sensitivity", msg))

    # Validate source
    if "source" in example:
        is_valid, msg = validate_source(example["source"])
        if not is_valid:
            errors.append(ValidationError(line_num, "source", msg))

    # Validate source_detail
    if "source_detail" in example:
        is_valid, msg = validate_source_detail(example["source_detail"])
        if not is_valid:
            errors.append(ValidationError(line_num, "source_detail", msg))

    # Validate reviewed
    if "reviewed" in example:
        is_valid, msg = validate_reviewed(example["reviewed"])
        if not is_valid:
            errors.append(ValidationError(line_num, "reviewed", msg))

    return errors


def validate_file(filepath: Path) -> Tuple[int, int, List[ValidationError], Dict[str, Any]]:
    """
    Validate an entire JSONL file.

    Returns:
        (total_lines, valid_examples, errors, stats)
    """
    valid_count = 0
    errors: List[ValidationError] = []
    ids_seen: set = set()
    stats = {
        "actions": {},
        "resource_types": {},
        "sensitivities": {},
        "sources": {},
        "reviewed_count": 0,
    }

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    # Skip empty lines
                    continue

                # Parse JSON
                try:
                    example = json.loads(line)
                except json.JSONDecodeError as e:
                    errors.append(ValidationError(line_num, "json", f"Invalid JSON: {e}"))
                    continue

                # Validate example
                example_errors = validate_example(example, line_num)
                if example_errors:
                    errors.extend(example_errors)
                else:
                    valid_count += 1

                    # Check for duplicate IDs
                    if isinstance(example, dict) and "id" in example:
                        example_id = example["id"]
                        if example_id in ids_seen:
                            errors.append(
                                ValidationError(line_num, "id", f"Duplicate ID: {example_id}")
                            )
                        else:
                            ids_seen.add(example_id)

                    # Collect statistics
                    if isinstance(example, dict) and "labels" in example and isinstance(example["labels"], dict):
                        labels = example["labels"]
                        action = labels.get("action")
                        if action:
                            stats["actions"][action] = stats["actions"].get(action, 0) + 1

                        resource = labels.get("resource_type")
                        if resource:
                            stats["resource_types"][resource] = stats["resource_types"].get(resource, 0) + 1

                        sensitivity = labels.get("sensitivity")
                        if sensitivity:
                            stats["sensitivities"][sensitivity] = stats["sensitivities"].get(sensitivity, 0) + 1

                    if isinstance(example, dict) and "source" in example:
                        source = example["source"]
                        stats["sources"][source] = stats["sources"].get(source, 0) + 1

                    if isinstance(example, dict) and "reviewed" in example:
                        if example["reviewed"]:
                            stats["reviewed_count"] += 1

    except IOError as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)

    total_lines = valid_count + len(errors)
    return total_lines, valid_count, errors, stats


def print_report(filepath: Path, total: int, valid: int, errors: List[ValidationError], stats: Dict[str, Any]):
    """Print validation report."""
    print(f"\n{'=' * 70}")
    print(f"SEED DATASET VALIDATION REPORT")
    print(f"{'=' * 70}\n")

    print(f"File: {filepath}")
    print(f"Total lines processed: {total}")
    print(f"Valid examples: {valid}")
    print(f"Invalid examples: {len(errors)}")
    print(f"Validity: {100 * valid / total if total > 0 else 0:.1f}%\n")

    if errors:
        print(f"{'─' * 70}")
        print(f"ERRORS (first 20 shown):")
        print(f"{'─' * 70}\n")
        for error in errors[:20]:
            print(f"  {error}")
        if len(errors) > 20:
            print(f"\n  ... and {len(errors) - 20} more errors")
        print()

    if valid > 0:
        print(f"{'─' * 70}")
        print(f"STATISTICS:")
        print(f"{'─' * 70}\n")

        print("Action distribution:")
        for action in sorted(VALID_ACTIONS):
            count = stats["actions"].get(action, 0)
            pct = 100 * count / valid if valid > 0 else 0
            print(f"  {action:10s}: {count:5d} ({pct:5.1f}%)")

        print("\nResource type distribution:")
        for rt in sorted(VALID_RESOURCE_TYPES):
            count = stats["resource_types"].get(rt, 0)
            pct = 100 * count / valid if valid > 0 else 0
            print(f"  {rt:10s}: {count:5d} ({pct:5.1f}%)")
        if stats["resource_types"].get(None):
            count = stats["resource_types"][None]
            pct = 100 * count / valid if valid > 0 else 0
            print(f"  {'null':10s}: {count:5d} ({pct:5.1f}%)")

        print("\nSensitivity distribution:")
        for sens in sorted(VALID_SENSITIVITIES):
            count = stats["sensitivities"].get(sens, 0)
            pct = 100 * count / valid if valid > 0 else 0
            print(f"  {sens:10s}: {count:5d} ({pct:5.1f}%)")
        if stats["sensitivities"].get(None):
            count = stats["sensitivities"][None]
            pct = 100 * count / valid if valid > 0 else 0
            print(f"  {'null':10s}: {count:5d} ({pct:5.1f}%)")

        print("\nSource distribution:")
        for source in sorted(VALID_SOURCES):
            count = stats["sources"].get(source, 0)
            pct = 100 * count / valid if valid > 0 else 0
            print(f"  {source:15s}: {count:5d} ({pct:5.1f}%)")

        print(f"\nReviewed examples: {stats['reviewed_count']} ({100 * stats['reviewed_count'] / valid if valid > 0 else 0:.1f}%)")

    print(f"\n{'=' * 70}\n")

    # Return exit code
    return 0 if len(errors) == 0 else 1


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python validate_examples.py <jsonl_file>")
        print("\nExample:")
        print("  python validate_examples.py data/seed/examples.jsonl")
        sys.exit(1)

    filepath = Path(sys.argv[1])
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    total, valid, errors, stats = validate_file(filepath)
    exit_code = print_report(filepath, total, valid, errors, stats)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

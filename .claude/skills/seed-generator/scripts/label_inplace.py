#!/usr/bin/env python3
"""
Label seed dataset examples in-place using heuristic rules.

This script reads a JSONL file, infers labels using keyword rules and
context metadata, and writes updates back to the same file (or does a
dry run without writing).
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def word_patterns(words: List[str]) -> List[str]:
    return [rf"\b{re.escape(word)}\b" for word in words]


ACTION_ORDER = ["export", "delete", "update", "write", "execute", "read"]
ACTION_PATTERNS = {
    "export": word_patterns(
        [
            "export",
            "download",
            "backup",
            "dump",
            "extract",
            "transfer",
            "sync",
            "replicate",
        ]
    ),
    "delete": word_patterns(
        [
            "delete",
            "remove",
            "drop",
            "destroy",
            "purge",
            "revoke",
            "clear",
            "erase",
            "wipe",
            "unlink",
            "archive",
        ]
    ),
    "update": word_patterns(
        [
            "update",
            "modify",
            "change",
            "edit",
            "patch",
            "replace",
            "set",
            "reset",
            "suspend",
            "unsuspend",
            "attach",
            "detach",
            "adjust",
            "revise",
        ]
    ),
    "write": word_patterns(
        [
            "create",
            "insert",
            "add",
            "post",
            "save",
            "store",
            "submit",
            "register",
            "upload",
            "publish",
        ]
    ),
    "execute": word_patterns(
        [
            "execute",
            "run",
            "invoke",
            "trigger",
            "call",
            "start",
            "launch",
            "initiate",
            "perform",
            "activate",
            "dispatch",
            "retry",
            "redeliver",
            "verify",
            "check",
        ]
    ),
    "read": word_patterns(
        [
            "read",
            "get",
            "list",
            "retrieve",
            "fetch",
            "query",
            "search",
            "find",
            "show",
            "display",
            "view",
            "lookup",
            "inspect",
            "scan",
            "browse",
            "examine",
            "pull",
            "load",
        ]
    ),
}

RESOURCE_ORDER = ["database", "storage", "queue", "cache", "api"]
RESOURCE_PATTERNS = {
    "database": word_patterns(
        [
            "database",
            "db",
            "sql",
            "nosql",
            "table",
            "collection",
            "record",
            "postgres",
            "postgresql",
            "mysql",
            "sqlite",
            "mongodb",
            "dynamodb",
            "rds",
            "cassandra",
            "elasticsearch",
            "bigquery",
            "snowflake",
            "redshift",
            "oracle",
            "mssql",
        ]
    ),
    "storage": word_patterns(
        [
            "storage",
            "s3",
            "blob",
            "bucket",
            "file",
            "filesystem",
            "object",
            "gcs",
            "minio",
            "dropbox",
            "box",
            "gdrive",
            "drive",
        ]
    ),
    "queue": word_patterns(
        [
            "queue",
            "kafka",
            "sqs",
            "rabbitmq",
            "pubsub",
            "sns",
            "nats",
            "kinesis",
            "topic",
            "stream",
        ]
    ),
    "cache": word_patterns(["cache", "redis", "memcached", "session"]),
    "api": word_patterns(
        [
            "api",
            "service",
            "endpoint",
            "webhook",
            "http",
            "rest",
            "graphql",
            "gateway",
            "microservice",
        ]
    ),
}

SENSITIVITY_PATTERNS = {
    "secret": word_patterns(
        [
            "password",
            "token",
            "credential",
            "credentials",
            "secret",
            "oauth",
            "payment",
            "credit card",
            "ssn",
            "pii",
            "phi",
            "medical",
            "health",
            "financial",
        ]
    )
    + [
        r"\bapi[-\s]?key\b",
        r"\bprivate\s+key\b",
        r"\bsecret\s+key\b",
        r"\baccess\s+key\b",
        r"\brefresh\s+token\b",
        r"\bauth(?:entication|orization)?\b",
        r"\baccount\s+number\b",
    ],
    "public": word_patterns(
        [
            "public",
            "open",
            "external",
            "anonymous",
            "guest",
            "published",
            "community",
            "marketplace",
        ]
    ),
    "internal": word_patterns(
        [
            "internal",
            "private",
            "confidential",
            "restricted",
            "organization",
            "enterprise",
            "team",
            "member",
            "employee",
            "staff",
            "user",
            "account",
            "installation",
            "config",
            "configuration",
            "settings",
        ]
    ),
}

METHOD_ACTION_MAP = {
    "GET": "read",
    "POST": "write",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
    "HEAD": "read",
    "OPTIONS": "read",
}

HTTP_METHODS = set(METHOD_ACTION_MAP.keys())


def compile_patterns(patterns: Dict[str, List[str]]) -> Dict[str, List[re.Pattern]]:
    compiled: Dict[str, List[re.Pattern]] = {}
    for key, entries in patterns.items():
        compiled[key] = [re.compile(entry, re.IGNORECASE) for entry in entries]
    return compiled


COMPILED_ACTION = compile_patterns(ACTION_PATTERNS)
COMPILED_RESOURCE = compile_patterns(RESOURCE_PATTERNS)
COMPILED_SENSITIVITY = compile_patterns(SENSITIVITY_PATTERNS)


def build_search_text(example: Dict[str, object]) -> str:
    context = example.get("context") or {}
    parts = [
        str(example.get("raw_text") or ""),
        str(context.get("tool_name") or ""),
        str(context.get("tool_method") or ""),
    ]
    return " ".join(parts).lower()


def extract_http_method(tool_method: Optional[str]) -> Optional[str]:
    if not tool_method:
        return None
    token = tool_method.strip().split()[0].upper()
    if token in HTTP_METHODS:
        return token
    return None


def match_patterns(patterns: List[re.Pattern], text: str) -> Optional[str]:
    for pattern in patterns:
        if pattern.search(text):
            return pattern.pattern
    return None


def infer_action(text: str, method: Optional[str]) -> Tuple[str, float, str]:
    for action in ACTION_ORDER:
        matched = match_patterns(COMPILED_ACTION[action], text)
        if matched:
            return action, 0.9, f"keyword:{matched}"
    if method and method in METHOD_ACTION_MAP:
        return METHOD_ACTION_MAP[method], 0.7, f"http_method:{method}"
    return "read", 0.3, "default:read"


def infer_resource_type(text: str, method: Optional[str]) -> Tuple[Optional[str], float, str]:
    for resource_type in RESOURCE_ORDER:
        matched = match_patterns(COMPILED_RESOURCE[resource_type], text)
        if matched:
            return resource_type, 0.9, f"keyword:{matched}"
    if method:
        return "api", 0.6, f"http_method:{method}"
    return None, 0.3, "default:null"


def infer_sensitivity(text: str) -> Tuple[Optional[str], float, str]:
    matched = match_patterns(COMPILED_SENSITIVITY["secret"], text)
    if matched:
        return "secret", 0.9, f"keyword:{matched}"
    matched = match_patterns(COMPILED_SENSITIVITY["public"], text)
    if matched:
        return "public", 0.8, f"keyword:{matched}"
    matched = match_patterns(COMPILED_SENSITIVITY["internal"], text)
    if matched:
        return "internal", 0.7, f"keyword:{matched}"
    return None, 0.3, "default:null"


def is_null(value: Optional[str]) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() == "null":
        return True
    return False


def label_example(
    example: Dict[str, object],
    overwrite: bool,
    confidence_threshold: float,
) -> Tuple[Dict[str, object], List[str]]:
    warnings: List[str] = []
    labels = example.get("labels") or {}
    labels.setdefault("action", None)
    labels.setdefault("resource_type", None)
    labels.setdefault("sensitivity", None)
    example["labels"] = labels

    text = build_search_text(example)
    context = example.get("context") or {}
    method = extract_http_method(context.get("tool_method"))

    if overwrite or is_null(labels.get("action")):
        action, confidence, reason = infer_action(text, method)
        labels["action"] = action
        if confidence < confidence_threshold:
            warnings.append(f"action:{reason}")

    if overwrite or is_null(labels.get("resource_type")):
        resource_type, confidence, reason = infer_resource_type(text, method)
        labels["resource_type"] = resource_type
        if confidence < confidence_threshold:
            warnings.append(f"resource_type:{reason}")

    if overwrite or is_null(labels.get("sensitivity")):
        sensitivity, confidence, reason = infer_sensitivity(text)
        labels["sensitivity"] = sensitivity
        if confidence < confidence_threshold:
            warnings.append(f"sensitivity:{reason}")

    return example, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Label JSONL examples in-place using heuristic rules."
    )
    parser.add_argument("jsonl_path", help="Path to JSONL file to label")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze and report without writing changes",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create a .bak backup before overwriting",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing labels instead of only filling nulls",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only label the first N examples (rest unchanged)",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.6,
        help="Warn when any inferred label is below this confidence",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.jsonl_path)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    temp_path = input_path.with_suffix(input_path.suffix + ".tmp")
    labeled_count = 0
    warning_count = 0
    warnings_by_line: List[str] = []
    total_lines = 0

    output_handle = None if args.dry_run else temp_path.open("w")

    try:
        with input_path.open("r") as handle:
            for line_number, line in enumerate(handle, start=1):
                total_lines += 1
                stripped = line.strip()
                if not stripped:
                    if output_handle:
                        output_handle.write(line)
                    continue

                try:
                    example = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"Invalid JSON on line {line_number}: {exc}") from exc

                if args.limit and labeled_count >= args.limit:
                    if output_handle:
                        output_handle.write(json.dumps(example))
                        output_handle.write("\n")
                    continue

                labeled_count += 1
                updated, warnings = label_example(
                    example, args.overwrite, args.confidence_threshold
                )
                if warnings:
                    warning_count += 1
                    warnings_by_line.append(
                        f"Line {line_number}: {updated.get('id')} ({', '.join(warnings)})"
                    )
                if output_handle:
                    output_handle.write(json.dumps(updated))
                    output_handle.write("\n")
    finally:
        if output_handle:
            output_handle.close()

    if args.dry_run:
        if temp_path.exists():
            temp_path.unlink()
    else:
        if args.backup:
            backup_path = input_path.with_suffix(input_path.suffix + ".bak")
            shutil.copy2(input_path, backup_path)
        os.replace(temp_path, input_path)

    print(
        "Labeling summary:",
        file=sys.stderr,
    )
    print(f"  file: {input_path}", file=sys.stderr)
    print(f"  total_lines: {total_lines}", file=sys.stderr)
    print(f"  labeled: {labeled_count}", file=sys.stderr)
    print(f"  warnings: {warning_count}", file=sys.stderr)

    if warnings_by_line:
        print("\nLow-confidence labels:", file=sys.stderr)
        for warning in warnings_by_line[:50]:
            print(f"  {warning}", file=sys.stderr)
        if len(warnings_by_line) > 50:
            print("  ...", file=sys.stderr)


if __name__ == "__main__":
    main()

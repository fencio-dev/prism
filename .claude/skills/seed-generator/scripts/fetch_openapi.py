#!/usr/bin/env python3
"""
Fetches and parses OpenAPI specifications to extract operation verbs and descriptions.

This script helps extract raw text examples from OpenAPI specs for seed dataset generation.

Usage:
    python fetch_openapi.py <spec_url> [--output <output_file>]

Examples:
    python fetch_openapi.py https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json
    python fetch_openapi.py https://example.com/api/openapi.yaml --output examples.jsonl

Output format:
    Each line is a JSON object with:
    {
        "raw_text": "operation description",
        "context": {
            "tool_name": "api-name",
            "tool_method": "HTTP_METHOD /path"
        },
        "source": "openapi-spec",
        "source_detail": "api-name-version"
    }

Supports:
    - JSON OpenAPI specs
    - YAML OpenAPI specs
    - HTTP/HTTPS URLs
    - Local file paths
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import urlopen


def fetch_spec(spec_source: str) -> Dict[str, Any]:
    """
    Fetch OpenAPI spec from URL or local file.

    Args:
        spec_source: URL or file path to OpenAPI spec

    Returns:
        Parsed OpenAPI spec as dictionary
    """
    try:
        # Try as URL first
        if spec_source.startswith("http"):
            with urlopen(spec_source) as response:
                content = response.read().decode("utf-8")
        else:
            # Treat as file path
            with open(spec_source, "r") as f:
                content = f.read()

        # Try JSON first
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try YAML
            try:
                import yaml

                return yaml.safe_load(content)
            except ImportError:
                print(
                    "Error: YAML support requires PyYAML. Install with: pip install pyyaml",
                    file=sys.stderr,
                )
                sys.exit(1)

    except Exception as e:
        print(f"Error fetching spec from {spec_source}: {e}", file=sys.stderr)
        sys.exit(1)


def extract_api_name(spec: Dict[str, Any], spec_source: str) -> str:
    """
    Extract API name from spec or source.

    Args:
        spec: Parsed OpenAPI spec
        spec_source: Original source (URL or file path)

    Returns:
        Estimated API name
    """
    # Try from spec info
    info = spec.get("info", {})
    if "title" in info:
        return info["title"].lower().replace(" ", "-")

    # Try to extract from URL/path
    parsed = urlparse(spec_source)
    if parsed.scheme:
        # URL: extract domain
        domain = parsed.netloc.replace("www.", "").split(".")[0]
        return domain
    else:
        # File path
        return Path(spec_source).stem


def extract_operations(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract operations from OpenAPI spec.

    Args:
        spec: Parsed OpenAPI spec

    Returns:
        List of operation objects with path, method, and description
    """
    operations = []
    paths = spec.get("paths", {})

    if not paths:
        return operations

    for path, path_item in paths.items():
        # Skip parameters at path level
        if not isinstance(path_item, dict):
            continue

        for method, operation in path_item.items():
            # Skip non-HTTP method entries
            if method not in ["get", "post", "put", "patch", "delete", "head", "options"]:
                continue

            if not isinstance(operation, dict):
                continue

            # Extract description/summary
            summary = operation.get("summary", "")
            description = operation.get("description", "")
            text = (summary or description).strip()

            if not text:
                # Generate from path and method
                text = f"{method.upper()} {path}"

            operations.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "text": text,
                    "operation_id": operation.get("operationId", ""),
                }
            )

    return operations


def generate_raw_text(operation: Dict[str, Any]) -> str:
    """
    Generate human-readable raw_text from operation.

    Args:
        operation: Operation object with method, path, text

    Returns:
        Formatted raw_text string
    """
    method = operation["method"]
    text = operation["text"]

    # Map HTTP method to action verb
    action_verbs = {
        "GET": ["list", "retrieve", "fetch", "get"],
        "POST": ["create", "add", "insert", "submit"],
        "PUT": ["update", "replace", "set"],
        "PATCH": ["update", "modify", "patch"],
        "DELETE": ["delete", "remove", "drop"],
        "HEAD": ["check", "verify"],
        "OPTIONS": ["query options"],
    }

    verb = action_verbs.get(method, ["execute"])[0]

    # Use description if it's readable, otherwise construct
    if text and len(text) > 10 and not text.startswith(method):
        return text.lower().strip()
    else:
        # Fallback: construct from method and path
        return f"{verb} {operation['path'].lower().strip('/')}"


def examples_from_spec(
    spec_source: str, api_name: Optional[str] = None, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Extract seed dataset examples from OpenAPI spec.

    Args:
        spec_source: URL or file path to OpenAPI spec
        api_name: Optional API name override
        limit: Optional limit on number of examples to extract

    Returns:
        List of example objects ready for seed dataset
    """
    # Fetch spec
    spec = fetch_spec(spec_source)

    # Get API name
    if not api_name:
        api_name = extract_api_name(spec, spec_source)

    # Extract version if available
    version = spec.get("info", {}).get("version", "v1")
    source_detail = f"{api_name}-{version}".lower()

    # Extract operations
    operations = extract_operations(spec)
    if limit:
        operations = operations[:limit]

    # Generate examples
    examples = []
    for op in operations:
        raw_text = generate_raw_text(op)

        example = {
            "id": str(uuid.uuid4()),
            "raw_text": raw_text,
            "context": {
                "tool_name": api_name,
                "tool_method": f"{op['method']} {op['path']}",
                "resource_location": None,
            },
            "labels": {
                "action": None,
                "resource_type": None,
                "sensitivity": None,
            },
            "source": "openapi-spec",
            "source_detail": source_detail,
            "reviewed": False,
        }
        examples.append(example)

    return examples


def write_jsonl(examples: List[Dict[str, Any]], output_file: Optional[str] = None):
    """
    Write examples to JSONL file.

    Args:
        examples: List of example objects
        output_file: Optional output file path. If None, writes to stdout.
    """
    output = open(output_file, "w") if output_file else sys.stdout

    try:
        for example in examples:
            json.dump(example, output)
            output.write("\n")
        if output_file:
            output.close()
    except IOError as e:
        print(f"Error writing output: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Extract seed dataset examples from OpenAPI specifications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python fetch_openapi.py https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json
    python fetch_openapi.py https://example.com/api/openapi.yaml --output examples.jsonl
    python fetch_openapi.py /path/to/local/spec.json --output extracted.jsonl --limit 100
        """,
    )

    parser.add_argument("spec_url", help="URL or file path to OpenAPI specification")
    parser.add_argument(
        "--output", "-o", help="Output file (JSONL format). If not specified, writes to stdout."
    )
    parser.add_argument(
        "--api-name", help="API name override (extracted from spec by default)"
    )
    parser.add_argument(
        "--limit", type=int, help="Limit number of examples to extract (optional)"
    )

    args = parser.parse_args()

    print(f"Fetching OpenAPI spec from: {args.spec_url}", file=sys.stderr)

    # Extract examples
    examples = examples_from_spec(args.spec_url, api_name=args.api_name, limit=args.limit)

    print(f"Extracted {len(examples)} operations from spec", file=sys.stderr)

    if not examples:
        print("Warning: No operations found in spec", file=sys.stderr)
        sys.exit(0)

    # Write output
    write_jsonl(examples, args.output)

    if args.output:
        print(f"Wrote {len(examples)} examples to: {args.output}", file=sys.stderr)
    else:
        print(f"Wrote {len(examples)} examples to stdout", file=sys.stderr)


if __name__ == "__main__":
    main()

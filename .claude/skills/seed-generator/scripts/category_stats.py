#!/usr/bin/env python3
"""
Analyzes category distribution in a seed dataset.

Usage:
    python category_stats.py <jsonl_file>

Example:
    python category_stats.py data/seed/examples.jsonl

Output:
    - Count and percentage for each label category
    - Warnings for underrepresented categories
    - Distribution balance assessment
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict


def analyze_file(filepath: Path) -> Dict[str, Any]:
    """Analyze category distribution in JSONL file."""
    stats = {
        "total": 0,
        "actions": defaultdict(int),
        "resource_types": defaultdict(int),
        "sensitivities": defaultdict(int),
        "sources": defaultdict(int),
        "reviewed": {"true": 0, "false": 0},
    }

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    example = json.loads(line)
                except json.JSONDecodeError:
                    continue

                stats["total"] += 1

                # Extract labels
                if isinstance(example, dict):
                    labels = example.get("labels", {})
                    if isinstance(labels, dict):
                        action = labels.get("action")
                        if action:
                            stats["actions"][action] += 1

                        resource = labels.get("resource_type")
                        if resource:
                            stats["resource_types"][resource] += 1
                        else:
                            stats["resource_types"]["null"] += 1

                        sensitivity = labels.get("sensitivity")
                        if sensitivity:
                            stats["sensitivities"][sensitivity] += 1
                        else:
                            stats["sensitivities"]["null"] += 1

                    source = example.get("source")
                    if source:
                        stats["sources"][source] += 1

                    reviewed = example.get("reviewed", False)
                    if reviewed:
                        stats["reviewed"]["true"] += 1
                    else:
                        stats["reviewed"]["false"] += 1

    except IOError as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)

    return stats


def print_category_table(title: str, counts: Dict[str, int], total: int):
    """Print a category distribution table."""
    print(f"\n{title}")
    print("─" * 60)

    if not counts:
        print("  (No data)")
        return

    # Sort by count descending
    sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)

    for category, count in sorted_counts:
        pct = 100 * count / total if total > 0 else 0
        bar_width = int(pct / 2)  # Scale to fit 50 chars
        bar = "█" * bar_width
        print(f"  {category:15s} {count:6d} ({pct:5.1f}%) {bar}")


def check_balance(counts: Dict[str, int], total: int, category_type: str):
    """Check if distribution is balanced and print warnings."""
    if not counts or total == 0:
        return

    num_categories = len(counts)
    expected_per_category = total / num_categories

    warnings = []

    for category, count in counts.items():
        pct = 100 * count / total

        # Thresholds for warning
        if pct < 5:
            warnings.append(
                f"⚠ '{category}' is underrepresented ({pct:.1f}%, {count} examples). "
                f"Target is ~{expected_per_category:.0f} per category."
            )
        elif pct > 50:
            warnings.append(
                f"⚠ '{category}' is overrepresented ({pct:.1f}%, {count} examples). "
                f"Consider balancing with other categories."
            )

    if warnings:
        print(f"\n{category_type} Balance Assessment:")
        for warning in warnings:
            print(f"  {warning}")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python category_stats.py <jsonl_file>")
        print("\nExample:")
        print("  python category_stats.py data/seed/examples.jsonl")
        sys.exit(1)

    filepath = Path(sys.argv[1])
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    stats = analyze_file(filepath)
    total = stats["total"]

    print(f"\n{'=' * 60}")
    print(f"CATEGORY DISTRIBUTION ANALYSIS")
    print(f"{'=' * 60}")
    print(f"\nFile: {filepath}")
    print(f"Total examples: {total}\n")

    if total == 0:
        print("No valid examples found.")
        sys.exit(1)

    # Print action distribution
    print_category_table("ACTION DISTRIBUTION:", dict(stats["actions"]), total)
    check_balance(dict(stats["actions"]), total, "Action")

    # Print resource_type distribution
    print_category_table("RESOURCE TYPE DISTRIBUTION:", dict(stats["resource_types"]), total)
    check_balance(
        {k: v for k, v in stats["resource_types"].items() if k != "null"},
        total,
        "Resource Type",
    )

    # Print sensitivity distribution
    print_category_table("SENSITIVITY DISTRIBUTION:", dict(stats["sensitivities"]), total)
    check_balance(
        {k: v for k, v in stats["sensitivities"].items() if k != "null"},
        total,
        "Sensitivity",
    )

    # Print source distribution
    print_category_table("SOURCE DISTRIBUTION:", dict(stats["sources"]), total)

    # Print review status
    print(f"\nREVIEW STATUS:")
    print("─" * 60)
    reviewed = stats["reviewed"]["true"]
    not_reviewed = stats["reviewed"]["false"]
    reviewed_pct = 100 * reviewed / total if total > 0 else 0
    print(f"  Reviewed:     {reviewed:6d} ({reviewed_pct:5.1f}%)")
    print(f"  Not reviewed: {not_reviewed:6d} ({100 - reviewed_pct:5.1f}%)")

    if reviewed_pct < 5:
        print(f"\n⚠ Only {reviewed_pct:.1f}% of examples have been reviewed. Consider manual curation.")
    elif reviewed_pct > 50:
        print(f"\n✓ Good coverage: {reviewed_pct:.1f}% of examples have been reviewed.")

    print(f"\n{'=' * 60}\n")

    # Summary recommendations
    print("RECOMMENDATIONS:")
    print("─" * 60)

    underrepresented = []
    for category, count in stats["actions"].items():
        pct = 100 * count / total
        if pct < 10:
            underrepresented.append((category, pct))

    if underrepresented:
        print("\nNext batch should focus on:")
        for category, pct in sorted(underrepresented, key=lambda x: x[1]):
            print(f"  - More '{category}' examples (currently {pct:.1f}%)")
    else:
        print("\n✓ Action distribution looks balanced!")

    if reviewed_pct < 15:
        print("\nConsider manual review of ~15% of examples for quality assurance.")

    print("\n")


if __name__ == "__main__":
    main()

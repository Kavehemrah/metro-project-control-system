"""Read-only diagnostic for unmatched activity-revenue rows in the source workbook.

Run from the repository root:
    python scripts/diagnose_activity_revenue_matching.py
Optionally pass a workbook path. This script prints candidate activities only;
it never changes the workbook, mappings, or database.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl

from imports.import_excel import (
    _normal_key,
    _parse_operational_plan,
    _parse_physical_progress,
)

TARGETS = [
    ("تونل شماره 2", "خط شرقی", "انتقال، مونتاژ و نصب خط", 6),
    ("تونل شماره 3", "خط شرقی", "بتن ریزی دیواره داکت", 9),
    ("تونل شماره 3", "خط غربی", "بتن ریزی دیواره داکت", 11),
    ("تونل شماره 4", "خط شرقی", "پخش و مونتاژ ریل باقی مانده", 13),
]


def score(target: str, candidate: str) -> float:
    return difflib.SequenceMatcher(
        None, _normal_key(target), _normal_key(candidate)
    ).ratio()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workbook",
        nargs="?",
        type=Path,
        default=ROOT / "metro-project-control-system.xlsx",
        help="Workbook to inspect (defaults to the project workbook).",
    )
    path = parser.parse_args().workbook.expanduser().resolve()
    if not path.is_file():
        print(f"ERROR: workbook not found: {path}", file=sys.stderr)
        return 2

    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    try:
        physical = _parse_physical_progress(wb)
        plan = _parse_operational_plan(wb)
        print("WORKBOOK:", path)
        print("OPERATIONAL PLAN ACTIVITIES:", len(plan))
        print("PHYSICAL PROGRESS PACKAGES:", len(physical))
        print("\nACTIVITY PLAN ROWS:")
        for item in plan:
            print(
                f"  row {item['row_no']}: {item['position']} / {item['zone']} / "
                f"{item['title']} | remaining={item['remaining']:,.3f}"
            )

        print("\nUNMATCHED REVENUE ROW CANDIDATES:")
        for position, zone, title, source_row in TARGETS:
            print(f"\nExcel row {source_row}: {position} / {zone} / {title}")
            ranked = sorted(
                (
                    (
                        score(title, item["title"]),
                        score(position, item["position"]),
                        score(zone, item["zone"]),
                        item,
                    )
                    for item in plan
                ),
                key=lambda entry: (
                    entry[0] + 0.35 * entry[1] + 0.25 * entry[2]
                ),
                reverse=True,
            )
            for title_score, position_score, zone_score, item in ranked[:5]:
                combined = title_score + 0.35 * position_score + 0.25 * zone_score
                print(
                    f"  candidate row {item['row_no']} | score={combined:.3f} "
                    f"(title={title_score:.3f}, position={position_score:.3f}, "
                    f"zone={zone_score:.3f}) | {item['position']} / "
                    f"{item['zone']} / {item['title']}"
                )

        print(
            "\nNOTE: Similarity scores are diagnostic hints only. "
            "No activity mapping or financial value was changed."
        )
    finally:
        wb.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

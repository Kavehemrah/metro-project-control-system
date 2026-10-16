"""Validate the real Excel workbook using a temporary SQLite database.

Run from the repository root:
    python scripts/validate_excel_import.py
Or pass another workbook:
    python scripts/validate_excel_import.py path/to/workbook.xlsx
The repository's data/project.db is never opened or modified by this script.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# When invoked as "python scripts/validate_excel_import.py", Python puts the
# scripts/ directory on sys.path, not the repository root. Add the root before
# importing the application packages so the documented command works directly.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.db import connect
from imports.import_excel import import_workbook


def validate_workbook(workbook: Path) -> int:
    workbook = workbook.expanduser().resolve()
    if not workbook.is_file():
        print(f"ERROR: workbook not found: {workbook}", file=sys.stderr)
        return 2

    failures: list[str] = []
    with TemporaryDirectory(prefix="metro-validation-") as folder:
        db_path = Path(folder) / "validation.db"
        try:
            report = import_workbook(str(workbook), db_path=db_path)
        except Exception as error:
            print(f"IMPORT FAILED: {type(error).__name__}: {error}", file=sys.stderr)
            return 1

        conn = connect(db_path)
        try:
            project = conn.execute(
                "SELECT name, contract_value FROM project WHERE id=1"
            ).fetchone()
            kpi = conn.execute(
                """
                SELECT physical_progress, revenue, cost, balance, revenue_progress
                FROM kpi WHERE project_id=1 ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            activity_revenue_rows = conn.execute(
                """
                SELECT COUNT(*) FROM revenue_entry
                WHERE project_id=1 AND source='EXCEL_ACTIVITY' AND activity_id IS NOT NULL
                """
            ).fetchone()[0]
            unmatched = conn.execute(
                """
                SELECT source_row_no, position, zone, title, unit,
                       unit_rate, total_qty, amount, status
                FROM unmatched_activity_revenue
                WHERE project_id=1 AND status='CURRENT'
                ORDER BY source_row_no
                """
            ).fetchall()
            duplicate_source_rows = conn.execute(
                """
                SELECT source_row_no, COUNT(*) AS n
                FROM unmatched_activity_revenue
                WHERE project_id=1 AND status='CURRENT'
                GROUP BY source_row_no HAVING COUNT(*) > 1
                """
            ).fetchall()
            bad_amounts = [
                dict(row) for row in unmatched
                if not math.isclose(
                    float(row["amount"] or 0),
                    float(row["unit_rate"] or 0) * float(row["total_qty"] or 0),
                    rel_tol=1e-9,
                    abs_tol=0.01,
                )
            ]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()

        unmatched_total = sum(float(row["amount"] or 0) for row in unmatched)
        print("WORKBOOK:", workbook)
        print("PROJECT:", dict(project) if project else None)
        print("KPI:", tuple(kpi) if kpi else None)
        print("ACTIVITIES:", report.get("activities", 0))
        print("ACTIVITY REVENUE ROWS:", activity_revenue_rows)
        print("UNMATCHED REVENUE ROWS:", len(unmatched))
        print(f"UNMATCHED REVENUE TOTAL (IRR): {unmatched_total:,.2f}")
        print("UNMATCHED REVENUE DETAILS:")
        for row in unmatched:
            print(
                f"  Excel row {row['source_row_no']}: "
                f"{row['position']} / {row['zone']} / {row['title']} | "
                f"{row['unit_rate']:,.2f} × {row['total_qty']:,.2f} "
                f"= {row['amount']:,.2f} IRR"
            )
        print("IMPORT WARNINGS:", len(report.get("warnings", [])))
        print("DATABASE INTEGRITY:", integrity)
        print("FOREIGN KEY ERRORS:", len(foreign_key_errors))

        if not project or float(project["contract_value"] or 0) <= 0:
            failures.append("contract value is missing or non-positive")
        if not kpi:
            failures.append("KPI row is missing")
        if int(report.get("activities", 0)) <= 0:
            failures.append("no activities were imported")
        if int(report.get("physical_rows", 0)) <= 0:
            failures.append("no physical-progress rows were imported")
        if activity_revenue_rows <= 0:
            failures.append("no confirmed activity-revenue rows were imported")
        if int(report.get("unmatched_activity_revenue_rows", -1)) != len(unmatched):
            failures.append("import report unmatched-row count differs from the database")
        if not math.isclose(
            float(report.get("unmatched_activity_revenue_amount", 0) or 0),
            unmatched_total,
            rel_tol=1e-9,
            abs_tol=0.01,
        ):
            failures.append("import report unmatched amount differs from the database")
        if duplicate_source_rows:
            failures.append("duplicate current quarantine records exist for a source row")
        if bad_amounts:
            failures.append("one or more quarantined amounts do not equal unit rate × quantity")
        if integrity != "ok":
            failures.append(f"SQLite integrity check returned {integrity!r}")
        if foreign_key_errors:
            failures.append(f"foreign key check found {len(foreign_key_errors)} error(s)")

        if failures:
            print("\nVALIDATION FAILED:")
            for failure in failures:
                print(" -", failure)
            return 1

        print("\nVALIDATION PASSED. Temporary database will be removed automatically.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_workbook = REPOSITORY_ROOT / "metro-project-control-system.xlsx"
    parser.add_argument(
        "workbook",
        nargs="?",
        type=Path,
        default=default_workbook,
        help=f"Excel workbook to validate (default: {default_workbook})",
    )
    args = parser.parse_args()
    return validate_workbook(args.workbook)


if __name__ == "__main__":
    raise SystemExit(main())

"""Immutable schedule baseline snapshots and variance reporting."""
from __future__ import annotations

import sqlite3
from datetime import date

from app.services.dates import normalize_date
from app.services.forecast import calculate_schedule
from app.services.work_calendar import count_scheduled_days, project_holidays


def create_schedule_baseline(
    conn: sqlite3.Connection,
    project_id: int = 1,
    name: str = "Baseline",
    notes: str | None = None,
) -> int:
    """Capture the current dated schedule, dependencies, and project holidays.

    Baseline rows are append-only snapshots. Updating the live activity schedule
    later does not alter any previously captured baseline.
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("Baseline name is required.")

    activities = conn.execute(
        """
        SELECT id, row_no, position, zone, title, quantity, unit,
               start_date, finish_date, duration_days
        FROM activity WHERE project_id=? ORDER BY row_no, id
        """,
        (project_id,),
    ).fetchall()
    if not activities:
        raise ValueError("There are no activities to baseline.")

    missing = [
        str(row["row_no"] if row["row_no"] is not None else row["id"])
        for row in activities
        if not row["start_date"] or not row["finish_date"]
    ]
    if missing:
        raise ValueError(
            "Every activity must have a planned start and finish date. Missing rows: "
            + ", ".join(missing)
        )

    holidays = project_holidays(conn, project_id)
    snapshot_rows = []
    for row in activities:
        start = date.fromisoformat(normalize_date(row["start_date"]))
        finish = date.fromisoformat(normalize_date(row["finish_date"]))
        if finish < start:
            raise ValueError(f"Activity {row['id']} has a finish date before its start date.")
        duration = count_scheduled_days(start, finish, holidays)
        if duration < 1:
            raise ValueError(f"Activity {row['id']} has no scheduled days in its date range.")
        snapshot_rows.append((row, duration))

    conn.execute("SAVEPOINT create_schedule_baseline")
    try:
        cursor = conn.execute(
            """
            INSERT INTO schedule_baseline(project_id, name, notes, activity_count)
            VALUES(?,?,?,?)
            """,
            (project_id, name, notes, len(snapshot_rows)),
        )
        baseline_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO schedule_baseline_activity(
                baseline_id, activity_id, row_no, position, zone, title,
                quantity, unit, start_date, finish_date, duration_days
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    baseline_id, row["id"], row["row_no"], row["position"],
                    row["zone"], row["title"], row["quantity"], row["unit"],
                    normalize_date(row["start_date"]), normalize_date(row["finish_date"]), duration,
                )
                for row, duration in snapshot_rows
            ],
        )
        dependencies = conn.execute(
            """
            SELECT activity_id, predecessor_activity_id, relation_type, lag_days
            FROM activity_dependency WHERE project_id=?
            """,
            (project_id,),
        ).fetchall()
        conn.executemany(
            """
            INSERT INTO schedule_baseline_dependency(
                baseline_id, activity_id, predecessor_activity_id, relation_type, lag_days
            ) VALUES(?,?,?,?,?)
            """,
            [
                (
                    baseline_id, row["activity_id"], row["predecessor_activity_id"],
                    row["relation_type"], row["lag_days"],
                )
                for row in dependencies
            ],
        )
        conn.executemany(
            """
            INSERT INTO schedule_baseline_holiday(baseline_id, holiday_date, title)
            SELECT ?, holiday_date, title FROM calendar_holiday
            WHERE project_id=?
            """,
            [(baseline_id, project_id)],
        )
        conn.execute("RELEASE SAVEPOINT create_schedule_baseline")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT create_schedule_baseline")
        conn.execute("RELEASE SAVEPOINT create_schedule_baseline")
        raise
    return baseline_id


def list_schedule_baselines(conn: sqlite3.Connection, project_id: int = 1) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, name, notes, activity_count, created_at
            FROM schedule_baseline WHERE project_id=?
            ORDER BY id DESC
            """,
            (project_id,),
        ).fetchall()
    ]


def compare_schedule_to_baseline(
    conn: sqlite3.Connection,
    baseline_id: int,
    as_of: date | None = None,
) -> dict:
    """Compare the live forecast with an immutable baseline snapshot."""
    baseline = conn.execute(
        "SELECT id, project_id, name, created_at FROM schedule_baseline WHERE id=?",
        (baseline_id,),
    ).fetchone()
    if baseline is None:
        raise ValueError("Schedule baseline not found.")

    project_id = int(baseline["project_id"])
    forecast = calculate_schedule(conn, project_id=project_id, as_of=as_of, persist=False)
    if forecast.get("cycle"):
        return {
            "baseline_id": baseline_id,
            "baseline_name": baseline["name"],
            "cycle": forecast["cycle"],
            "activities": [],
        }

    current = {item["id"]: item for item in forecast["activities"]}
    snapshots = conn.execute(
        """
        SELECT activity_id, row_no, title, start_date, finish_date, duration_days
        FROM schedule_baseline_activity WHERE baseline_id=?
        ORDER BY row_no, activity_id
        """,
        (baseline_id,),
    ).fetchall()
    items = []
    for row in snapshots:
        live = current.get(row["activity_id"])
        forecast_start = live["forecast_start"] if live else None
        forecast_finish = live["forecast_finish"] if live else None
        baseline_start = date.fromisoformat(row["start_date"])
        baseline_finish = date.fromisoformat(row["finish_date"])
        start_variance = (forecast_start - baseline_start).days if forecast_start else None
        finish_variance = (forecast_finish - baseline_finish).days if forecast_finish else None
        items.append(
            {
                "activity_id": row["activity_id"],
                "row_no": row["row_no"],
                "title": row["title"],
                "baseline_start": baseline_start,
                "baseline_finish": baseline_finish,
                "baseline_duration_days": row["duration_days"],
                "forecast_start": forecast_start,
                "forecast_finish": forecast_finish,
                "start_variance_calendar_days": start_variance,
                "finish_variance_calendar_days": finish_variance,
                "delayed": finish_variance is not None and finish_variance > 0,
            }
        )

    baseline_finish = max(
        (date.fromisoformat(row["finish_date"]) for row in snapshots),
        default=None,
    )
    forecast_finish = max(
        (item["forecast_finish"] for item in forecast["activities"] if item["forecast_finish"]),
        default=None,
    )
    return {
        "baseline_id": baseline_id,
        "baseline_name": baseline["name"],
        "baseline_project_finish": baseline_finish,
        "forecast_project_finish": forecast_finish,
        "project_finish_variance_calendar_days": (
            (forecast_finish - baseline_finish).days
            if baseline_finish and forecast_finish else None
        ),
        "cycle": [],
        "activities": items,
    }

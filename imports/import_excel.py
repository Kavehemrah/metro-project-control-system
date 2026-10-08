from __future__ import annotations

import re
from pathlib import Path

import openpyxl

from app.db import connect
from app.services.control import detect_finance_discrepancy, scan_formula_errors


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\u200c", " ").replace("\n", " ").strip()


def _clean_number(value) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace(" ", "").replace("%", "")
    text = text.replace("٫", ".").replace("٬", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _row_text(row) -> str:
    return " ".join(_clean_text(value) for value in row if _clean_text(value))


def _find_header_row(sheet, required_groups, max_rows=20):
    # Each group contains alternative labels; at least one label from every group is required.
    for row_no in range(1, min(sheet.max_row, max_rows) + 1):
        values = [_clean_text(sheet.cell(row_no, col).value) for col in range(1, sheet.max_column + 1)]
        if all(any(keyword in value for keyword in group for value in values) for group in required_groups):
            return row_no
    return None


def _find_columns(sheet, header_row, aliases):
    columns = {}
    for col in range(1, sheet.max_column + 1):
        value = _clean_text(sheet.cell(header_row, col).value)
        if not value:
            continue
        normalized_value = _normal_key(value)
        for key, words in aliases.items():
            if key in columns:
                continue
            if any(_normal_key(word) in normalized_value for word in words):
                columns[key] = col
    return columns


def _normal_key(value: str) -> str:
    return (
        value.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", " ")
        .replace(" ", "")
        .strip()
        .lower()
    )


def _parse_project_metadata(workbook):
    candidates = ["جلد"]
    sheet = next((workbook[name] for name in candidates if name in workbook.sheetnames), None)
    if sheet is None:
        return {"name": "پروژه مترو", "period_label": "دوره گزارش"}

    return {
        "name": _clean_text(sheet["B2"].value) or "پروژه مترو",
        "period_label": _clean_text(sheet["J1"].value) or "دوره گزارش",
    }


def _parse_summary_metrics(workbook):
    if "خلاصه گزارش" not in workbook.sheetnames:
        raise ValueError("Sheet 'خلاصه گزارش' was not found.")

    sheet = workbook["خلاصه گزارش"]
    summary = {
        "physical_progress": _clean_number(sheet["F5"].value),
        "revenue": _clean_number(sheet["F11"].value),
        "cost": _clean_number(sheet["F20"].value),
        "adjustment": 0.0,
        "other_income": 0.0,
        "monthly_finance": [],
    }

    for row in sheet.iter_rows(values_only=True):
        text = _row_text(row)
        if not text:
            continue
        numeric = [_clean_number(v) for v in row if isinstance(v, (int, float))]
        if not numeric:
            continue
        value = numeric[-1]

        if "درصد پیشرفت فیزیکی پروژه" in text:
            summary["physical_progress"] = value
        elif "تعدیل" in text:
            summary["adjustment"] = value
        elif "سایر" in text and "درآمد" in text:
            summary["other_income"] = value

    summary["total_revenue"] = (
        summary["revenue"] + summary["adjustment"] + summary["other_income"]
    )

    header_row = None
    for row_no in range(1, min(sheet.max_row, 12) + 1):
        text = _row_text(
            [sheet.cell(row_no, col).value for col in range(1, min(sheet.max_column, 10) + 1)]
        )
        if "مهر" in text or "آبان" in text:
            header_row = row_no
            break

    if header_row:
        month_columns = []
        for col in range(1, sheet.max_column + 1):
            label = _clean_text(sheet.cell(header_row, col).value)
            if label and label not in {"مجموع", "جمع"}:
                if any(month in label for month in ["مهر", "آبان", "شهریور", "آذر", "دی", "بهمن", "اسفند"]):
                    month_columns.append((col, label))
        for col, label in month_columns:
            revenue = 0.0
            cost = 0.0
            for row_no in range(header_row + 1, sheet.max_row + 1):
                label_text = _row_text(
                    [sheet.cell(row_no, c).value for c in range(1, min(sheet.max_column, 6) + 1)]
                )
                value = _clean_number(sheet.cell(row_no, col).value)
                if "هزینه" in label_text and ("جمع" in label_text or "کل" in label_text):
                    cost = value
                if "درآمد" in label_text and ("جمع" in label_text or "کل" in label_text):
                    revenue = value
            month_name = label
            if revenue or cost:
                summary["monthly_finance"].append((month_name, revenue, cost))

    if not summary["monthly_finance"]:
        summary["monthly_finance"] = [
            ("دوره گزارش", summary["total_revenue"], summary["cost"])
        ]

    summary["balance"] = summary["total_revenue"] - summary["cost"]
    return summary


def _find_sheet(workbook, names):
    for name in names:
        if name in workbook.sheetnames:
            return workbook[name]
    return None


def _parse_physical_progress(workbook):
    sheet = _find_sheet(workbook, ["پيشرفت فيزيكي", "پیشرفت فیزیکی", "پیشرفت فیزیکی11"])
    if sheet is None:
        return {}

    header_row = _find_header_row(
        sheet,
        [
            ["فعالیت", "شرح"],
            ["جبهه", "موقعیت"],
            ["واحد"],
        ],
        max_rows=25,
    )
    if header_row is None:
        return {}

    columns = _find_columns(
        sheet,
        header_row,
        {
            "zone": ["جبهه", "موقعیت"],
            "title": ["شرح فعالیت", "شرح", "فعالیت"],
            "unit": ["واحد"],
            "total": ["حجم کل", "مقدار کل", "کل حجم"],
            "opening": ["عملکرد ابتدای دوره", "ابتدای دوره"],
            "remaining": ["باقی مانده", "باقیمانده"],
        },
    )

    month_columns = []
    period_header_row = min(sheet.max_row, header_row + 1)
    for col in range(1, sheet.max_column + 1):
        label = _clean_text(sheet.cell(period_header_row, col).value)
        if any(month in label for month in ["مهر", "آبان", "شهریور", "آذر", "دی", "بهمن", "اسفند"]):
            month_columns.append((col, label))

    result = {}
    for row_no in range(header_row + 1, sheet.max_row + 1):
        title = _clean_text(sheet.cell(row_no, columns.get("title", 0)).value) if columns.get("title") else ""
        zone = _clean_text(sheet.cell(row_no, columns.get("zone", 0)).value) if columns.get("zone") else ""
        if not title:
            continue

        key = (_normal_key(zone), _normal_key(title))
        item = {
            "zone": zone,
            "title": title,
            "unit": _clean_text(sheet.cell(row_no, columns["unit"]).value) if columns.get("unit") else "",
            "total": _clean_number(sheet.cell(row_no, columns["total"]).value) if columns.get("total") else 0,
            "opening": _clean_number(sheet.cell(row_no, columns["opening"]).value) if columns.get("opening") else 0,
            "remaining": _clean_number(sheet.cell(row_no, columns["remaining"]).value) if columns.get("remaining") else 0,
            "periods": [],
        }

        for col, label in month_columns:
            value = _clean_number(sheet.cell(row_no, col).value)
            if value:
                item["periods"].append((label, value))

        result[key] = item

    return result


def _parse_operational_plan(workbook):
    sheet = _find_sheet(workbook, ["برنامه عملیاتی", "برنامه اجرایی"])
    if sheet is None:
        return []

    header_row = _find_header_row(
        sheet,
        [
            ["موقعیت", "جبهه کاری"],
            ["شرح فعالیت", "شرح"],
            ["حجم باقی مانده", "باقی مانده"],
        ],
        max_rows=25,
    )
    if header_row is None:
        return []

    columns = _find_columns(
        sheet,
        header_row,
        {
            "position": ["موقعیت"],
            "zone": ["جبهه کاری", "جبهه"],
            "title": ["شرح فعالیت", "شرح"],
            "remaining": ["حجم باقی مانده", "حجم باقیمانده", "باقی مانده"],
            "start": ["شروع"],
            "finish": ["پایان", "اتمام"],
            "duration": ["مدت"],
            "daily": ["برنامه روزانه", "روزانه", "راندمان"],
        },
    )

    result = []
    fixed_cols = set(columns.values())

    # Daily columns are only accepted when a nearby header cell contains a date.
    daily_columns = []
    for col in range(1, sheet.max_column + 1):
        if col in fixed_cols:
            continue
        labels = []
        for r in range(max(1, header_row - 3), header_row + 1):
            value = sheet.cell(r, col).value
            if value is not None:
                labels.append(_clean_text(value))
        date_value = next(
            (sheet.cell(r, col).value for r in range(max(1, header_row - 3), header_row + 1)
             if hasattr(sheet.cell(r, col).value, "strftime")),
            None,
        )
        if date_value is not None:
            daily_columns.append((col, date_value.strftime("%Y-%m-%d")))
            continue

        label = next((v for v in labels if re.search(r"140[0-9][/\-]", v)), "")
        if label:
            daily_columns.append((col, label))

    for row_no in range(header_row + 1, sheet.max_row + 1):
        title = _clean_text(sheet.cell(row_no, columns.get("title", 0)).value) if columns.get("title") else ""
        zone = _clean_text(sheet.cell(row_no, columns.get("zone", 0)).value) if columns.get("zone") else ""
        position = _clean_text(sheet.cell(row_no, columns.get("position", 0)).value) if columns.get("position") else ""
        if not title:
            continue

        item = {
            "row_no": row_no,
            "position": position,
            "zone": zone,
            "title": title,
            "remaining": _clean_number(sheet.cell(row_no, columns["remaining"]).value) if columns.get("remaining") else 0,
            "start": _clean_text(sheet.cell(row_no, columns["start"]).value) if columns.get("start") else "",
            "finish": _clean_text(sheet.cell(row_no, columns["finish"]).value) if columns.get("finish") else "",
            "duration": int(_clean_number(sheet.cell(row_no, columns["duration"]).value)) if columns.get("duration") else 0,
            "daily": _clean_number(sheet.cell(row_no, columns["daily"]).value) if columns.get("daily") else 0,
            "daily_plan": [],
        }

        for col, label in daily_columns:
            value = _clean_number(sheet.cell(row_no, col).value)
            if value:
                item["daily_plan"].append((label, value))

        result.append(item)

    return result


def _parse_detail_cost(workbook):
    sheet = _find_sheet(workbook, ["عملکرد مالی-هزینه‌ها", "عملکرد مالی-هزینه ها"])
    if sheet is None:
        return 0.0

    candidates = []
    fallback = []
    for row in sheet.iter_rows(values_only=True):
        text = _row_text(row)
        if "جمع" not in text:
            continue
        numeric = [_clean_number(v) for v in row if isinstance(v, (int, float))]
        if not numeric:
            continue
        value = max(numeric)
        fallback.append(value)
        if "هزینه" in text or "هزينه" in text:
            candidates.append(value)
    if candidates:
        return max(candidates)
    return max(fallback) if fallback else 0.0


def _parse_financial_entries(workbook):
    sheet = workbook["خلاصه گزارش"] if "خلاصه گزارش" in workbook.sheetnames else None
    if sheet is None:
        return {"revenue": [], "cost": []}

    revenue_entries = []
    cost_entries = []

    for row_no in range(7, 12):
        label = _clean_text(sheet.cell(row_no, 2).value)
        if not label:
            continue
        for col_no in range(3, 6):
            period = _clean_text(sheet.cell(3, col_no).value)
            amount = _clean_number(sheet.cell(row_no, col_no).value)
            if amount:
                revenue_entries.append({
                    "category": label,
                    "period": period or "مجموع",
                    "amount": amount,
                })

    for row_no in range(14, 35):
        label = _clean_text(sheet.cell(row_no, 2).value)
        if not label:
            continue
        for col_no in range(3, 6):
            period = _clean_text(sheet.cell(3, col_no).value)
            amount = _clean_number(sheet.cell(row_no, col_no).value)
            if amount:
                cost_entries.append({
                    "category": label,
                    "period": period or "مجموع",
                    "amount": amount,
                })

    return {"revenue": revenue_entries, "cost": cost_entries}


def _matches_any(text: str, candidates: list[str]) -> bool:
    normalized_text = _normal_key(text)
    normalized_candidates = {_normal_key(candidate) for candidate in candidates}
    return any(candidate in normalized_text for candidate in normalized_candidates)


def _resource_category_for_sheet(title: str) -> str | None:
    if _matches_any(title, ["ماشین", "ماشين", "دستگاه", "ماشین آلات", "ماشين الات", "ماشینالات"]):
        return "ماشین‌آلات"
    if _matches_any(title, ["نیروی", "نيروي", "انسانی", "انساني", "پرسنل", "شغل"]):
        return "نیروی انسانی"
    if _matches_any(title, ["مواد", "مصالح", "ماده", "مواد و مصالح", "مصالح اصلی"]):
        return "مصالح"
    return None


def _parse_resources(workbook):
    resources = []
    seen = set()

    for sheet in workbook.worksheets:
        category = _resource_category_for_sheet(sheet.title)
        if category is None:
            continue

        for row in sheet.iter_rows(min_row=3, max_row=sheet.max_row):
            values = [_clean_text(cell.value) for cell in row[:6]]
            if not any(values):
                continue
            if any(keyword in _normal_key(values[0]) for keyword in ["رديف", "ردیف", "سطر"]) and len(values) > 1:
                continue

            title = values[1] if len(values) > 1 else ""
            if not title:
                continue
            title_key = _normal_key(title)
            if title_key in {"ماشینآلات", "نيرويانسانی", "موادومصالحاصلی", "موادومصالح", "دستگاه", "نیرویانسانی"}:
                continue

            unit = values[2] if len(values) > 2 else ""
            required = _clean_number(values[3] if len(values) > 3 else 0)
            available = _clean_number(values[4] if len(values) > 4 else 0)

            if not title or (required == 0 and available == 0 and not unit):
                continue

            key = (category, title)
            if key in seen:
                continue
            seen.add(key)
            resources.append(
                {
                    "category": category,
                    "title": title,
                    "required": required,
                    "available": available,
                    "unit": unit,
                }
            )

    return resources


def _upsert_activity(conn, item, physical, row_no):
    key = (_normal_key(item["zone"]), _normal_key(item["title"]))
    p = physical.get(key, {})

    total = p.get("total", 0) or 0
    remaining = item["remaining"] or p.get("remaining", 0) or 0
    opening = p.get("opening", 0) or 0
    actual_period_qty = sum(value for _period, value in p.get("periods", []))
    actual_total = opening + actual_period_qty
    if total <= 0:
        total = remaining + actual_total

    existing = None
    if item.get("row_no"):
        existing = conn.execute(
            "SELECT id FROM activity WHERE project_id=1 AND row_no=?",
            (item["row_no"],),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id FROM activity WHERE project_id=1 AND title=? AND zone=? AND position=?",
            (item["title"], item["zone"], item["position"]),
        ).fetchone()
        if existing is None:
            existing = conn.execute(
                "SELECT id FROM activity WHERE project_id=1 AND title=? AND zone=?",
                (item["title"], item["zone"]),
            ).fetchone()

    values = (
        item["position"],
        item["zone"],
        item["title"],
        total,
        remaining,
        p.get("unit", ""),
        item["start"],
        item["finish"],
        item["duration"],
        item["daily"],
        sum(value for _date, value in item["daily_plan"]),
        actual_total,
        (actual_total / total) if total else 0,
        0,
        "NORMAL",
    )

    if existing:
        conn.execute(
            """
            UPDATE activity SET position=?, zone=?, title=?, quantity=?, remaining_qty=?,
            unit=?, start_date=?, finish_date=?, duration_days=?, daily_target=?,
            planned_qty=?, actual_qty=?, progress=?, row_no=?, status=?
            WHERE id=?
            """,
            (*values[:13], row_no, values[14], existing["id"]),
        )
        activity_id = existing["id"]
    else:
        cursor = conn.execute(
            """
            INSERT INTO activity(
                project_id, row_no, position, zone, title, quantity, remaining_qty, unit,
                start_date, finish_date, duration_days, daily_target, planned_qty,
                actual_qty, progress, delay_days, status
            ) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (row_no, *values),
        )
        activity_id = cursor.lastrowid

    conn.execute("DELETE FROM activity_daily_plan WHERE activity_id=?", (activity_id,))
    for plan_date, quantity in item["daily_plan"]:
        conn.execute(
            """
            INSERT OR REPLACE INTO activity_daily_plan(activity_id, plan_date, quantity, source)
            VALUES(?,?,?,'EXCEL')
            """,
            (activity_id, plan_date, quantity),
        )

    conn.execute("DELETE FROM activity_period WHERE activity_id=?", (activity_id,))
    cumulative = opening
    for period, actual in p.get("periods", []):
        cumulative += actual
        planned = 0.0
        for planned_period, planned_value in item["daily_plan"]:
            if planned_period == period:
                planned += planned_value
        conn.execute(
            """
            INSERT INTO activity_period(
                activity_id, period, planned_qty, actual_qty, cumulative_qty,
                remaining_qty, achievement_pct, source
            ) VALUES(?,?,?,?,?,?,?,'EXCEL')
            """,
            (
                activity_id,
                period,
                planned,
                actual,
                cumulative,
                max(0.0, total - cumulative),
                (actual / planned * 100) if planned else 0,
            ),
        )


def import_workbook(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    workbook_values = openpyxl.load_workbook(path, data_only=True, read_only=False)
    workbook_formulas = openpyxl.load_workbook(path, data_only=False, read_only=False)

    metadata = _parse_project_metadata(workbook_values)
    metrics = _parse_summary_metrics(workbook_values)
    physical = _parse_physical_progress(workbook_values)
    operational = _parse_operational_plan(workbook_values)
    detail_cost = _parse_detail_cost(workbook_values)
    resources = _parse_resources(workbook_values)
    financial_entries = _parse_financial_entries(workbook_values)
    formula_errors = scan_formula_errors(workbook_formulas)

    conn = connect()
    try:
        conn.execute("BEGIN")

        conn.execute(
            """
            INSERT INTO project(id, name, code, contract_value, period_label, status)
            VALUES (1, ?, 'MB-1405-01', 0, ?, 'ACTIVE')
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                period_label=excluded.period_label,
                status=excluded.status
            """,
            (metadata["name"], metadata["period_label"]),
        )

        conn.execute("DELETE FROM kpi WHERE project_id=1")
        conn.execute(
            """
            INSERT INTO kpi(project_id, physical_progress, revenue, cost, balance, revenue_progress)
            VALUES(1,?,?,?,?,?)
            """,
            (
                metrics["physical_progress"],
                metrics["total_revenue"],
                metrics["cost"],
                metrics["balance"],
                0,
            ),
        )

        conn.execute("DELETE FROM monthly_finance WHERE project_id=1")
        for month_name, revenue, cost in metrics["monthly_finance"]:
            conn.execute(
                "INSERT INTO monthly_finance(project_id,month,revenue,cost) VALUES(1,?,?,?)",
                (month_name, revenue, cost),
            )

        conn.execute("DELETE FROM discrepancy WHERE project_id=1")
        conn.execute("DELETE FROM resource WHERE project_id=1")
        conn.execute("DELETE FROM revenue_entry WHERE project_id=1")
        conn.execute("DELETE FROM cost_entry WHERE project_id=1")
        conn.execute("DELETE FROM activity_dependency WHERE project_id=1")

        for item in resources:
            conn.execute(
                "INSERT INTO resource(project_id, category, title, required, available, unit) VALUES(1,?,?,?,?,?)",
                (item["category"], item["title"], item["required"], item["available"], item["unit"]),
            )

        for entry in financial_entries["revenue"]:
            conn.execute(
                "INSERT INTO revenue_entry(project_id, activity_id, period, category, amount, source) VALUES(1, NULL, ?, ?, ?, 'EXCEL')",
                (entry["period"], entry["category"], entry["amount"]),
            )

        for entry in financial_entries["cost"]:
            conn.execute(
                "INSERT INTO cost_entry(project_id, activity_id, period, category, amount, source) VALUES(1, NULL, ?, ?, ?, 'EXCEL')",
                (entry["period"], entry["category"], entry["amount"]),
            )

        # Do not destroy the existing model when a workbook layout cannot be parsed.
        if operational:
            conn.execute("DELETE FROM activity WHERE project_id=1")
            for item in operational:
                _upsert_activity(conn, item, physical, item["row_no"])
            infer_activity_dependencies = __import__('app.services.control', fromlist=['infer_activity_dependencies']).infer_activity_dependencies
            infer_activity_dependencies(conn, 1)
        else:
            conn.execute(
                "INSERT INTO discrepancy(project_id,title,detail,severity) VALUES(1,?,?,?)",
                (
                    "ورود برنامه عملیاتی انجام نشد",
                    "ساختار شیت «برنامه عملیاتی» شناسایی نشد؛ داده‌های فعالیت قبلی حفظ شدند.",
                    "HIGH",
                ),
            )

        discrepancy = detect_finance_discrepancy(metrics["cost"], detail_cost)
        if discrepancy:
            conn.execute(
                "INSERT INTO discrepancy(project_id,title,detail,severity) VALUES(1,?,?,?)",
                (discrepancy["title"], discrepancy["detail"], discrepancy["severity"]),
            )

        for error in formula_errors:
            conn.execute(
                "INSERT INTO discrepancy(project_id,title,detail,severity) VALUES(1,?,?,?)",
                (error["title"], error["detail"], error["severity"]),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        workbook_values.close()
        workbook_formulas.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python imports/import_excel.py <workbook.xlsx>")

    import_workbook(sys.argv[1])

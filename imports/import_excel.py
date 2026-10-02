from pathlib import Path

import openpyxl

from app.db import connect


def _clean_number(value):
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace(" ", "")
        cleaned = cleaned.replace("%", "")
        if not cleaned:
            return 0.0
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def _extract_numeric_values(row):
    values = []
    for value in row:
        if value is None:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
            continue
        if isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            try:
                values.append(float(text.replace(",", "")))
            except ValueError:
                pass
    return values


def _parse_monthly_finance(sheet):
    month_header = [sheet.cell(row=3, column=col).value for col in range(4, 7)]
    total_revenue = [
        sheet.cell(row=11, column=col).value or 0 for col in range(4, 7)
    ]
    total_cost = [
        sheet.cell(row=20, column=col).value or 0 for col in range(4, 7)
    ]

    months = []
    for month_name, revenue, cost in zip(month_header, total_revenue, total_cost):
        if month_name is None:
            continue
        label = str(month_name).strip()
        if label == "مجموع":
            continue
        months.append((label, float(revenue), float(cost)))
    return months


def _parse_summary_metrics(workbook):
    if "خلاصه گزارش" not in workbook.sheetnames:
        raise ValueError("Sheet 'خلاصه گزارش' was not found.")

    worksheet = workbook["خلاصه گزارش"]
    summary = {}

    summary["physical_progress"] = _clean_number(worksheet["F5"].value)
    summary["revenue"] = _clean_number(worksheet["F11"].value)
    summary["cost"] = _clean_number(worksheet["F20"].value)
    summary["balance"] = summary["revenue"] - summary["cost"]
    summary["total_revenue"] = summary["revenue"]
    summary["monthly_finance"] = _parse_monthly_finance(worksheet)

    for row in worksheet.iter_rows(values_only=True):
        row_text = " ".join(
            str(value).strip() for value in row if isinstance(value, str) and value.strip()
        )
        if not row_text:
            continue

        values = _extract_numeric_values(row)
        if "درصد پیشرفت فیزیکی پروژه" in row_text:
            summary["physical_progress"] = values[-1] if values else summary["physical_progress"]
        elif "درآمد" in row_text and "کارکرد" in row_text:
            summary["revenue"] = values[-1] if values else summary["revenue"]
        elif "تعدیل" in row_text:
            summary["adjustment"] = values[-1] if values else 0.0
        elif "سایر" in row_text and "مبلغ" not in row_text:
            summary["other_income"] = values[-1] if values else 0.0
        elif "جمع" in row_text and "درآمد" in row_text:
            summary["total_revenue"] = values[-1] if values else summary["total_revenue"]
        elif "جمع" in row_text and row[2] == "جمع" and row[5] is not None:
            if row[2] == "جمع" and row[5] is not None and row[1] is None:
                summary["cost"] = _clean_number(row[5])

    summary.setdefault("adjustment", 0.0)
    summary.setdefault("other_income", 0.0)
    summary.setdefault("total_revenue", summary["revenue"] + summary["adjustment"] + summary["other_income"])
    summary["balance"] = summary["total_revenue"] - summary["cost"]

    return summary


def _parse_project_metadata(workbook):
    if "جلد" not in workbook.sheetnames:
        raise ValueError("Sheet 'جلد' was not found.")

    sheet = workbook["جلد"]
    project_name = sheet["B2"].value or "پروژه مترو"
    period_label = sheet["J1"].value or "پروژه"
    return {
        "name": str(project_name),
        "period_label": str(period_label),
    }


def import_workbook(path):
    path = Path(path)
    workbook = openpyxl.load_workbook(
        path,
        data_only=True,
        read_only=True,
    )

    metadata = _parse_project_metadata(workbook)
    metrics = _parse_summary_metrics(workbook)

    conn = connect()
    conn.execute(
        """
        INSERT INTO project(id, name, code, contract_value, period_label, status)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            period_label=excluded.period_label,
            status=excluded.status
        """,
        (
            metadata["name"],
            "MB-1405-01",
            0,
            metadata["period_label"],
            "ACTIVE",
        ),
    )
    conn.execute("DELETE FROM kpi WHERE project_id=1")
    conn.execute(
        """
        INSERT INTO kpi(
            project_id,
            physical_progress,
            revenue,
            cost,
            balance,
            revenue_progress
        )
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
        if not month_name:
            continue
        conn.execute(
            "INSERT INTO monthly_finance(project_id, month, revenue, cost) VALUES(1,?,?,?)",
            (month_name, revenue, cost),
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python imports/import_excel.py <workbook.xlsx>"
        )

    import_workbook(sys.argv[1])

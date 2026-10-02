from pathlib import Path
import openpyxl

from app.db import connect


def import_workbook(path):
    path = Path(path)
    workbook = openpyxl.load_workbook(
        path,
        data_only=True,
        read_only=True,
    )

    if "خلاصه گزارش" not in workbook.sheetnames:
        raise ValueError("Sheet 'خلاصه گزارش' was not found.")

    worksheet = workbook["خلاصه گزارش"]
    physical = worksheet["F5"].value or 0
    revenue = worksheet["F11"].value or 0

    cost = 0
    for row in worksheet.iter_rows(
        min_row=14,
        max_row=53,
        values_only=True,
    ):
        if (
            isinstance(row[5], (int, float))
            and row[1]
            and "جمع" in str(row[1])
        ):
            cost = row[5]

    conn = connect()
    conn.execute(
        "DELETE FROM kpi WHERE project_id=1"
    )
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
            physical,
            revenue,
            cost,
            revenue - cost,
            0,
        ),
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

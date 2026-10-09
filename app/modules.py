import csv
import sqlite3
import zipfile
from pathlib import Path

from openpyxl.utils.exceptions import InvalidFileException
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db import connect
from .services.control import activity_control, update_activity_rollup
from .ui import BLUE, MUTED, TEXT


def _configure_table(table, headers):
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.verticalHeader().setDefaultSectionSize(36)


class RecordDialog(QDialog):
    def __init__(self, title, fields, initial=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setLayoutDirection(Qt.RightToLeft)
        self.fields = fields
        self.inputs = {}
        initial = initial or {}

        layout = QVBoxLayout(self)
        form = QFormLayout()
        for key, label, kind, options in fields:
            value = initial.get(key, options.get("default"))
            if kind == "text":
                widget = QLineEdit("" if value is None else str(value))
            elif kind in ("number", "percent"):
                widget = QDoubleSpinBox()
                widget.setRange(options.get("minimum", 0), options.get("maximum", 1_000_000_000))
                widget.setDecimals(options.get("decimals", 2))
                widget.setValue(float(value or 0) * (100 if kind == "percent" else 1))
                if kind == "percent":
                    widget.setSuffix("%")
            elif kind == "integer":
                widget = QSpinBox()
                widget.setRange(options.get("minimum", 0), options.get("maximum", 1_000_000))
                widget.setValue(int(value or 0))
            elif kind == "choice":
                widget = QComboBox()
                widget.addItems(options["choices"])
                if value in options["choices"]:
                    widget.setCurrentText(value)
            else:
                raise ValueError(f"Unsupported field type: {kind}")
            self.inputs[key] = (widget, kind, options)
            form.addRow(label, widget)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        result = {}
        for key, (widget, kind, _) in self.inputs.items():
            if kind == "text":
                result[key] = widget.text().strip()
            elif kind == "choice":
                result[key] = widget.currentText()
            else:
                value = widget.value()
                result[key] = value / 100 if kind == "percent" else value
        return result


class RecordPage(QWidget):
    def __init__(
        self,
        title,
        query,
        columns,
        fields,
        table_name,
        project_id=1,
        query_params=(),
    ):
        super().__init__()
        self.setLayoutDirection(Qt.RightToLeft)
        self.title = title
        self.query = query
        self.columns = columns
        self.fields = fields
        self.table_name = table_name
        self.project_id = project_id
        self.query_params = query_params
        self.records = []

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(12)
        heading = QLabel(title)
        heading.setStyleSheet(f"font-size:21px;font-weight:700;color:{TEXT};")
        root.addWidget(heading)

        actions = QHBoxLayout()
        self.add_button = QPushButton("افزودن")
        self.edit_button = QPushButton("ویرایش")
        self.delete_button = QPushButton("حذف")
        self.refresh_button = QPushButton("بروزرسانی")
        for button in (self.add_button, self.edit_button, self.delete_button, self.refresh_button):
            actions.addWidget(button)
        actions.addStretch()
        root.addLayout(actions)

        self.table = QTableWidget()
        _configure_table(self.table, [column[1] for column in columns])
        root.addWidget(self.table)

        self.add_button.clicked.connect(self.add_record)
        self.edit_button.clicked.connect(self.edit_record)
        self.delete_button.clicked.connect(self.delete_record)
        self.refresh_button.clicked.connect(self.refresh)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self.edit_record())
        self.refresh()

    def refresh(self):
        conn = connect()
        try:
            self.records = conn.execute(
                self.query,
                (self.project_id, *self.query_params),
            ).fetchall()
        finally:
            conn.close()

        self.table.setRowCount(len(self.records))
        for row_index, record in enumerate(self.records):
            for column_index, (key, _label, formatter) in enumerate(self.columns):
                value = record[key]
                text = formatter(value, record) if formatter else str(value if value is not None else "")
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row_index, column_index, item)
        self.table.resizeColumnsToContents()

    def _selected_record(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.records):
            QMessageBox.information(self, "انتخاب ردیف", "ابتدا یک ردیف را انتخاب کنید.")
            return None
        return self.records[row]

    def _dialog(self, title, initial=None):
        dialog = RecordDialog(title, self.fields, initial, self)
        if dialog.exec() != QDialog.Accepted:
            return None
        values = dialog.values()
        for key, label, kind, _options in self.fields:
            if kind == "text" and not values[key]:
                QMessageBox.warning(self, "ورودی ناقص", f"فیلد «{label}» الزامی است.")
                return None
        return values

    def _save_values(self, values, record_id=None):
        values = self.prepare_values(values)
        keys = list(values)
        conn = connect()
        try:
            if record_id is None:
                columns = ["project_id", *keys]
                placeholders = ", ".join("?" for _ in columns)
                conn.execute(
                    f"INSERT INTO {self.table_name} ({', '.join(columns)}) VALUES ({placeholders})",
                    [self.project_id, *(values[key] for key in keys)],
                )
            else:
                assignments = ", ".join(f"{key}=?" for key in keys)
                conn.execute(
                    f"UPDATE {self.table_name} SET {assignments} WHERE id=? AND project_id=?",
                    [*(values[key] for key in keys), record_id, self.project_id],
                )
            conn.commit()
        finally:
            conn.close()
        self.refresh()
        self.records_changed()

    def prepare_values(self, values):
        return values

    def records_changed(self):
        pass

    def add_record(self):
        values = self._dialog(f"افزودن {self.title}")
        if values is not None:
            self._save_values(values)

    def edit_record(self):
        record = self._selected_record()
        if record is None:
            return
        values = self._dialog(f"ویرایش {self.title}", dict(record))
        if values is not None:
            self._save_values(values, record["id"])

    def delete_record(self):
        record = self._selected_record()
        if record is None:
            return
        answer = QMessageBox.question(
            self,
            "تأیید حذف",
            "این ردیف حذف شود؟",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        conn = connect()
        try:
            conn.execute(
                f"DELETE FROM {self.table_name} WHERE id=? AND project_id=?",
                (record["id"], self.project_id),
            )
            conn.commit()
        finally:
            conn.close()
        self.refresh()
        self.records_changed()


ACTIVITY_FIELDS = [
    ("position", "موقعیت", "text", {}),
    ("zone", "جبهه کاری", "text", {}),
    ("title", "شرح فعالیت", "text", {}),
    ("quantity", "حجم کل", "number", {"minimum": 0, "decimals": 2}),
    ("remaining_qty", "حجم باقی‌مانده", "number", {"minimum": 0, "decimals": 2}),
    ("unit", "واحد", "text", {}),
    ("start_date", "شروع", "text", {}),
    ("finish_date", "پایان", "text", {}),
    ("duration_days", "مدت (روز)", "integer", {"minimum": 0, "maximum": 36500}),
    ("daily_target", "برنامه روزانه", "number", {"minimum": 0, "decimals": 2}),
    ("delay_days", "تأخیر (روز)", "integer", {"minimum": 0, "maximum": 36500}),
    (
        "status",
        "وضعیت",
        "choice",
        {"choices": ["NORMAL", "WARNING", "CRITICAL"], "default": "NORMAL"},
    ),
]


class ActivityPage(RecordPage):
    def __init__(self, title, progress_only=False):
        self.progress_only = progress_only
        query = (
            "SELECT * FROM activity WHERE project_id=? "
            "ORDER BY delay_days DESC, row_no"
        )
        columns = [
            ("row_no", "ردیف", None),
            ("position", "موقعیت", None),
            ("zone", "جبهه", None),
            ("title", "فعالیت", None),
            ("quantity", "حجم کل", lambda v, _r: f"{v or 0:,.2f}"),
            ("plan_remaining_qty", "مانده برنامه", lambda v, _r: f"{v or 0:,.2f}"),
            ("remaining_qty", "باقی‌مانده", lambda v, _r: f"{v or 0:,.2f}"),
            ("unit", "واحد", None),
            ("start_date", "شروع", None),
            ("finish_date", "پایان", None),
            ("daily_target", "برنامه روزانه", lambda v, _r: f"{v or 0:,.2f}"),
            ("planned_qty", "برنامه دوره", lambda v, _r: f"{v or 0:,.2f}"),
            ("actual_qty", "عملکرد", lambda v, _r: f"{v or 0:,.2f}"),
            ("variance_qty", "انحراف", lambda v, _r: f"{v or 0:,.2f}"),
            ("achievement_pct", "تحقق", lambda v, _r: f"{v or 0:.1f}%"),
            ("progress", "پیشرفت", lambda v, _r: f"{(v or 0) * 100:.1f}%"),
            ("delay_days", "تأخیر", lambda v, _r: f"{v or 0} روز"),
            ("status", "وضعیت", None),
        ]
        super().__init__(
            title,
            query,
            columns,
            ACTIVITY_FIELDS,
            "activity",
        )
        if progress_only:
            self.add_button.setText("ثبت عملکرد روزانه")
            self.edit_button.setText("ثبت عملکرد")
            self.delete_button.setText("حذف عملکرد")
        else:
            self.add_button.setText("افزودن فعالیت")
        self.refresh()

    def prepare_values(self, values):
        values["source"] = "MANUAL"
        return values

    def refresh(self):
        conn = connect()
        try:
            self.records = conn.execute(
                self.query,
                (self.project_id, *self.query_params),
            ).fetchall()

            self.table.setRowCount(len(self.records))
            for row_index, record in enumerate(self.records):
                control = activity_control(conn, record["id"])
                display_values = [
                    record["row_no"],
                    record["position"],
                    record["zone"],
                    record["title"],
                    f"{record['quantity'] or 0:,.2f}",
                    f"{record['plan_remaining_qty'] or 0:,.2f}",
                    f"{record['remaining_qty'] or 0:,.2f}",
                    record["unit"],
                    record["start_date"],
                    record["finish_date"],
                    f"{record['daily_target'] or 0:,.2f}",
                    f"{control['planned_qty']:,.2f}",
                    f"{control['actual_qty']:,.2f}",
                    f"{control['variance_qty']:,.2f}",
                    f"{control['achievement_pct']:.1f}%",
                    f"{control['physical_progress_pct']:.1f}%",
                    f"{record['delay_days'] or 0} روز",
                    record["status"],
                ]
                for column_index, value in enumerate(display_values):
                    item = QTableWidgetItem(str(value if value is not None else ""))
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.table.setItem(row_index, column_index, item)
        finally:
            conn.close()
        self.table.resizeColumnsToContents()

    def _record_actual(self, record):
        fields = [
            ("actual_date", "تاریخ عملکرد", "text", {}),
            ("quantity", f"مقدار عملکرد ({record['unit'] or ''})", "number", {"minimum": 0, "decimals": 2}),
            ("notes", "توضیحات", "text", {}),
        ]
        dialog = RecordDialog("ثبت عملکرد روزانه", fields, {}, self)
        if dialog.exec() != QDialog.Accepted:
            return

        values = dialog.values()
        if not values["actual_date"] or values["quantity"] <= 0:
            QMessageBox.warning(self, "ورودی ناقص", "تاریخ و مقدار عملکرد الزامی است.")
            return

        conn = connect()
        try:
            conn.execute(
                """
                INSERT INTO activity_daily_actual(activity_id, actual_date, quantity, source, notes)
                VALUES(?, ?, ?, 'MANUAL', ?)
                ON CONFLICT(activity_id, actual_date) DO UPDATE SET
                    quantity=excluded.quantity,
                    notes=excluded.notes
                """,
                (record["id"], values["actual_date"], values["quantity"], values["notes"]),
            )

            update_activity_rollup(conn, record["id"])
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def add_record(self):
        if self.progress_only:
            record = self._selected_record()
            if record:
                self._record_actual(record)
            return
        super().add_record()

    def edit_record(self):
        if self.progress_only:
            record = self._selected_record()
            if record:
                self._record_actual(record)
            return
        super().edit_record()

    def delete_record(self):
        if not self.progress_only:
            super().delete_record()
            return
        record = self._selected_record()
        if not record:
            return
        conn = connect()
        try:
            conn.execute("DELETE FROM activity_daily_actual WHERE activity_id=?", (record["id"],))
            update_activity_rollup(conn, record["id"])
            conn.commit()
        finally:
            conn.close()
        self.refresh()


RESOURCE_FIELDS = [
    (
        "category",
        "دسته",
        "choice",
        {"choices": ["ماشین‌آلات", "نیروی انسانی", "مصالح"]},
    ),
    ("title", "عنوان منبع", "text", {}),
    ("required", "موردنیاز", "number", {"minimum": 0, "decimals": 2}),
    ("available", "موجود", "number", {"minimum": 0, "decimals": 2}),
    ("unit", "واحد", "text", {}),
]


class ResourcePage(RecordPage):
    def __init__(self, title, category):
        self.category = category
        query = """
            SELECT resource.*,
                   COALESCE((
                       SELECT GROUP_CONCAT(
                           resource_period.period || ': ' ||
                           printf('%.2f', resource_period.required_qty),
                           ' | '
                       )
                       FROM resource_period
                       WHERE resource_period.resource_id=resource.id
                         AND resource_period.source='EXCEL'
                   ), '') AS period_summary
            FROM resource WHERE resource.project_id=?
        """
        if category:
            query += " AND resource.category=?"
        query += " ORDER BY resource.category, resource.title"
        super().__init__(
            title,
            query,
            [
                ("category", "دسته", None),
                ("title", "عنوان", None),
                ("required", "موردنیاز", lambda value, _record: f"{value:,.2f}" if value is not None else "نامشخص"),
                ("available", "موجود", lambda value, _record: f"{value:,.2f}" if value is not None else "نامشخص"),
                ("unit", "واحد", None),
                ("supply_type", "نوع تأمین", None),
                ("unit_price", "بهای واحد (ریال)", lambda value, _record: f"{value:,.0f}" if value is not None else "نامشخص"),
                ("period_summary", "نیاز دوره‌ای", None),
            ],
            RESOURCE_FIELDS,
            "resource",
            query_params=(category,) if category else (),
        )

    def _dialog(self, title, initial=None):
        initial = dict(initial or {})
        if self.category:
            initial["category"] = self.category
        return super()._dialog(title, initial)

    def prepare_values(self, values):
        values["source"] = "MANUAL"
        return values


RISK_FIELDS = [
    ("category", "دسته", "text", {}),
    ("title", "شرح ریسک", "text", {}),
    ("consequence", "پیامد", "text", {}),
    ("existing_controls", "کنترل‌های موجود", "text", {}),
    ("probability", "احتمال (۱ تا ۵)", "integer", {"minimum": 1, "maximum": 5}),
    ("impact", "اثر (۱ تا ۵)", "integer", {"minimum": 1, "maximum": 5}),
    ("control", "ضریب کنترل (۱ تا ۵)", "integer", {"minimum": 1, "maximum": 5}),
    ("action", "اقدام اصلاحی", "text", {}),
]


class RiskPage(RecordPage):
    def __init__(self):
        super().__init__(
            "ریسک و اقدامات اصلاحی",
            "SELECT * FROM risk WHERE project_id=? ORDER BY score DESC",
            [
                ("category", "دسته", None),
                ("title", "شرح ریسک", None),
                ("consequence", "پیامد", None),
                ("existing_controls", "کنترل‌های موجود", None),
                ("probability", "احتمال", None),
                ("impact", "اثر", None),
                ("control", "کنترل", None),
                ("score", "امتیاز", None),
                ("action", "اقدام اصلاحی", None),
            ],
            RISK_FIELDS,
            "risk",
        )

    def prepare_values(self, values):
        values["score"] = values["probability"] * values["impact"] * values["control"]
        values["source"] = "MANUAL"
        return values


class FinancePage(QWidget):
    def __init__(self):
        super().__init__()
        self.setLayoutDirection(Qt.RightToLeft)
        layout = QVBoxLayout(self)
        title = QLabel("درآمد و هزینه")
        title.setStyleSheet(f"font-size:21px;font-weight:700;color:{TEXT};")
        layout.addWidget(title)

        self.summary = QLabel()
        self.summary.setStyleSheet(f"font-size:15px;color:{BLUE};font-weight:600;")
        layout.addWidget(self.summary)
        self.table = QTableWidget()
        _configure_table(self.table, ["دوره", "درآمد (ریال)", "هزینه (ریال)", "بالانس (ریال)"])
        layout.addWidget(self.table)
        refresh = QPushButton("بروزرسانی")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        self.refresh()

    def refresh(self):
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT month, revenue, cost FROM monthly_finance WHERE project_id=1 ORDER BY id"
            ).fetchall()
            totals = conn.execute(
                "SELECT revenue, cost, balance FROM kpi WHERE project_id=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row["month"],
                f"{row['revenue']:,.0f}",
                f"{row['cost']:,.0f}",
                f"{row['revenue'] - row['cost']:,.0f}",
            ]
            for col, value in enumerate(values):
                self.table.setItem(row_index, col, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        if totals:
            self.summary.setText(
                f"جمع کل: درآمد {totals['revenue']:,.0f} ریال  |  "
                f"هزینه {totals['cost']:,.0f} ریال  |  "
                f"بالانس {totals['balance']:,.0f} ریال"
            )
        else:
            self.summary.setText("اطلاعات مالی ثبت نشده است.")


class ReportsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setLayoutDirection(Qt.RightToLeft)
        layout = QVBoxLayout(self)
        title = QLabel("گزارش‌ها")
        title.setStyleSheet(f"font-size:21px;font-weight:700;color:{TEXT};")
        layout.addWidget(title)
        explanation = QLabel("خروجی CSV را می‌توانید در Excel باز کنید.")
        explanation.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(explanation)
        for label, filename, headers, query in [
            (
                "خروجی فعالیت‌ها",
                "activities.csv",
                ["ردیف", "فعالیت", "جبهه", "حجم", "واحد", "پیشرفت", "تأخیر", "وضعیت"],
                "SELECT row_no,title,zone,quantity,unit,progress,delay_days,status FROM activity WHERE project_id=? ORDER BY row_no",
            ),
            (
                "خروجی منابع",
                "resources.csv",
                ["دسته", "عنوان", "موردنیاز", "موجود", "واحد"],
                "SELECT category,title,required,available,unit FROM resource WHERE project_id=? ORDER BY category,title",
            ),
            (
                "خروجی ریسک‌ها",
                "risks.csv",
                ["دسته", "شرح ریسک", "پیامد", "کنترل‌های موجود", "احتمال", "اثر", "کنترل", "امتیاز", "اقدام"],
                "SELECT category,title,consequence,existing_controls,probability,impact,control,score,action FROM risk WHERE project_id=? ORDER BY score DESC",
            ),
        ]:
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, file=filename, cols=headers, sql=query: self.export_csv(file, cols, sql)
            )
            layout.addWidget(button)
        layout.addStretch()

    def export_csv(self, filename, headers, query):
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "ذخیره گزارش",
            str(Path.home() / filename),
            "CSV (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        conn = connect()
        try:
            rows = conn.execute(query, (1,)).fetchall()
        finally:
            conn.close()

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as output:
                writer = csv.writer(output)
                writer.writerow(headers)
                for row in rows:
                    writer.writerow([self._safe_csv_value(value) for value in row])
        except OSError as error:
            QMessageBox.critical(self, "خطای ذخیره گزارش", str(error))
            return
        QMessageBox.information(self, "گزارش ذخیره شد", f"گزارش در مسیر زیر ذخیره شد:\n{path}")

    @staticmethod
    def _safe_csv_value(value):
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            return "'" + value
        return value


class SettingsPage(QWidget):
    def __init__(self, imported_callback):
        super().__init__()
        self.imported_callback = imported_callback
        self.setLayoutDirection(Qt.RightToLeft)
        layout = QVBoxLayout(self)
        title = QLabel("تنظیمات و ورود اطلاعات")
        title.setStyleSheet(f"font-size:21px;font-weight:700;color:{TEXT};")
        layout.addWidget(title)
        description = QLabel(
            "داده‌های Excel با فایل جدید بروزرسانی می‌شوند؛ اطلاعاتی که در نرم‌افزار دستی ثبت یا ویرایش شده‌اند حفظ می‌شوند."
        )
        description.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(description)
        note = QLabel(
            "ورود فایل Excel از شیت‌های اصلی پروژه انجام می‌شود: اطلاعات پروژه، خلاصه KPI، برنامه عملیاتی، "
            "پیشرفت فیزیکی، داده‌های منابع و ماشین‌آلات و مصالح. در صورت نبود ساختار شیت، تنها داده‌های "
            "مستند و قابل تشخیص وارد می‌شوند و بقیه داده‌ها در ماژول‌های مربوط قابل ویرایش هستند."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(note)
        button = QPushButton("انتخاب و ورود فایل Excel")
        button.clicked.connect(self.import_excel)
        layout.addWidget(button)
        layout.addStretch()

    def import_excel(self):
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "انتخاب فایل Excel",
            str(Path.home()),
            "Excel (*.xlsx *.xlsm)",
        )
        if not path:
            return
        try:
            from imports.import_excel import import_workbook

            import_workbook(path)
        except (
            OSError,
            ValueError,
            KeyError,
            sqlite3.Error,
            InvalidFileException,
            zipfile.BadZipFile,
        ) as error:
            QMessageBox.critical(self, "خطای ورود اطلاعات", str(error))
            return
        QMessageBox.information(self, "ورود اطلاعات", "اطلاعات فایل Excel وارد شد.")
        self.imported_callback()

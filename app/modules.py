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
    QInputDialog,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db import connect
from .services.control import activity_control, update_activity_rollup
from .services.dates import normalize_date
from .services.forecast import calculate_schedule, forecast_finance
from .services.actual_finance import finance_performance, record_actual
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
            elif kind == "activity":
                widget = QComboBox()
                widget.addItem("بدون تخصیص به فعالیت", None)
                for activity_id, activity_label in options["choices"]:
                    widget.addItem(activity_label, activity_id)
                index = widget.findData(value)
                widget.setCurrentIndex(index if index >= 0 else 0)
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
            elif kind == "activity":
                result[key] = widget.currentData()
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
            ("finish_date", "پایان برنامه", None),
            ("forecast_finish", "پایان پیش‌بینی", None),
            ("daily_target", "برنامه روزانه", lambda v, _r: f"{v or 0:,.2f}"),
            ("planned_qty", "برنامه دوره", lambda v, _r: f"{v or 0:,.2f}"),
            ("actual_qty", "عملکرد دوره", lambda v, _r: f"{v or 0:,.2f}"),
            ("cumulative_actual_qty", "عملکرد تجمعی", lambda v, _r: f"{v or 0:,.2f}"),
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
        self.schedule_notice = QLabel()
        self.schedule_notice.setWordWrap(True)
        self.schedule_notice.setStyleSheet(f"color:{MUTED};")
        self.layout().insertWidget(1, self.schedule_notice)
        self.dependency_button = QPushButton("مدیریت پیش‌نیازها")
        self.dependency_button.clicked.connect(self.manage_dependencies)
        self.layout().itemAt(2).layout().addWidget(self.dependency_button)
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
            schedule = calculate_schedule(conn, self.project_id, persist=True)
            if not schedule["cycle"]:
                conn.commit()
            self.records = conn.execute(
                self.query,
                (self.project_id, *self.query_params),
            ).fetchall()
            if hasattr(self, "schedule_notice"):
                if schedule["cycle"]:
                    self.schedule_notice.setText("خطا: وابستگی فعالیت‌ها چرخه دارد؛ تاریخ‌های پیش‌بینی به‌روزرسانی نشدند. فعالیت‌ها: " + "، ".join(schedule.get("cycle_titles", [])))
                else:
                    delayed = sum(1 for item in schedule["activities"] if item["delay_days"] > 0)
                    self.schedule_notice.setText(f"موتور زمان‌بندی: {schedule['updated']} فعالیت بررسی شد؛ {delayed} فعالیت دارای تأخیر پیش‌بینی‌شده است. محاسبات بر مبنای روز تقویمی هستند.")

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
                    record["forecast_finish"],
                    f"{record['daily_target'] or 0:,.2f}",
                    f"{control['planned_qty']:,.2f}",
                    f"{control['period_actual_qty']:,.2f}",
                    f"{control['cumulative_actual_qty']:,.2f}",
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

    def manage_dependencies(self):
        activity = self._selected_record()
        if not activity:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"پیش‌نیازهای فعالیت: {activity['title'] or activity['id']}")
        dialog.setLayoutDirection(Qt.RightToLeft)
        dialog.resize(760, 520)
        root = QVBoxLayout(dialog)
        form = QFormLayout()
        predecessor = QComboBox()
        relation = QComboBox()
        relation.addItem("پایان به شروع (FS)", "finish_to_start")
        relation.addItem("شروع به شروع (SS)", "start_to_start")
        relation.addItem("پایان به پایان (FF)", "finish_to_finish")
        relation.addItem("شروع به پایان (SF)", "start_to_finish")
        lag = QSpinBox()
        lag.setRange(-365, 365)
        lag.setValue(0)
        form.addRow("فعالیت پیش‌نیاز", predecessor)
        form.addRow("نوع رابطه", relation)
        form.addRow("وقفه تقویمی (روز)", lag)
        root.addLayout(form)

        dependency_table = QTableWidget()
        _configure_table(dependency_table, ["پیش‌نیاز", "نوع رابطه", "وقفه (روز)", "منبع"])
        root.addWidget(dependency_table)

        def load_dependencies():
            conn = connect()
            try:
                activities = conn.execute(
                    "SELECT id,row_no,title,zone FROM activity WHERE project_id=? AND id<>? ORDER BY row_no,id",
                    (self.project_id, activity["id"]),
                ).fetchall()
                current = conn.execute(
                    """
                    SELECT d.id,d.relation_type,d.lag_days,d.source,a.row_no,a.title,a.zone
                    FROM activity_dependency d
                    JOIN activity a ON a.id=d.predecessor_activity_id
                    WHERE d.project_id=? AND d.activity_id=?
                    ORDER BY a.row_no,a.id
                    """,
                    (self.project_id, activity["id"]),
                ).fetchall()
            finally:
                conn.close()
            predecessor.clear()
            for item in activities:
                prefix = f"{item['row_no']} - " if item["row_no"] is not None else ""
                suffix = f" ({item['zone']})" if item["zone"] else ""
                predecessor.addItem(f"{prefix}{item['title'] or 'فعالیت'}{suffix}", item["id"])
            dependency_table.setRowCount(len(current))
            relation_labels = {
                "finish_to_start": "FS",
                "start_to_start": "SS",
                "finish_to_finish": "FF",
                "start_to_finish": "SF",
            }
            for row_index, item in enumerate(current):
                cell = QTableWidgetItem(f"{item['row_no'] or ''} - {item['title'] or ''}")
                cell.setData(Qt.UserRole, item["id"])
                dependency_table.setItem(row_index, 0, cell)
                dependency_table.setItem(row_index, 1, QTableWidgetItem(relation_labels.get(item["relation_type"], item["relation_type"] or "")))
                dependency_table.setItem(row_index, 2, QTableWidgetItem(str(item["lag_days"] or 0)))
                dependency_table.setItem(row_index, 3, QTableWidgetItem(item["source"] or ""))
            dependency_table.resizeColumnsToContents()

        def add_dependency():
            predecessor_id = predecessor.currentData()
            if predecessor_id is None:
                QMessageBox.warning(dialog, "پیش‌نیاز", "ابتدا یک فعالیت پیش‌نیاز انتخاب کنید.")
                return
            conn = connect()
            try:
                conn.execute(
                    """
                    INSERT INTO activity_dependency(
                        project_id,activity_id,predecessor_activity_id,relation_type,lag_days,notes,source
                    ) VALUES(?,?,?,?,?,?, 'MANUAL')
                    ON CONFLICT(project_id,activity_id,predecessor_activity_id) DO UPDATE SET
                        relation_type=excluded.relation_type,
                        lag_days=excluded.lag_days,
                        notes=excluded.notes,
                        source='MANUAL'
                    """,
                    (
                        self.project_id, activity["id"], predecessor_id,
                        relation.currentData(), lag.value(),
                        "وابستگی تعریف‌شده توسط کاربر",
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as error:
                conn.rollback()
                QMessageBox.warning(dialog, "وابستگی نامعتبر", str(error))
                return
            finally:
                conn.close()
            load_dependencies()

        def remove_dependency():
            row_index = dependency_table.currentRow()
            if row_index < 0:
                return
            item = dependency_table.item(row_index, 0)
            dependency_id = item.data(Qt.UserRole) if item else None
            if dependency_id is None:
                return
            confirm = QMessageBox.question(
                dialog, "حذف پیش‌نیاز", "وابستگی انتخاب‌شده حذف شود؟"
            )
            if confirm != QMessageBox.Yes:
                return
            conn = connect()
            try:
                conn.execute(
                    "DELETE FROM activity_dependency WHERE id=? AND project_id=? AND activity_id=?",
                    (dependency_id, self.project_id, activity["id"]),
                )
                conn.commit()
            finally:
                conn.close()
            load_dependencies()

        actions = QHBoxLayout()
        add_button = QPushButton("ثبت / به‌روزرسانی پیش‌نیاز")
        add_button.clicked.connect(add_dependency)
        remove_button = QPushButton("حذف پیش‌نیاز انتخاب‌شده")
        remove_button.clicked.connect(remove_dependency)
        close_button = QPushButton("بستن")
        close_button.clicked.connect(dialog.accept)
        actions.addWidget(add_button)
        actions.addWidget(remove_button)
        actions.addStretch()
        actions.addWidget(close_button)
        root.addLayout(actions)
        load_dependencies()
        dialog.exec()
        self.refresh()

    def _actual_rows(self, record):
        conn = connect()
        try:
            return conn.execute(
                """
                SELECT id, actual_date, quantity, notes
                FROM activity_daily_actual
                WHERE activity_id=? AND source='MANUAL'
                ORDER BY actual_date DESC, id DESC
                """,
                (record["id"],),
            ).fetchall()
        finally:
            conn.close()

    def _choose_actual(self, record, action):
        rows = self._actual_rows(record)
        if not rows:
            QMessageBox.information(self, "عملکرد روزانه", "برای این فعالیت عملکرد دستی ثبت نشده است.")
            return None
        labels = [
            f"{row['actual_date']} | مقدار {float(row['quantity'] or 0):,.2f}"
            + (f" | {row['notes']}" if row["notes"] else "")
            for row in rows
        ]
        selected, accepted = QInputDialog.getItem(
            self, f"{action} عملکرد", "رکورد عملکرد موردنظر را انتخاب کنید:", labels, 0, False
        )
        if not accepted:
            return None
        return rows[labels.index(selected)]

    def _record_actual(self, record, existing_actual=None):
        fields = [
            ("actual_date", "تاریخ عملکرد (مثلاً 1405/07/15)", "text", {}),
            ("quantity", f"مقدار عملکرد ({record['unit'] or ''})", "number", {"minimum": 0, "decimals": 2}),
            ("notes", "توضیحات", "text", {}),
        ]
        initial = dict(existing_actual) if existing_actual is not None else {}
        dialog = RecordDialog(
            "ویرایش عملکرد روزانه" if existing_actual is not None else "ثبت عملکرد روزانه",
            fields,
            initial,
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        values = dialog.values()
        if not values["actual_date"] or values["quantity"] <= 0:
            QMessageBox.warning(self, "ورودی ناقص", "تاریخ و مقدار عملکرد الزامی است.")
            return
        try:
            actual_date = normalize_date(values["actual_date"])
        except ValueError as error:
            QMessageBox.warning(self, "تاریخ نامعتبر", str(error))
            return

        conn = connect()
        try:
            if existing_actual is not None:
                conn.execute(
                    """
                    UPDATE activity_daily_actual
                    SET actual_date=?, quantity=?, notes=?
                    WHERE id=? AND activity_id=? AND source='MANUAL'
                    """,
                    (
                        actual_date,
                        values["quantity"],
                        values["notes"],
                        existing_actual["id"],
                        record["id"],
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO activity_daily_actual(activity_id, actual_date, quantity, source, notes)
                    VALUES(?, ?, ?, 'MANUAL', ?)
                    ON CONFLICT(activity_id, actual_date) DO UPDATE SET
                        quantity=excluded.quantity,
                        notes=excluded.notes
                    WHERE activity_daily_actual.source='MANUAL'
                    """,
                    (record["id"], actual_date, values["quantity"], values["notes"]),
                )
                if cursor.rowcount == 0:
                    conn.rollback()
                    QMessageBox.warning(
                        self,
                        "تاریخ متعلق به Excel",
                        "برای این تاریخ رکورد واردشده از Excel وجود دارد؛ برای جلوگیری از بازنویسی داده منبع، عملکرد دستی ثبت نشد.",
                    )
                    return
            update_activity_rollup(conn, record["id"])
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            QMessageBox.warning(
                self,
                "تاریخ تکراری",
                "برای این فعالیت در این تاریخ عملکرد ثبت شده است. تاریخ دیگری انتخاب کنید یا همان رکورد را ویرایش کنید.",
            )
            return
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
                actual = self._choose_actual(record, "ویرایش")
                if actual:
                    self._record_actual(record, actual)
            return
        super().edit_record()

    def delete_record(self):
        if not self.progress_only:
            super().delete_record()
            return
        record = self._selected_record()
        if not record:
            return
        actual = self._choose_actual(record, "حذف")
        if not actual:
            return
        confirm = QMessageBox.question(
            self,
            "تأیید حذف",
            f"عملکرد {actual['actual_date']} با مقدار {float(actual['quantity'] or 0):,.2f} حذف شود؟",
        )
        if confirm != QMessageBox.Yes:
            return
        conn = connect()
        try:
            conn.execute(
                "DELETE FROM activity_daily_actual WHERE id=? AND activity_id=? AND source='MANUAL'",
                (actual["id"], record["id"]),
            )
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
                           resource_period.period || ': نیاز ' ||
                           printf('%.2f', resource_period.required_qty) ||
                           ' | موجودی اول دوره ' ||
                           CASE WHEN resource_period.opening_stock IS NULL
                                THEN 'نامشخص'
                                ELSE printf('%.2f', resource_period.opening_stock) END ||
                           ' | خرید ' ||
                           CASE WHEN resource_period.purchase_qty IS NULL
                                THEN 'نامشخص'
                                ELSE printf('%.2f', resource_period.purchase_qty) END ||
                           ' | موجود/تأمین ' ||
                           CASE WHEN resource_period.available_qty IS NULL
                                THEN 'نامشخص'
                                ELSE printf('%.2f', resource_period.available_qty) END ||
                           ' | بهای واحد ' ||
                           CASE WHEN resource_period.unit_price IS NULL
                                THEN 'نامشخص'
                                ELSE printf('%.0f', resource_period.unit_price) END,
                           ' || '
                       )
                       FROM resource_period
                       WHERE resource_period.resource_id=resource.id
                         AND resource_period.source='EXCEL'
                   ), '') AS period_summary,
                    COALESCE((
                        SELECT GROUP_CONCAT(
                            resource_period.period || ': کمبود ' ||
                            CASE
                                WHEN resource_period.available_qty IS NOT NULL THEN
                                    printf('%.2f', MAX(0, resource_period.required_qty - resource_period.available_qty))
                                WHEN resource_period.opening_stock IS NOT NULL
                                     AND resource_period.purchase_qty IS NOT NULL THEN
                                    printf('%.2f', MAX(0, resource_period.required_qty - resource_period.opening_stock - resource_period.purchase_qty))
                                WHEN resource.available IS NOT NULL THEN
                                    printf('%.2f', MAX(0, resource_period.required_qty - resource.available))
                                ELSE 'نامشخص'
                            END,
                            ' || '
                        )
                        FROM resource_period
                        WHERE resource_period.resource_id=resource.id
                          AND resource_period.source IN ('EXCEL','MANUAL')
                    ), '') AS shortage_summary
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
                ("shortage_summary", "کمبود دوره‌ای", None),
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
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"font-size:15px;color:{BLUE};font-weight:600;")
        layout.addWidget(self.summary)
        self.forecast_summary = QLabel()
        self.forecast_summary.setWordWrap(True)
        self.forecast_summary.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(self.forecast_summary)

        self.actual_summary = QLabel()
        self.actual_summary.setWordWrap(True)
        self.actual_summary.setStyleSheet(f"font-size:14px;color:{TEXT};")
        layout.addWidget(self.actual_summary)
        actual_title = QLabel("دفتر درآمد و هزینه واقعی")
        actual_title.setStyleSheet(f"font-size:17px;font-weight:600;color:{TEXT};")
        layout.addWidget(actual_title)
        actual_note = QLabel(
            "این دفتر از مبالغ برنامه‌ای جداست. ثبت هزینه یا درآمد واقعی نیازمند ورود دستی اطلاعات معتبر است؛ "
            "برآورد EAC فقط برای فعالیت‌هایی محاسبه می‌شود که بودجه هزینه، پیشرفت و هزینه واقعی تخصیص‌یافته داشته باشند."
        )
        actual_note.setWordWrap(True)
        actual_note.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(actual_note)
        self.actual_table = QTableWidget()
        _configure_table(
            self.actual_table,
            ["تاریخ", "دوره", "نوع", "شرح", "مبلغ (ریال)", "فعالیت مرتبط", "توضیحات"],
        )
        layout.addWidget(self.actual_table)
        actual_actions = QHBoxLayout()
        self.add_actual_button = QPushButton("ثبت درآمد/هزینه واقعی")
        self.edit_actual_button = QPushButton("ویرایش رکورد واقعی")
        self.delete_actual_button = QPushButton("حذف رکورد واقعی")
        for button in (self.add_actual_button, self.edit_actual_button, self.delete_actual_button):
            actual_actions.addWidget(button)
        actual_actions.addStretch()
        layout.addLayout(actual_actions)
        self.add_actual_button.clicked.connect(self.add_actual_entry)
        self.edit_actual_button.clicked.connect(self.edit_actual_entry)
        self.delete_actual_button.clicked.connect(self.delete_actual_entry)

        self.table = QTableWidget()
        _configure_table(self.table, ["دوره", "درآمد (ریال)", "هزینه (ریال)", "بالانس (ریال)"])
        layout.addWidget(self.table)
        refresh = QPushButton("بروزرسانی")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)

        allocation_title = QLabel("تخصیص مبالغ مالی به فعالیت‌ها")
        allocation_title.setStyleSheet(f"font-size:17px;font-weight:600;color:{TEXT};")
        layout.addWidget(allocation_title)
        allocation_note = QLabel(
            "ردیف‌های Excel به‌صورت خودکار به فعالیت‌ها متصل نمی‌شوند؛ فقط در صورت انتخاب دستی محاسبه پیشرفت‌محور انجام می‌شود."
        )
        allocation_note.setWordWrap(True)
        allocation_note.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(allocation_note)
        self.allocations_table = QTableWidget()
        _configure_table(
            self.allocations_table,
            ["نوع", "دوره", "شرح", "مبلغ (ریال)", "فعالیت مرتبط"],
        )
        layout.addWidget(self.allocations_table)
        save_allocations = QPushButton("ذخیره تخصیص‌های مالی")
        save_allocations.clicked.connect(self.save_allocations)
        layout.addWidget(save_allocations)
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
            forecast = forecast_finance(conn, 1)
            performance = finance_performance(conn, 1)
            actual_entries = conn.execute(
                """
                SELECT e.*, a.row_no, a.title AS activity_title
                FROM actual_finance_entry e
                LEFT JOIN activity a ON a.id=e.activity_id
                WHERE e.project_id=1
                ORDER BY e.entry_date DESC, e.id DESC
                """
            ).fetchall()
            activities = conn.execute(
                "SELECT id,row_no,title,zone FROM activity WHERE project_id=1 ORDER BY row_no,id"
            ).fetchall()
            allocation_rows = []
            for table_name, label in (("revenue_entry", "درآمد"), ("cost_entry", "هزینه")):
                for entry in conn.execute(
                    f"SELECT id,activity_id,period,category,amount FROM {table_name} WHERE project_id=1 ORDER BY period,id"
                ).fetchall():
                    allocation_rows.append((table_name, label, entry))
        finally:
            conn.close()

        self.actual_table.setRowCount(len(actual_entries))
        for row_index, entry in enumerate(actual_entries):
            kind = "درآمد واقعی" if entry["entry_type"] == "REVENUE" else "هزینه واقعی"
            activity_label = (
                f"{entry['row_no']} - {entry['activity_title'] or 'فعالیت'}"
                if entry["activity_id"] is not None else "بدون تخصیص"
            )
            values = [
                entry["entry_date"], entry["period"] or "", kind, entry["category"],
                f"{float(entry['amount'] or 0):,.0f}", activity_label, entry["notes"] or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, entry["id"])
                self.actual_table.setItem(row_index, column, item)
        self.actual_table.resizeColumnsToContents()
        eac_text = (
            f"EAC هزینه برای {performance['assessed_activity_count']} فعالیتِ قابل ارزیابی: "
            f"{performance['eac_assessed']:,.0f} ریال"
            if performance["eac_assessed"] is not None
            else "EAC هزینه هنوز قابل محاسبه نیست؛ برای فعالیت‌های دارای بودجه، هزینه واقعی و پیشرفت تخصیص‌یافته ثبت کنید."
        )
        cpi_text = f"{performance['cpi']:.3f}" if performance["cpi"] is not None else "نامشخص"
        self.actual_summary.setText(
            f"واقعی ثبت‌شده: درآمد {performance['actual_revenue']:,.0f} ریال | "
            f"هزینه {performance['actual_cost']:,.0f} ریال | "
            f"خالص {performance['actual_net']:,.0f} ریال. "
            f"ارزش کسب‌شده هزینه (EV): {performance['earned_value_cost']:,.0f} ریال؛ "
            f"CPI: {cpi_text}. {eac_text}. "
            f"بودجه هزینه ارزیابی‌نشده: {performance['unassessed_budget']:,.0f} ریال."
        )

        self.allocations_table.setRowCount(len(allocation_rows))
        for row_index, (table_name, label, entry) in enumerate(allocation_rows):
            kind_item = QTableWidgetItem(label)
            kind_item.setData(Qt.UserRole, (table_name, entry["id"]))
            self.allocations_table.setItem(row_index, 0, kind_item)
            self.allocations_table.setItem(row_index, 1, QTableWidgetItem(str(entry["period"] or "")))
            self.allocations_table.setItem(row_index, 2, QTableWidgetItem(str(entry["category"] or "")))
            self.allocations_table.setItem(row_index, 3, QTableWidgetItem(f"{float(entry['amount'] or 0):,.0f}"))
            selector = QComboBox()
            selector.addItem("بدون تخصیص", None)
            for activity in activities:
                prefix = f"{activity['row_no']} - " if activity["row_no"] is not None else ""
                suffix = f" ({activity['zone']})" if activity["zone"] else ""
                selector.addItem(f"{prefix}{activity['title'] or 'فعالیت'}{suffix}", activity["id"])
            index = selector.findData(entry["activity_id"])
            selector.setCurrentIndex(index if index >= 0 else 0)
            self.allocations_table.setCellWidget(row_index, 4, selector)
        self.allocations_table.resizeColumnsToContents()

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
        revenue = forecast["revenue"]
        cost = forecast["cost"]
        self.forecast_summary.setText(
            "ارزش پیشرفت وزنی از مبالغ تخصیص‌یافته: "
            f"درآمد {revenue['earned_to_date']:,.0f} از مبلغ تخصیص‌یافته {revenue['linked_budget']:,.0f} ریال؛ "
            f"هزینه {cost['earned_to_date']:,.0f} از {cost['linked_budget']:,.0f} ریال. "
            f"مبالغ بدون تخصیص: درآمد {revenue['unallocated_amount']:,.0f} و هزینه {cost['unallocated_amount']:,.0f} ریال. "
            "این محاسبه بودجه/مبلغ برنامه‌ای را بر اساس درصد پیشرفت وزن می‌دهد و جایگزین ثبت هزینه و درآمد واقعی نیست."
        )

    def save_allocations(self):
        conn = connect()
        try:
            for row_index in range(self.allocations_table.rowCount()):
                kind_item = self.allocations_table.item(row_index, 0)
                if kind_item is None:
                    continue
                target = kind_item.data(Qt.UserRole)
                if not target:
                    continue
                table_name, entry_id = target
                if table_name not in ("revenue_entry", "cost_entry"):
                    continue
                selector = self.allocations_table.cellWidget(row_index, 4)
                activity_id = selector.currentData() if selector is not None else None
                conn.execute(
                    f"UPDATE {table_name} SET activity_id=? WHERE id=? AND project_id=1",
                    (activity_id, entry_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        QMessageBox.information(self, "تخصیص مالی", "تخصیص‌های مالی ذخیره شد.")
        self.refresh()


    def _actual_entry_dialog(self, existing=None):
        conn = connect()
        try:
            activities = conn.execute(
                "SELECT id,row_no,title,zone FROM activity WHERE project_id=1 ORDER BY row_no,id"
            ).fetchall()
        finally:
            conn.close()
        choices = []
        for activity in activities:
            prefix = f"{activity['row_no']} - " if activity["row_no"] is not None else f"ID {activity['id']} - "
            suffix = f" ({activity['zone']})" if activity["zone"] else ""
            choices.append((activity["id"], f"{prefix}{activity['title'] or 'فعالیت'}{suffix}"))
        initial = dict(existing or {})
        initial["entry_type"] = (
            "درآمد واقعی" if initial.get("entry_type") == "REVENUE"
            else "هزینه واقعی" if initial.get("entry_type") == "COST"
            else "هزینه واقعی"
        )
        fields = [
            ("entry_date", "تاریخ (شمسی یا میلادی)", "text", {}),
            ("period", "دوره گزارش", "text", {}),
            ("entry_type", "نوع رکورد", "choice", {"choices": ["درآمد واقعی", "هزینه واقعی"]}),
            ("category", "شرح / دسته", "text", {}),
            ("amount", "مبلغ (ریال)", "number", {"minimum": 0, "decimals": 0}),
            ("activity_id", "فعالیت مرتبط", "activity", {"choices": choices}),
            ("notes", "توضیحات", "text", {}),
        ]
        dialog = RecordDialog("ثبت/ویرایش درآمد و هزینه واقعی", fields, initial, self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["entry_date"] or not values["category"]:
            QMessageBox.warning(self, "ورودی ناقص", "تاریخ و شرح الزامی است.")
            return
        try:
            normalized_date = normalize_date(values["entry_date"])
            entry_type = "REVENUE" if values["entry_type"] == "درآمد واقعی" else "COST"
            conn = connect()
            try:
                record_actual(
                    conn,
                    project_id=1,
                    entry_date=normalized_date,
                    period=values["period"],
                    entry_type=entry_type,
                    category=values["category"],
                    amount=values["amount"],
                    activity_id=values["activity_id"],
                    notes=values["notes"],
                    entry_id=existing["id"] if existing is not None else None,
                )
                conn.commit()
            finally:
                conn.close()
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "ثبت ناموفق", str(error))
            return
        self.refresh()

    def add_actual_entry(self):
        self._actual_entry_dialog()

    def edit_actual_entry(self):
        row = self.actual_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "انتخاب رکورد", "ابتدا یک رکورد از دفتر واقعی انتخاب کنید.")
            return
        item = self.actual_table.item(row, 0)
        entry_id = item.data(Qt.UserRole) if item is not None else None
        conn = connect()
        try:
            existing = conn.execute(
                "SELECT * FROM actual_finance_entry WHERE id=? AND project_id=1 AND source='MANUAL'",
                (entry_id,),
            ).fetchone()
        finally:
            conn.close()
        if existing is None:
            QMessageBox.warning(self, "رکورد نامعتبر", "رکورد انتخاب‌شده پیدا نشد یا قابل ویرایش نیست.")
            return
        self._actual_entry_dialog(dict(existing))

    def delete_actual_entry(self):
        row = self.actual_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "انتخاب رکورد", "ابتدا یک رکورد از دفتر واقعی انتخاب کنید.")
            return
        item = self.actual_table.item(row, 0)
        entry_id = item.data(Qt.UserRole) if item is not None else None
        answer = QMessageBox.question(
            self, "تأیید حذف", "رکورد مالی واقعی انتخاب‌شده حذف شود؟"
        )
        if answer != QMessageBox.Yes:
            return
        conn = connect()
        try:
            conn.execute(
                "DELETE FROM actual_finance_entry WHERE id=? AND project_id=1 AND source='MANUAL'",
                (entry_id,),
            )
            conn.commit()
        finally:
            conn.close()
        self.refresh()


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

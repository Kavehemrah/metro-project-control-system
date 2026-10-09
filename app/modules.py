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
from .services.dates import format_date, normalize_date
from .services.forecast import calculate_schedule, forecast_finance
from .services.actual_finance import finance_performance, record_actual
from .services.activity_costs import (
    activity_cost_items,
    activity_resource_forecast,
)
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
            elif kind == "date":
                widget = QLineEdit(format_date(value) if value else "")
                widget.setPlaceholderText("مثلاً 1405/07/17")
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
            elif kind == "resource":
                widget = QComboBox()
                widget.addItem("انتخاب منبع", None)
                for resource_id, resource_label in options["choices"]:
                    widget.addItem(resource_label, resource_id)
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
            elif kind == "date":
                result[key] = widget.text().strip() or None
            elif kind == "choice":
                result[key] = widget.currentText()
            elif kind in ("activity", "resource"):
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
        try:
            values = self.prepare_values(values)
        except ValueError as error:
            QMessageBox.warning(self, "ورودی نامعتبر", str(error))
            return
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
    ("start_date", "شروع", "date", {}),
    ("finish_date", "پایان", "date", {}),
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

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("فیلتر موقعیت:"))
        self.position_filter = QComboBox()
        self.position_filter.addItem("همه موقعیت‌ها", None)
        filter_layout.addWidget(self.position_filter)
        filter_layout.addWidget(QLabel("فیلتر فعالیت:"))
        self.activity_filter = QComboBox()
        self.activity_filter.addItem("همه فعالیت‌ها", None)
        self.activity_filter.setMinimumWidth(250)
        filter_layout.addWidget(self.activity_filter)
        self.search_filter = QLineEdit()
        self.search_filter.setPlaceholderText("جستجوی متن در موقعیت، جبهه یا شرح فعالیت")
        filter_layout.addWidget(self.search_filter, 1)
        self.layout().insertLayout(3, filter_layout)
        self.position_filter.currentIndexChanged.connect(self.refresh)
        self.activity_filter.currentIndexChanged.connect(self.refresh)
        self.search_filter.textChanged.connect(self.refresh)
        self._updating_filter_choices = False
        self.refresh()

    def _populate_activity_filters(self, records):
        if not hasattr(self, "position_filter") or self._updating_filter_choices:
            return
        self._updating_filter_choices = True
        try:
            old_position = self.position_filter.currentData()
            old_activity = self.activity_filter.currentData()
            self.position_filter.blockSignals(True)
            self.activity_filter.blockSignals(True)
            self.position_filter.clear()
            self.position_filter.addItem("همه موقعیت‌ها", None)
            positions = sorted({str(row["position"]).strip() for row in records if row["position"]})
            for position in positions:
                self.position_filter.addItem(position, position)
            position_index = self.position_filter.findData(old_position)
            self.position_filter.setCurrentIndex(position_index if position_index >= 0 else 0)
            selected_position = self.position_filter.currentData()

            candidates = [
                row for row in records
                if selected_position is None or (row["position"] or "").strip() == selected_position
            ]
            self.activity_filter.clear()
            self.activity_filter.addItem("همه فعالیت‌ها", None)
            for row in candidates:
                prefix = f"{row['row_no']} - " if row["row_no"] is not None else f"ID {row['id']} - "
                location = " / ".join(part for part in [row["position"], row["zone"]] if part)
                suffix = f" ({location})" if location else ""
                self.activity_filter.addItem(f"{prefix}{row['title'] or 'فعالیت'}{suffix}", row["id"])
            activity_index = self.activity_filter.findData(old_activity)
            self.activity_filter.setCurrentIndex(activity_index if activity_index >= 0 else 0)
            self.position_filter.blockSignals(False)
            self.activity_filter.blockSignals(False)
        finally:
            self._updating_filter_choices = False

    def prepare_values(self, values):
        for key in ("start_date", "finish_date"):
            values[key] = normalize_date(values[key]) if values.get(key) else None
        values["source"] = "MANUAL"
        return values

    def refresh(self):
        conn = connect()
        try:
            schedule = calculate_schedule(conn, self.project_id, persist=True)
            if not schedule["cycle"]:
                conn.commit()
            base_records = conn.execute(
                self.query,
                (self.project_id, *self.query_params),
            ).fetchall()
            if hasattr(self, "position_filter"):
                self._populate_activity_filters(base_records)
                selected_position = self.position_filter.currentData()
                selected_activity = self.activity_filter.currentData()
                search_text = self.search_filter.text().strip().casefold()
                self.records = [
                    row for row in base_records
                    if (selected_position is None or (row["position"] or "").strip() == selected_position)
                    and (selected_activity is None or row["id"] == selected_activity)
                    and (
                        not search_text
                        or search_text in " ".join(
                            str(row[key] or "")
                            for key in ("position", "zone", "title")
                        ).casefold()
                    )
                ]
            else:
                self.records = base_records
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
                    format_date(record["start_date"]),
                    format_date(record["finish_date"]),
                    format_date(record["forecast_finish"]),
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
            f"{format_date(row['actual_date'])} | مقدار {float(row['quantity'] or 0):,.2f}"
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
            ("actual_date", "تاریخ عملکرد", "date", {}),
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
            f"عملکرد {format_date(actual['actual_date'])} با مقدار {float(actual['quantity'] or 0):,.2f} حذف شود؟",
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



class ActivityCostResourcePage(QWidget):
    """Activity-unit cost build-up and resource capacity planning."""

    COST_CATEGORIES = ["مصالح", "ماشین‌آلات", "نیروی انسانی", "پیمانکار", "حمل", "سایر"]

    def __init__(self, project_id=1):
        super().__init__()
        self.project_id = project_id
        self.setLayoutDirection(Qt.RightToLeft)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(10)

        title = QLabel("برآورد هزینه و منابع فعالیت")
        title.setStyleSheet(f"font-size:21px;font-weight:700;color:{TEXT};")
        root.addWidget(title)
        note = QLabel(
            "هزینه پایه را یک‌بار به ازای واحد خروجی فعالیت تعریف کنید؛ مبلغ کل = ضریب مصرف × بهای واحد × حجم فعالیت. "
            "اگر واحد فعالیت متر است اما بهای آیتم به‌ازای هر اسلب است، واحد آیتم را «اسلب» و ضریب را تعداد اسلب به‌ازای هر متر وارد کنید. "
            "نیروی انسانی و ماشین‌آلات بر مبنای اوج تولید روزانه کنترل می‌شوند؛ مصرف مصالح در دوره جمع می‌شود. "
            "هزینه پیش‌بینی‌نشده جداگانه در دفتر مالی واقعی ثبت می‌شود."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};")
        root.addWidget(note)

        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("فعالیت:"))
        self.activity_selector = QComboBox()
        self.activity_selector.setMinimumWidth(430)
        selector_row.addWidget(self.activity_selector, 1)
        self.activity_info = QLabel()
        self.activity_info.setWordWrap(True)
        selector_row.addWidget(self.activity_info, 1)
        root.addLayout(selector_row)

        cost_title = QLabel("ریز هزینه پایه به ازای واحد فعالیت")
        cost_title.setStyleSheet(f"font-size:16px;font-weight:600;color:{TEXT};")
        root.addWidget(cost_title)
        self.cost_table = QTableWidget()
        _configure_table(
            self.cost_table,
            ["دسته", "شرح هزینه", "واحد هزینه", "ضریب به ازای یک واحد فعالیت", "بهای واحد (ریال)", "هزینه به ازای واحد فعالیت", "برآورد کل فعالیت (ریال)"],
        )
        self.cost_table.setMinimumHeight(170)
        root.addWidget(self.cost_table, 2)
        cost_actions = QHBoxLayout()
        self.add_cost_button = QPushButton("افزودن ردیف هزینه")
        self.edit_cost_button = QPushButton("ویرایش ردیف هزینه")
        self.delete_cost_button = QPushButton("حذف ردیف هزینه")
        for button in (self.add_cost_button, self.edit_cost_button, self.delete_cost_button):
            cost_actions.addWidget(button)
        cost_actions.addStretch()
        root.addLayout(cost_actions)
        self.add_cost_button.clicked.connect(self.add_cost_item)
        self.edit_cost_button.clicked.connect(self.edit_cost_item)
        self.delete_cost_button.clicked.connect(self.delete_cost_item)

        resource_title = QLabel("نیاز منابع بر اساس مقدار برنامه فعالیت")
        resource_title.setStyleSheet(f"font-size:16px;font-weight:600;color:{TEXT};")
        root.addWidget(resource_title)
        self.resource_table = QTableWidget()
        _configure_table(
            self.resource_table,
            ["دوره", "دسته", "منبع", "واحد منبع", "ضریب به ازای واحد فعالیت", "مقدار خروجی مبنا", "نیاز منبع", "موجود", "کمبود", "هزینه تخمینی کمبود (ریال)"],
        )
        self.resource_table.setMinimumHeight(170)
        root.addWidget(self.resource_table, 2)
        resource_actions = QHBoxLayout()
        self.add_requirement_button = QPushButton("افزودن نیاز منبع")
        self.edit_requirement_button = QPushButton("ویرایش نیاز منبع")
        self.delete_requirement_button = QPushButton("حذف نیاز منبع")
        for button in (self.add_requirement_button, self.edit_requirement_button, self.delete_requirement_button):
            resource_actions.addWidget(button)
        resource_actions.addStretch()
        root.addLayout(resource_actions)
        self.add_requirement_button.clicked.connect(self.add_requirement)
        self.edit_requirement_button.clicked.connect(self.edit_requirement)
        self.delete_requirement_button.clicked.connect(self.delete_requirement)

        self.activity_selector.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def _selected_activity(self):
        activity_id = self.activity_selector.currentData()
        if activity_id is None:
            return None
        conn = connect()
        try:
            row = conn.execute(
                "SELECT * FROM activity WHERE id=? AND project_id=?",
                (activity_id, self.project_id),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _ensure_activity(self):
        activity = self._selected_activity()
        if activity is None:
            QMessageBox.information(self, "انتخاب فعالیت", "ابتدا یک فعالیت انتخاب کنید.")
        return activity

    def refresh(self):
        conn = connect()
        try:
            activities = conn.execute(
                """
                SELECT id,row_no,position,zone,title,unit,quantity,daily_target
                FROM activity WHERE project_id=?
                ORDER BY row_no,id
                """,
                (self.project_id,),
            ).fetchall()
            old_id = self.activity_selector.currentData()
            self.activity_selector.blockSignals(True)
            self.activity_selector.clear()
            for activity in activities:
                number = f"{activity['row_no']} - " if activity["row_no"] is not None else f"ID {activity['id']} - "
                location = " / ".join(part for part in [activity["position"], activity["zone"]] if part)
                suffix = f" ({location})" if location else ""
                self.activity_selector.addItem(
                    f"{number}{activity['title'] or 'فعالیت'}{suffix}",
                    activity["id"],
                )
            index = self.activity_selector.findData(old_id)
            self.activity_selector.setCurrentIndex(index if index >= 0 else (0 if activities else -1))
            activity_id = self.activity_selector.currentData()
            activity = next((dict(row) for row in activities if row["id"] == activity_id), None)
            if activity:
                self.activity_info.setText(
                    f"حجم فعالیت: {float(activity['quantity'] or 0):,.2f} {activity['unit'] or ''} | "
                    f"راندمان روزانه مبنا: {float(activity['daily_target'] or 0):,.2f}"
                )
            else:
                self.activity_info.setText("فعالیتی ثبت نشده است.")

            if activity is None:
                cost_rows = []
                resource_rows = []
            else:
                cost_rows = activity_cost_items(conn, self.project_id)
                cost_rows = [row for row in cost_rows if row["activity_id"] == activity_id]
                resource_rows = activity_resource_forecast(conn, self.project_id)
                resource_rows = [row for row in resource_rows if row["activity_id"] == activity_id]
        finally:
            self.activity_selector.blockSignals(False)
            conn.close()

        activity_qty = float(activity["quantity"] or 0) if activity else 0.0
        self.cost_table.setRowCount(len(cost_rows))
        for row_index, item in enumerate(cost_rows):
            coeff = float(item["quantity_per_activity_unit"] or 0)
            price = float(item["unit_price"] or 0)
            unit_cost = coeff * price
            values = [
                item["category"], item["item_name"], item["unit"] or "",
                f"{coeff:,.4f}", f"{price:,.0f}", f"{unit_cost:,.0f}",
                f"{float(item['estimated_total'] or 0):,.0f}",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 0:
                    cell.setData(Qt.UserRole, item["id"])
                self.cost_table.setItem(row_index, column, cell)
        self.cost_table.resizeColumnsToContents()

        self.resource_table.setRowCount(len(resource_rows))
        for row_index, item in enumerate(resource_rows):
            available = item["available_qty"]
            shortage = item["shortage_qty"]
            shortage_cost = item["shortage_cost"]
            values = [
                item["period"],
                item["category"],
                item["resource_title"],
                item["unit"] or item["resource_unit"] or "",
                f"{float(item['quantity_per_activity_unit'] or 0):,.4f}",
                f"{float(item['planned_activity_qty'] or 0):,.2f}",
                f"{float(item['required_qty'] or 0):,.2f}",
                f"{available:,.2f}" if available is not None else "نامشخص",
                f"{shortage:,.2f}" if shortage is not None else "نامشخص",
                f"{shortage_cost:,.0f}" if shortage_cost is not None else "نامشخص",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 0:
                    cell.setData(Qt.UserRole, item["id"])
                self.resource_table.setItem(row_index, column, cell)
        self.resource_table.resizeColumnsToContents()

    def _cost_dialog(self, existing=None):
        fields = [
            ("category", "دسته هزینه", "choice", {"choices": self.COST_CATEGORIES}),
            ("item_name", "شرح هزینه", "text", {}),
            ("unit", "واحد هزینه", "text", {}),
            ("quantity_per_activity_unit", "مقدار به ازای یک واحد فعالیت", "number", {"minimum": 0, "decimals": 4}),
            ("unit_price", "بهای واحد (ریال)", "number", {"minimum": 0, "decimals": 0}),
            ("notes", "توضیحات", "text", {}),
        ]
        dialog = RecordDialog(
            "ویرایش ریز هزینه" if existing else "تعریف هزینه به ازای واحد فعالیت",
            fields,
            dict(existing or {}),
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return None
        values = dialog.values()
        if not values["item_name"]:
            QMessageBox.warning(self, "ورودی ناقص", "شرح هزینه الزامی است.")
            return None
        return values

    def _selected_cost_item(self):
        row = self.cost_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "انتخاب ردیف", "یک ردیف از جدول هزینه پایه انتخاب کنید.")
            return None
        cell = self.cost_table.item(row, 0)
        item_id = cell.data(Qt.UserRole) if cell else None
        conn = connect()
        try:
            item = conn.execute(
                "SELECT * FROM activity_cost_item WHERE id=? AND project_id=?",
                (item_id, self.project_id),
            ).fetchone()
            return dict(item) if item else None
        finally:
            conn.close()

    def add_cost_item(self):
        activity = self._ensure_activity()
        if not activity:
            return
        values = self._cost_dialog()
        if values is None:
            return
        conn = connect()
        try:
            conn.execute(
                """
                INSERT INTO activity_cost_item(
                    project_id,activity_id,category,item_name,unit,
                    quantity_per_activity_unit,unit_price,notes,source
                ) VALUES(?,?,?,?,?,?,?,?, 'MANUAL')
                """,
                (
                    self.project_id, activity["id"], values["category"], values["item_name"],
                    values["unit"], values["quantity_per_activity_unit"],
                    values["unit_price"], values["notes"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def edit_cost_item(self):
        current = self._selected_cost_item()
        if not current:
            return
        values = self._cost_dialog(current)
        if values is None:
            return
        activity = self._ensure_activity()
        if not activity:
            return
        conn = connect()
        try:
            conn.execute(
                """
                UPDATE activity_cost_item SET activity_id=?,category=?,item_name=?,unit=?,
                    quantity_per_activity_unit=?,unit_price=?,notes=?,source='MANUAL'
                WHERE id=? AND project_id=?
                """,
                (
                    activity["id"], values["category"], values["item_name"], values["unit"],
                    values["quantity_per_activity_unit"], values["unit_price"], values["notes"],
                    current["id"], self.project_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def delete_cost_item(self):
        current = self._selected_cost_item()
        if not current:
            return
        answer = QMessageBox.question(self, "تأیید حذف", f"هزینه «{current['item_name']}» حذف شود؟")
        if answer != QMessageBox.Yes:
            return
        conn = connect()
        try:
            conn.execute(
                "DELETE FROM activity_cost_item WHERE id=? AND project_id=?",
                (current["id"], self.project_id),
            )
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def _resource_dialog(self, existing=None):
        activity = self._ensure_activity()
        if not activity:
            return None
        conn = connect()
        try:
            resource_records = conn.execute(
                """
                SELECT id,category,title,unit FROM resource
                WHERE project_id=? ORDER BY category,title
                """,
                (self.project_id,),
            ).fetchall()
        finally:
            conn.close()
        if not resource_records:
            QMessageBox.information(
                self, "تعریف منبع", "ابتدا ماشین‌آلات، نیروی انسانی و مصالح را در صفحه منابع تعریف یا از Excel وارد کنید."
            )
            return None
        choices = []
        for item in resource_records:
            label = f"{item['category'] or 'منبع'} — {item['title'] or 'بدون عنوان'}"
            if item["unit"]:
                label += f" ({item['unit']})"
            choices.append((item["id"], label))
        fields = [
            ("resource_id", "منبع", "resource", {"choices": choices}),
            ("quantity_per_activity_unit", "نیاز به ازای یک واحد فعالیت", "number", {"minimum": 0, "decimals": 4}),
            ("notes", "توضیحات", "text", {}),
        ]
        dialog = RecordDialog(
            "ویرایش ضریب مصرف منبع" if existing else "تعریف نیاز منبع برای واحد فعالیت",
            fields,
            dict(existing or {}),
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return None
        values = dialog.values()
        if values["resource_id"] is None:
            QMessageBox.warning(self, "انتخاب منبع", "یک منبع معتبر انتخاب کنید.")
            return None
        source = next((r for r in resource_records if r["id"] == values["resource_id"]), None)
        if source is None:
            QMessageBox.warning(self, "منبع نامعتبر", "منبع انتخاب‌شده پیدا نشد.")
            return None
        values.update(
            category=source["category"] or "سایر",
            resource_title=source["title"] or "منبع",
            unit=source["unit"],
        )
        return values, activity

    def _selected_requirement(self):
        row = self.resource_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "انتخاب ردیف", "یک ردیف از جدول نیاز منابع انتخاب کنید.")
            return None
        cell = self.resource_table.item(row, 0)
        requirement_id = cell.data(Qt.UserRole) if cell else None
        conn = connect()
        try:
            item = conn.execute(
                "SELECT * FROM activity_resource_requirement WHERE id=? AND project_id=?",
                (requirement_id, self.project_id),
            ).fetchone()
            return dict(item) if item else None
        finally:
            conn.close()

    def add_requirement(self):
        result = self._resource_dialog()
        if result is None:
            return
        values, activity = result
        conn = connect()
        try:
            conn.execute(
                """
                INSERT INTO activity_resource_requirement(
                    project_id,activity_id,resource_id,category,resource_title,unit,
                    quantity_per_activity_unit,notes,source
                ) VALUES(?,?,?,?,?,?,?,?, 'MANUAL')
                """,
                (
                    self.project_id, activity["id"], values["resource_id"], values["category"],
                    values["resource_title"], values["unit"],
                    values["quantity_per_activity_unit"], values["notes"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def edit_requirement(self):
        current = self._selected_requirement()
        if not current:
            return
        result = self._resource_dialog(current)
        if result is None:
            return
        values, activity = result
        conn = connect()
        try:
            conn.execute(
                """
                UPDATE activity_resource_requirement SET activity_id=?,resource_id=?,category=?,
                    resource_title=?,unit=?,quantity_per_activity_unit=?,notes=?,source='MANUAL'
                WHERE id=? AND project_id=?
                """,
                (
                    activity["id"], values["resource_id"], values["category"], values["resource_title"],
                    values["unit"], values["quantity_per_activity_unit"], values["notes"],
                    current["id"], self.project_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def delete_requirement(self):
        current = self._selected_requirement()
        if not current:
            return
        answer = QMessageBox.question(self, "تأیید حذف", f"نیاز منبع «{current['resource_title']}» حذف شود؟")
        if answer != QMessageBox.Yes:
            return
        conn = connect()
        try:
            conn.execute(
                "DELETE FROM activity_resource_requirement WHERE id=? AND project_id=?",
                (current["id"], self.project_id),
            )
            conn.commit()
        finally:
            conn.close()
        self.refresh()


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
            "ثبت روزانه هزینه‌های عادی لازم نیست. ریز هزینه پایه هر فعالیت را در صفحه «برآورد هزینه و منابع فعالیت» تعریف کنید؛ "
            "این دفتر برای هزینه‌های اضافه/پیش‌بینی‌نشده (مثل لودر اضافی) و ثبت‌های واقعی تجمیعی است. "
            "برآورد EAC فقط وقتی محاسبه می‌شود که بودجه، پیشرفت و هزینه واقعی کافی باشد."
        )
        actual_note.setWordWrap(True)
        actual_note.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(actual_note)
        self.actual_table = QTableWidget()
        _configure_table(
            self.actual_table,
            ["تاریخ", "دوره", "نوع", "شرح", "مبلغ (ریال)", "پیش‌بینی‌نشده", "فعالیت مرتبط", "توضیحات"],
        )
        layout.addWidget(self.actual_table)
        actual_actions = QHBoxLayout()
        self.add_actual_button = QPushButton("ثبت هزینه/درآمد اضافی یا پیش‌بینی‌نشده")
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
                format_date(entry["entry_date"]), entry["period"] or "", kind, entry["category"],
                f"{float(entry['amount'] or 0):,.0f}", "بله" if entry["is_unplanned"] else "خیر", activity_label, entry["notes"] or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, entry["id"])
                self.actual_table.setItem(row_index, column, item)
        self.actual_table.resizeColumnsToContents()
        eac_text = (
            f"برآورد هزینه پایه به‌علاوه هزینه‌های پیش‌بینی‌نشده، برای "
            f"{performance['budgeted_activity_count']} فعالیت بودجه‌بندی‌شده از "
            f"{performance['total_activity_count']} فعالیت: {performance['eac_assessed']:,.0f} ریال."
            if performance["eac_assessed"] is not None
            else "هنوز ریز هزینه واحدمحور تعریف نشده است؛ در صفحه «برآورد هزینه و منابع فعالیت» اقلام و بهای واحد را ثبت کنید."
        )
        self.actual_summary.setText(
            f"واقعی ثبت‌شده: درآمد {performance['actual_revenue']:,.0f} ریال | "
            f"هزینه‌های ثبت‌شده {performance['actual_cost']:,.0f} ریال | "
            f"خالص {performance['actual_net']:,.0f} ریال. "
            f"از هزینه‌های ثبت‌شده، {performance['unplanned_actual_cost']:,.0f} ریال پیش‌بینی‌نشده است. "
            f"ارزش کسب‌شده بر مبنای بودجه هزینه (EV): {performance['earned_value_cost']:,.0f} ریال. "
            f"پوشش فعالیت‌ها با ریز هزینه پایه: {performance['cost_model_coverage_pct']:.1f}٪؛ "
            f"فعالیت فاقد بودجه واحدمحور: {performance['unbudgeted_activity_count']}. "
            f"{eac_text} شاخص CPI و هزینه باقی‌مانده نمایش داده نمی‌شوند، چون برای محاسبه معتبر آن‌ها باید هزینه واقعی عادی به‌صورت کامل تجمیع شود؛ ثبت روزانه برای برآورد پایه لازم نیست."
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
        revenue_reference = revenue.get("summary_reference_amount", 0.0)
        revenue_difference = revenue.get("summary_reconciliation_difference")
        cost_reference = cost.get("summary_reference_amount", 0.0)
        cost_difference = cost.get("summary_reconciliation_difference")
        reference_note = ""
        if revenue_reference:
            reference_note += (
                f" درآمد کل در خلاصه Excel {revenue_reference:,.0f} ریال است؛ "
                f"اختلاف آن با جمع نرخ‌های درآمد فعالیت‌ها "
                f"{revenue_difference:,.0f} ریال است. این دو جمع نشده‌اند تا دوباره‌شماری نشود."
            )
        if cost_reference:
            reference_note += (
                f" هزینه خلاصه Excel {cost_reference:,.0f} ریال و اختلاف آن با ریز هزینه‌های "
                f"واحدمحور فعالیت‌ها {cost_difference:,.0f} ریال است."
            )
        self.forecast_summary.setText(
            "مبنای برآورد: بهای واحد و ضریب هزینه فعالیت، به همراه ردیف‌های تخصیص‌یافته موجود. "
            f"درآمد پیشرفت‌وزن‌شده {revenue['earned_to_date']:,.0f} از {revenue['linked_budget']:,.0f} ریال؛ "
            f"هزینه پیشرفت‌وزن‌شده {cost['earned_to_date']:,.0f} از {cost['linked_budget']:,.0f} ریال. "
            f"بدون تخصیص مشخص: درآمد {revenue['unallocated_amount']:,.0f} و هزینه {cost['unallocated_amount']:,.0f} ریال. "
            + reference_note +
            " این ارقام بودجه/برآورد هستند و جایگزین ثبت هزینه یا درآمد واقعی نیستند."
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
        initial["is_unplanned"] = "بله" if initial.get("is_unplanned") else "خیر"
        initial["entry_type"] = (
            "درآمد واقعی" if initial.get("entry_type") == "REVENUE"
            else "هزینه واقعی" if initial.get("entry_type") == "COST"
            else "هزینه واقعی"
        )
        fields = [
            ("entry_date", "تاریخ", "date", {}),
            ("period", "دوره گزارش", "text", {}),
            ("entry_type", "نوع رکورد", "choice", {"choices": ["درآمد واقعی", "هزینه واقعی"]}),
            ("category", "شرح / دسته", "text", {}),
            ("amount", "مبلغ (ریال)", "number", {"minimum": 0, "decimals": 0}),
            ("activity_id", "فعالیت مرتبط", "activity", {"choices": choices}),
            ("is_unplanned", "هزینه پیش‌بینی‌نشده؟", "choice", {"choices": ["خیر", "بله"]}),
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
                    is_unplanned=(values["is_unplanned"] == "بله"),
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

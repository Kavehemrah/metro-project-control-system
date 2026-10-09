from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)
from .db import connect
from .services.control import aggregate_activity_control, critical_activities
from .theme import ACCENT, BG as THEME_BG, MUTED as THEME_MUTED, NAV, TEXT as THEME_TEXT, font_family

NAVY = NAV
BLUE = ACCENT
GREEN = "#17866B"
ORANGE = "#C57A16"
RED = "#C84D45"
BG = THEME_BG
TEXT = THEME_TEXT
MUTED = THEME_MUTED


def money(value):
    return f"{value / 1e9:,.2f} B"


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
            continue

        child_layout = item.layout()
        if child_layout is not None:
            _clear_layout(child_layout)
            child_layout.deleteLater()


def card(title, value, subtitle="", accent=BLUE):
    frame = QFrame()
    frame.setObjectName("card")
    frame.setStyleSheet(
        f"QFrame#card{{background:white;border:1px solid #DDE6E2;"
        f"border-radius:8px;border-top:3px solid {accent};}}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(3)
    frame.setMinimumHeight(104)

    title_label = QLabel(title)
    title_label.setStyleSheet(f"color:{MUTED};font-size:12px;")
    value_label = QLabel(value)
    value_label.setStyleSheet(
        f"color:{accent};font-size:25px;font-weight:700;"
    )
    subtitle_label = QLabel(subtitle)
    subtitle_label.setStyleSheet(f"color:{MUTED};font-size:10px;")

    layout.addWidget(title_label)
    layout.addWidget(value_label)
    layout.addWidget(subtitle_label)
    return frame


class MiniChart(QWidget):
    def __init__(self, values, labels, title):
        super().__init__()
        self.values = values
        self.labels = labels
        self.title = title
        self.setMinimumHeight(210)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor("white"))

        painter.setPen(QPen(QColor("#DDE5EC")))
        for index in range(5):
            y = 48 + index * (rect.height() - 75) / 4
            painter.drawLine(20, y, rect.width() - 18, y)

        painter.setPen(QPen(QColor(TEXT)))
        painter.setFont(QFont(font_family(), 11, QFont.Bold))
        painter.drawText(18, 26, self.title)

        max_value = max(self.values) or 1
        points = []
        for index, value in enumerate(self.values):
            x = 35 + index * (rect.width() - 70) / max(1, len(self.values) - 1)
            y = 48 + (rect.height() - 85) * (1 - value / max_value)
            points.append((x, y))

        painter.setPen(QPen(QColor(BLUE), 3))
        for first, second in zip(points, points[1:]):
            painter.drawLine(first[0], first[1], second[0], second[1])

        painter.setBrush(QBrush(QColor(BLUE)))
        painter.setPen(Qt.NoPen)
        for (x, y), label in zip(points, self.labels):
            painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
            painter.setPen(QColor(MUTED))
            painter.setFont(QFont(font_family(), 9))
            painter.drawText(
                x - 25,
                rect.height() - 12,
                50,
                16,
                Qt.AlignCenter,
                label,
            )
            painter.setPen(Qt.NoPen)


class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.setLayoutDirection(Qt.RightToLeft)
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.content = QWidget()
        self.content.setLayoutDirection(Qt.RightToLeft)
        self.content_layout = QVBoxLayout(self.content)
        self.scroll_area.setWidget(self.content)
        page_layout.addWidget(self.scroll_area)
        self.build()

    def build(self):
        root = self.content_layout
        _clear_layout(root)

        conn = connect()
        project = conn.execute(
            "SELECT * FROM project WHERE id=1"
        ).fetchone()
        kpi = conn.execute(
            "SELECT * FROM kpi WHERE project_id=1"
        ).fetchone()
        months = conn.execute(
            "SELECT * FROM monthly_finance WHERE project_id=1 ORDER BY id"
        ).fetchall()
        activities = critical_activities(conn, 1, limit=6)
        risks = conn.execute(
            "SELECT * FROM risk WHERE project_id=1 ORDER BY score DESC"
        ).fetchall()
        discrepancies = conn.execute(
            "SELECT * FROM discrepancy WHERE project_id=1"
        ).fetchall()
        controls = aggregate_activity_control(conn, 1)
        conn.close()

        physical_actual = kpi["physical_progress"] if kpi else 0

        root.setContentsMargins(22, 20, 22, 24)
        root.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel(project["name"])
        title.setStyleSheet(
            f"font-size:22px;font-weight:700;color:{TEXT};"
        )
        period = QLabel(project["period_label"])
        period.setStyleSheet(f"color:{MUTED};")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(period)
        root.addLayout(header)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        cards.addWidget(
            card(
                "پیشرفت فیزیکی",
                f"{physical_actual * 100:.2f}%",
                "برنامه دوماهه",
                BLUE,
            )
        )
        cards.addWidget(
            card(
                "درآمد دو ماهه",
                money(kpi["revenue"]),
                "کارکرد + تعدیل + سایر",
                GREEN,
            )
        )
        cards.addWidget(
            card(
                "هزینه دو ماهه",
                money(kpi["cost"]),
                "مبنای خلاصه",
                RED,
            )
        )
        cards.addWidget(
            card(
                "بالانس مالی",
                money(kpi["balance"]),
                "درآمد منهای هزینه",
                BLUE,
            )
        )
        root.addLayout(cards)

        charts = QHBoxLayout()
        charts.addWidget(
            MiniChart(
                [physical_actual * 100],
                ["دوره"],
                "پیشرفت فیزیکی ثبت‌شده از اکسل",
            )
        )
        charts.addWidget(
            MiniChart(
                [month["revenue"] / 1e9 for month in months] or [0],
                [str(month["month"]) for month in months] or ["-"],
                "درآمد دوره‌ها (میلیارد ریال)",
            )
        )
        root.addLayout(charts)

        activity_frame = QFrame()
        activity_frame.setStyleSheet("background:white;border:1px solid #E4EAF0;border-radius:8px;")
        activity_layout = QVBoxLayout(activity_frame)
        activity_heading = QLabel(f"فعالیت‌های نیازمند اقدام  |  {len(activities)} مورد")
        activity_heading.setStyleSheet(f"font-size:15px;font-weight:700;color:{TEXT};")
        activity_layout.addWidget(activity_heading)

        activity_table = QTableWidget()
        activity_table.setColumnCount(6)
        activity_table.setHorizontalHeaderLabels(
            ["اولویت", "فعالیت", "جبهه کاری", "پیشرفت", "مانده", "علت پیگیری"]
        )
        activity_table.horizontalHeader().setStretchLastSection(True)
        activity_table.verticalHeader().setVisible(False)
        activity_table.setEditTriggers(QTableWidget.NoEditTriggers)
        activity_table.setAlternatingRowColors(True)
        activity_table.setSelectionBehavior(QTableWidget.SelectRows)
        activity_table.setShowGrid(False)
        activity_table.setWordWrap(False)
        activity_table.setMinimumHeight(220)
        activity_table.verticalHeader().setDefaultSectionSize(36)
        activity_table.setRowCount(max(1, len(activities)))
        activity_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        activity_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        activity_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)

        if not activities:
            activity_table.setItem(0, 0, QTableWidgetItem("فعالیت بحرانی یا نیازمند پیگیری ثبت نشده است"))
            activity_table.setSpan(0, 0, 1, 6)
        else:
            for row, activity in enumerate(activities):
                values = [
                    activity["priority_label"],
                    activity["title"],
                    activity["zone"],
                    f"{activity['physical_progress_pct']:.1f}%",
                    f"{activity['remaining_qty']:,.2f}",
                    activity["reason"],
                ]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(str(value))
                    if column == 0:
                        cell.setForeground(QColor(RED if activity["priority"] == 3 else ORANGE))
                        cell.setFont(QFont(font_family(), 9, QFont.Bold))
                    activity_table.setItem(row, column, cell)
        activity_layout.addWidget(activity_table)
        root.addWidget(activity_frame)

        lower = QHBoxLayout()
        lower.setSpacing(14)

        risk_frame = QFrame()
        risk_frame.setStyleSheet("background:white;border:1px solid #E4EAF0;border-radius:8px;")
        risk_layout = QVBoxLayout(risk_frame)
        risk_heading = QLabel("ریسک‌های فعال")
        risk_heading.setStyleSheet(f"font-size:15px;font-weight:700;color:{TEXT};")
        risk_layout.addWidget(risk_heading)

        risk_table = QTableWidget()
        risk_table.setColumnCount(3)
        risk_table.setHorizontalHeaderLabels(["ریسک", "امتیاز", "اقدام"])
        risk_table.verticalHeader().setVisible(False)
        risk_table.setEditTriggers(QTableWidget.NoEditTriggers)
        risk_table.setAlternatingRowColors(True)
        risk_table.setSelectionBehavior(QTableWidget.SelectRows)
        risk_table.setShowGrid(False)
        risk_table.setWordWrap(False)
        risk_table.verticalHeader().setDefaultSectionSize(36)
        risk_table.setRowCount(max(1, min(5, len(risks))))

        if not risks:
            risk_table.setItem(0, 0, QTableWidgetItem("ریسکی ثبت نشده است"))
            risk_table.setSpan(0, 0, 1, 3)
        else:
            for row, risk in enumerate(risks[:5]):
                values = [risk["title"], risk["score"], risk["action"]]
                for column, value in enumerate(values):
                    risk_table.setItem(
                        row,
                        column,
                        QTableWidgetItem(str(value)),
                    )

        risk_layout.addWidget(risk_table)
        lower.addWidget(risk_frame, 3)

        discrepancy_frame = QFrame()
        discrepancy_frame.setStyleSheet("background:white;border:1px solid #E4EAF0;border-radius:8px;")
        discrepancy_layout = QVBoxLayout(discrepancy_frame)
        discrepancy_heading = QLabel("مغایرت‌های سیستم")
        discrepancy_heading.setStyleSheet(f"font-size:15px;font-weight:700;color:{TEXT};")
        discrepancy_layout.addWidget(discrepancy_heading)

        if not discrepancies:
            empty = QLabel("مغایرتی ثبت نشده است")
            empty.setStyleSheet(f"color:{MUTED};font-weight:600;")
            discrepancy_layout.addWidget(empty)
        else:
            for discrepancy in discrepancies:
                label = QLabel(f"• {discrepancy['title']}")
                accent = (
                    RED if discrepancy["severity"] == "HIGH" else ORANGE
                )
                label.setStyleSheet(
                    f"color:{accent};font-weight:600;"
                )
                label.setWordWrap(True)
                discrepancy_layout.addWidget(label)

        discrepancy_layout.addStretch()
        lower.addWidget(discrepancy_frame, 2)

        root.addLayout(lower)

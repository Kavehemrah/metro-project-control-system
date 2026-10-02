from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
)
from .db import connect

NAVY = "#102A43"
BLUE = "#1677D2"
GREEN = "#18A56B"
ORANGE = "#F5A623"
RED = "#E74C3C"
BG = "#F4F7FA"
TEXT = "#17324D"
MUTED = "#6B7C8F"


def money(value):
    return f"{value / 1e9:,.2f} B"


def card(title, value, subtitle="", accent=BLUE):
    frame = QFrame()
    frame.setObjectName("card")
    frame.setStyleSheet(
        f"QFrame#card{{background:white;border:1px solid #E4EAF0;"
        f"border-radius:12px;border-top:4px solid {accent};}}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 12, 18, 12)

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
        painter.setFont(QFont("Arial", 11, QFont.Bold))
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
            painter.setFont(QFont("Arial", 9))
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
        self.build()

    def build(self):
        root = self.layout()
        if root is None:
            root = QVBoxLayout(self)
        else:
            while root.count():
                item = root.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

        conn = connect()
        project = conn.execute(
            "SELECT * FROM project WHERE id=1"
        ).fetchone()
        kpi = conn.execute(
            "SELECT * FROM kpi WHERE project_id=1"
        ).fetchone()
        months = conn.execute(
            "SELECT * FROM monthly_finance WHERE project_id=1"
        ).fetchall()
        activities = conn.execute(
            "SELECT * FROM activity WHERE project_id=1 ORDER BY delay_days DESC"
        ).fetchall()
        risks = conn.execute(
            "SELECT * FROM risk WHERE project_id=1 ORDER BY score DESC"
        ).fetchall()
        discrepancies = conn.execute(
            "SELECT * FROM discrepancy WHERE project_id=1"
        ).fetchall()
        conn.close()

        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel(project["name"])
        title.setStyleSheet(
            f"font-size:21px;font-weight:700;color:{TEXT};"
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
                f"{kpi['physical_progress'] * 100:.2f}%",
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
                [6.54, 14.26, 16.8],
                ["مهر", "آبان", "هدف"],
                "پیشرفت / عملکرد / پیش‌بینی",
            )
        )
        charts.addWidget(
            MiniChart(
                [324.2, 355.0],
                ["مهر", "آبان"],
                "درآمد دو ماهه (میلیارد ریال)",
            )
        )
        root.addLayout(charts)

        middle = QHBoxLayout()

        activity_frame = QFrame()
        activity_frame.setStyleSheet(
            "background:white;border:1px solid #E4EAF0;border-radius:12px;"
        )
        activity_layout = QVBoxLayout(activity_frame)
        activity_layout.addWidget(QLabel("فعالیت‌های بحرانی"))

        activity_table = QTableWidget(min(5, len(activities)), 4)
        activity_table.setHorizontalHeaderLabels(
            ["فعالیت", "جبهه", "تأخیر", "وضعیت"]
        )
        activity_table.horizontalHeader().setStretchLastSection(True)
        activity_table.verticalHeader().setVisible(False)
        activity_table.setEditTriggers(QTableWidget.NoEditTriggers)

        for row, activity in enumerate(activities[:5]):
            values = [
                activity["title"],
                activity["zone"],
                f"{activity['delay_days']} روز",
                activity["status"],
            ]
            for column, value in enumerate(values):
                activity_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(str(value)),
                )

        activity_layout.addWidget(activity_table)
        middle.addWidget(activity_frame, 2)

        risk_frame = QFrame()
        risk_frame.setStyleSheet(
            "background:white;border:1px solid #E4EAF0;border-radius:12px;"
        )
        risk_layout = QVBoxLayout(risk_frame)
        risk_layout.addWidget(QLabel("ریسک‌های فعال"))

        risk_table = QTableWidget(min(5, len(risks)), 3)
        risk_table.setHorizontalHeaderLabels(["ریسک", "امتیاز", "اقدام"])
        risk_table.verticalHeader().setVisible(False)
        risk_table.setEditTriggers(QTableWidget.NoEditTriggers)

        for row, risk in enumerate(risks[:5]):
            values = [risk["title"], risk["score"], risk["action"]]
            for column, value in enumerate(values):
                risk_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(str(value)),
                )

        risk_layout.addWidget(risk_table)
        middle.addWidget(risk_frame, 2)

        discrepancy_frame = QFrame()
        discrepancy_frame.setStyleSheet(
            "background:white;border:1px solid #E4EAF0;border-radius:12px;"
        )
        discrepancy_layout = QVBoxLayout(discrepancy_frame)
        discrepancy_layout.addWidget(QLabel("مغایرت‌های سیستم"))

        for discrepancy in discrepancies:
            label = QLabel(f"• {discrepancy['title']}")
            accent = (
                RED if discrepancy["severity"] == "HIGH" else ORANGE
            )
            label.setStyleSheet(
                f"color:{accent};font-weight:600;"
            )
            discrepancy_layout.addWidget(label)

        discrepancy_layout.addStretch()
        middle.addWidget(discrepancy_frame, 1)

        root.addLayout(middle)

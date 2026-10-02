import sys
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QWidget,
)
from PySide6.QtCore import Qt
from app.db import seed
from app.ui import Dashboard, NAVY, BG, TEXT
from app.modules import (
    ActivityPage,
    FinancePage,
    ReportsPage,
    ResourcePage,
    RiskPage,
    SettingsPage,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Metro Project Control System")
        self.resize(1500, 900)
        self.setLayoutDirection(Qt.RightToLeft)

        root = QWidget()
        root.setStyleSheet(
            f"QWidget{{font-family:Segoe UI,Arial;background:{BG};color:{TEXT};}}"
        )
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)

        side = QListWidget()
        side.setFixedWidth(215)
        side.setStyleSheet(
            f"""
            QListWidget{{background:{NAVY};color:white;border:0;padding:14px;}}
            QListWidget::item{{padding:15px 8px;border-radius:8px;}}
            QListWidget::item:selected{{background:#1677D2;}}
            """
        )
        side.addItems(
            [
                "داشبورد مدیریتی",
                "برنامه اجرایی",
                "پیشرفت فیزیکی",
                "درآمد و هزینه",
                "منابع و ماشین‌آلات",
                "نیروی انسانی",
                "مصالح و انبار",
                "ریسک و اقدامات اصلاحی",
                "گزارش‌ها",
                "تنظیمات",
            ]
        )

        stack = QStackedWidget()
        self.stack = stack
        self.dashboard = Dashboard()
        stack.addWidget(self.dashboard)
        stack.addWidget(ActivityPage("برنامه اجرایی"))
        stack.addWidget(ActivityPage("پیشرفت فیزیکی", progress_only=True))
        stack.addWidget(FinancePage())
        stack.addWidget(ResourcePage("منابع و ماشین‌آلات", "ماشین‌آلات"))
        stack.addWidget(ResourcePage("نیروی انسانی", "نیروی انسانی"))
        stack.addWidget(ResourcePage("مصالح و انبار", "مصالح"))
        stack.addWidget(RiskPage())
        stack.addWidget(ReportsPage())
        stack.addWidget(SettingsPage(self.refresh_data))

        side.currentRowChanged.connect(stack.setCurrentIndex)
        side.currentRowChanged.connect(self.refresh_dashboard_if_selected)
        side.setCurrentRow(0)

        lay.addWidget(side)
        lay.addWidget(stack)
        self.setCentralWidget(root)

    def refresh_data(self):
        self.dashboard.build()
        finance = self.stack.widget(3)
        finance.refresh()

    def refresh_dashboard_if_selected(self, index):
        if index == 0:
            self.dashboard.build()


if __name__ == "__main__":
    seed()
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

import sys
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from app.db import seed
from app.theme import NAV, apply_theme
from app.ui import Dashboard
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
        application = QApplication.instance()
        if application is not None:
            apply_theme(application)
        self.setWindowTitle("Metro Project Control System")
        self.resize(1440, 880)
        self.setMinimumSize(1080, 700)
        self.setLayoutDirection(Qt.RightToLeft)

        root = QWidget()
        root.setObjectName("mainRoot")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        side = QListWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(236)
        side.setSpacing(2)
        side.setUniformItemSizes(True)

        sidebar = QWidget()
        sidebar.setFixedWidth(236)
        sidebar.setStyleSheet(f"background:{NAV};")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(13, 20, 13, 16)
        sidebar_layout.setSpacing(12)
        brand = QLabel("مترو | کنترل پروژه")
        brand.setStyleSheet("color:white;font-size:15pt;font-weight:700;padding:5px 9px;")
        brand.setWordWrap(True)
        subtitle = QLabel("مدیریت و پایش عملیات")
        subtitle.setStyleSheet(f"color:#B4CBC3;font-size:9pt;padding:0 9px 10px;")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addWidget(side, 1)
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

        lay.addWidget(sidebar)
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

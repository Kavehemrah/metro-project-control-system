import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout, QListWidget, QLabel, QStackedWidget
from PySide6.QtCore import Qt
from app.db import seed
from app.ui import Dashboard, NAVY, BG, TEXT


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
        stack.addWidget(Dashboard())

        for name in [
            "برنامه اجرایی",
            "پیشرفت فیزیکی",
            "درآمد و هزینه",
            "منابع و ماشین‌آلات",
            "نیروی انسانی",
            "مصالح و انبار",
            "ریسک و اقدامات اصلاحی",
            "گزارش‌ها",
            "تنظیمات",
        ]:
            widget = QLabel(f"{name}\n\nاین ماژول در نسخه بعدی فعال می‌شود.")
            widget.setAlignment(Qt.AlignCenter)
            widget.setStyleSheet("font-size:20px;color:#6B7C8F;")
            stack.addWidget(widget)

        side.currentRowChanged.connect(stack.setCurrentIndex)
        side.setCurrentRow(0)

        lay.addWidget(side)
        lay.addWidget(stack)
        self.setCentralWidget(root)


if __name__ == "__main__":
    seed()
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

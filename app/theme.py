from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase


FONT_FAMILY = "Segoe UI"
BG = "#F3F6F5"
SURFACE = "#FFFFFF"
TEXT = "#20332F"
MUTED = "#71817C"
NAV = "#173A35"
ACCENT = "#138A72"
LINE = "#DDE6E2"


def font_family():
    return FONT_FAMILY


def apply_theme(app):
    font_path = Path(__file__).parent / "assets" / "Vazirmatn-wght.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id >= 0:
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            global FONT_FAMILY
            FONT_FAMILY = families[0]

    app.setFont(QFont(FONT_FAMILY, 10))
    app.setStyleSheet(
        f"""
        QWidget {{
            color: {TEXT};
            font-family: "{FONT_FAMILY}";
            font-size: 10pt;
        }}
        QWidget#mainRoot {{ background: {BG}; }}
        QListWidget#sidebar {{
            background: {NAV};
            color: #E8F1EE;
            border: 0;
            padding: 10px 9px;
            outline: 0;
        }}
        QListWidget#sidebar::item {{
            min-height: 22px;
            padding: 10px 12px;
            margin: 2px 0;
            border-radius: 6px;
            color: #D8E7E2;
        }}
        QListWidget#sidebar::item:hover {{ background: #254D46; }}
        QListWidget#sidebar::item:selected {{
            color: white;
            background: {ACCENT};
            font-weight: 700;
        }}
        QStackedWidget {{ background: {BG}; }}
        QFrame#surface {{
            background: {SURFACE};
            border: 1px solid {LINE};
            border-radius: 8px;
        }}
        QPushButton {{
            background: {ACCENT};
            color: white;
            border: 1px solid {ACCENT};
            border-radius: 6px;
            padding: 8px 14px;
            min-height: 20px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background: #0F755F; border-color: #0F755F; }}
        QPushButton:pressed {{ background: #0B604E; }}
        QPushButton:disabled {{ background: #A8BBB5; border-color: #A8BBB5; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
            background: white;
            border: 1px solid #C9D7D1;
            border-radius: 5px;
            padding: 6px 8px;
            selection-background-color: {ACCENT};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {ACCENT};
        }}
        QTableWidget {{
            background: white;
            alternate-background-color: #F5F8F7;
            border: 1px solid {LINE};
            border-radius: 6px;
            gridline-color: #E8EEEB;
            selection-background-color: #D9EEE8;
            selection-color: {TEXT};
        }}
        QHeaderView::section {{
            color: #405650;
            background: #EAF1EE;
            border: 0;
            border-bottom: 1px solid {LINE};
            padding: 8px 7px;
            font-weight: 700;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: #BDCDC7;
            min-height: 28px;
            border-radius: 5px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QToolTip {{
            color: white;
            background: #203A34;
            border: 0;
            padding: 5px 8px;
        }}
        """
    )
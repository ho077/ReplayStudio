"""Dark Fluent-ish theme for ReplayStudio.

Palette draws on VALORANT's own identity (signal red #FF4655 on near-black
blue) but stays a low-distraction desktop UI: large quiet surfaces, one accent
colour used only for meaning (selection / recording / active filter).
"""
from __future__ import annotations

BG = "#16161A"
SURFACE = "#1E1E24"
SURFACE_2 = "#26262E"
CARD = "#232329"
CARD_HOVER = "#2D2D36"
BORDER = "#33333D"
ACCENT = "#FF4655"
ACCENT_SOFT = "#FF465520"
BLUE = "#3D7EFF"
TEXT = "#EDEDF0"
TEXT_DIM = "#9A9AA6"
TEXT_FAINT = "#6C6C78"
GREEN = "#38D39F"
AMBER = "#F5A623"

RADIUS = 10

QSS = f"""
/* ---------- base ---------- */
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {BG}; }}

/* ---------- sidebar ---------- */
#sidebar {{
    background: {SURFACE};
    border-right: 1px solid {BORDER};
}}
#navButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 9px 12px;
    text-align: left;
    color: {TEXT_DIM};
    font-size: 13.5px;
}}
#navButton:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
#navButton[active="true"] {{
    background: {ACCENT_SOFT};
    color: {TEXT};
    border-left: 3px solid {ACCENT};
    padding-left: 9px;
}}
#brandLabel {{ color: {TEXT}; font-size: 16px; font-weight: 700; letter-spacing: 0.5px; }}
#brandSub {{ color: {TEXT_FAINT}; font-size: 11px; }}

/* ---------- title bar ---------- */
#titleBar {{ background: {SURFACE}; }}
#titleButton {{
    background: transparent; border: none; border-radius: 6px;
    color: {TEXT_DIM}; font-size: 14px; min-width: 42px; min-height: 30px;
}}
#titleButton:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
#titleButton[role="close"]:hover {{ background: #E81123; color: white; }}

/* ---------- generic controls ---------- */
QPushButton {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
    color: {TEXT};
}}
QPushButton:hover {{ background: {CARD_HOVER}; border-color: #43434F; }}
QPushButton:pressed {{ background: #191920; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: {SURFACE}; }}
QPushButton[accent="true"] {{
    background: {ACCENT}; border: none; color: white; font-weight: 600;
}}
QPushButton[accent="true"]:hover {{ background: #FF5C6A; }}
QPushButton[flat="true"] {{ background: transparent; border: none; padding: 6px; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {TEXT};
    min-height: 18px;
}}
QComboBox:hover, QLineEdit:hover {{ border-color: #45454F; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT}; selection-color: white;
    outline: none; padding: 4px;
}}
QLineEdit[role="search"] {{ padding-left: 30px; }}

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {BORDER}; background: {SURFACE_2};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QSlider::groove:horizontal {{
    height: 4px; background: {SURFACE_2}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -4px 0; border-radius: 6px;
    background: {TEXT};
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #3A3A45; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #4A4A57; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: #3A3A45; border-radius: 5px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QToolTip {{
    background: #2E2E38; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px 8px;
}}

/* ---------- list / grid ---------- */
QListView, QTreeView, QTableView {{
    background: transparent; border: none; outline: none;
    selection-background-color: transparent;
}}
QHeaderView::section {{
    background: {SURFACE}; color: {TEXT_DIM}; border: none;
    padding: 8px; font-weight: 600;
}}

/* ---------- panels ---------- */
#panel {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
}}
#filterBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
#statLabel {{ color: {TEXT_DIM}; font-size: 12px; }}
#sectionTitle {{ color: {TEXT}; font-size: 15px; font-weight: 600; }}

QProgressBar {{
    background: {SURFACE_2}; border: none; border-radius: 3px; height: 6px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QMenu {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 4px;
}}
QMenu::item {{ padding: 7px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}

QTabWidget::pane {{ border: none; background: {BG}; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM}; padding: 9px 16px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
"""


def badge_style(color: str) -> str:
    return f"""
        background: {color}22; color: {color}; border: none;
        border-radius: 5px; padding: 2px 7px; font-size: 11px; font-weight: 600;
    """

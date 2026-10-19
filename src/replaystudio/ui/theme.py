"""Multi-theme engine for ReplayStudio.

Each theme is a Valorant-flavoured palette (signal red, Jett's cyan, Phoenix's
ember, Reyna's violet, Sage's jade, Cypher's surveillance gold) expressed as one
immutable :class:`Palette`. Nothing else in the app hard-codes a colour: widgets
either read the live module constants or register a `$KEY` template through
:func:`style`, which is re-rendered whenever :func:`apply_theme` runs.

Adding a theme = adding one Palette. No other file changes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from string import Template


@dataclass(frozen=True)
class Palette:
    key: str
    name: str
    desc: str

    # surfaces (dark end → card end)
    BG: str
    SURFACE: str
    SURFACE_2: str
    CARD: str
    CARD_HOVER: str
    BORDER: str
    BORDER_HOVER: str
    PRESSED: str
    GRAD_A: str           # placeholder cover gradient start
    GRAD_B: str           # placeholder cover gradient end

    # accent
    ACCENT: str
    ACCENT_HOVER: str
    ACCENT_SOFT: str
    ON_ACCENT: str        # text/foreground colour placed on top of ACCENT

    # text
    TEXT: str
    TEXT_DIM: str
    TEXT_FAINT: str

    # semantic
    BLUE: str
    GREEN: str
    AMBER: str
    DEATH: str

    # chrome
    SCROLL: str
    SCROLL_HOVER: str
    TOOLTIP_BG: str
    CLOSE_HOVER: str

    RADIUS: int = 10


# --------------------------------------------------------------------- palettes
def _default() -> Palette:
    return Palette(
        key="valorant", name="无畏契约", desc="官方标志红 × 近黑蓝，默认主题",
        BG="#16161A", SURFACE="#1E1E24", SURFACE_2="#26262E",
        CARD="#232329", CARD_HOVER="#2D2D36", BORDER="#33333D",
        BORDER_HOVER="#43434F", PRESSED="#191920",
        GRAD_A="#2B2B35", GRAD_B="#1D1D24",
        ACCENT="#FF4655", ACCENT_HOVER="#FF5C6A", ACCENT_SOFT="#FF465520",
        ON_ACCENT="#FFFFFF",
        TEXT="#EDEDF0", TEXT_DIM="#9A9AA6", TEXT_FAINT="#6C6C78",
        BLUE="#3D7EFF", GREEN="#38D39F", AMBER="#F5A623", DEATH="#7A7A8C",
        SCROLL="#3A3A45", SCROLL_HOVER="#4A4A57",
        TOOLTIP_BG="#2E2E38", CLOSE_HOVER="#E81123",
    )


def _jett() -> Palette:
    return Palette(
        key="jett", name="捷影 · 疾风", desc="刀刃青 × 高空气流，冷冽锐利",
        BG="#111820", SURFACE="#18222C", SURFACE_2="#1F2B37",
        CARD="#1C2733", CARD_HOVER="#243542", BORDER="#2D3C4A",
        BORDER_HOVER="#3E5568", PRESSED="#131C25",
        GRAD_A="#233442", GRAD_B="#16212B",
        ACCENT="#7BF1FF", ACCENT_HOVER="#9BF5FF", ACCENT_SOFT="#7BF1FF20",
        ON_ACCENT="#06232B",
        TEXT="#E6F4F8", TEXT_DIM="#93A7B4", TEXT_FAINT="#65798A",
        BLUE="#4FA8FF", GREEN="#46D9A8", AMBER="#FFC65A", DEATH="#6E8595",
        SCROLL="#33465A", SCROLL_HOVER="#42586F",
        TOOLTIP_BG="#1F2B37", CLOSE_HOVER="#C9424E",
    )


def _phoenix() -> Palette:
    return Palette(
        key="phoenix", name="炼狱 · 焚火", desc="余烬橙 × 炭黑，燃烧质感",
        BG="#191411", SURFACE="#221A15", SURFACE_2="#2B211A",
        CARD="#251C16", CARD_HOVER="#33261D", BORDER="#3D2E22",
        BORDER_HOVER="#54402E", PRESSED="#1B1511",
        GRAD_A="#33241A", GRAD_B="#1F1610",
        ACCENT="#FF8A1A", ACCENT_HOVER="#FFA244", ACCENT_SOFT="#FF8A1A20",
        ON_ACCENT="#2A1400",
        TEXT="#F7EFE6", TEXT_DIM="#B3A08C", TEXT_FAINT="#826F5C",
        BLUE="#5E9CFF", GREEN="#8FD14F", AMBER="#FFD447", DEATH="#8E7A66",
        SCROLL="#483525", SCROLL_HOVER="#5C442F",
        TOOLTIP_BG="#2B211A", CLOSE_HOVER="#D13A2A",
    )


def _reyna() -> Palette:
    return Palette(
        key="reyna", name="蕾娜 · 紫靛", desc="吞噬紫 × 夜幕黑，进攻性高对比",
        BG="#171320", SURFACE="#201A2C", SURFACE_2="#282136",
        CARD="#241D30", CARD_HOVER="#2F2640", BORDER="#362C46",
        BORDER_HOVER="#4A3C60", PRESSED="#191324",
        GRAD_A="#2E2440", GRAD_B="#1D1730",
        ACCENT="#B566FF", ACCENT_HOVER="#C583FF", ACCENT_SOFT="#B566FF20",
        ON_ACCENT="#1A0A2B",
        TEXT="#F0E9FA", TEXT_DIM="#A698BC", TEXT_FAINT="#78688C",
        BLUE="#7C9BFF", GREEN="#5FD8A8", AMBER="#FFB347", DEATH="#8F80A6",
        SCROLL="#42365A", SCROLL_HOVER="#524470",
        TOOLTIP_BG="#282136", CLOSE_HOVER="#C0392B",
    )


def _sage() -> Palette:
    return Palette(
        key="sage", name="贤者 · 青玉", desc="治愈翡翠 × 静水绿，柔和不刺眼",
        BG="#111B18", SURFACE="#182622", SURFACE_2="#1F312B",
        CARD="#1C2B26", CARD_HOVER="#243934", BORDER="#2B4038",
        BORDER_HOVER="#3C5A4E", PRESSED="#131E1A",
        GRAD_A="#223A32", GRAD_B="#152420",
        ACCENT="#58E8C1", ACCENT_HOVER="#7BF0D0", ACCENT_SOFT="#58E8C120",
        ON_ACCENT="#04241C",
        TEXT="#E3F5EF", TEXT_DIM="#8FAEA4", TEXT_FAINT="#628075",
        BLUE="#57B6FF", GREEN="#7BE8A0", AMBER="#FFCE5C", DEATH="#7D968C",
        SCROLL="#2F4A3F", SCROLL_HOVER="#3D5F51",
        TOOLTIP_BG="#1F312B", CLOSE_HOVER="#C2453A",
    )


def _cypher() -> Palette:
    return Palette(
        key="cypher", name="零 · 监控", desc="监视青铜 × 沙丘灰，情报终端风",
        BG="#181712", SURFACE="#211F18", SURFACE_2="#2A271E",
        CARD="#252219", CARD_HOVER="#312D22", BORDER="#3B372A",
        BORDER_HOVER="#514A37", PRESSED="#1A1813",
        GRAD_A="#302C21", GRAD_B="#1E1B15",
        ACCENT="#D8C163", ACCENT_HOVER="#E5D082", ACCENT_SOFT="#D8C16320",
        ON_ACCENT="#241F06",
        TEXT="#F2EEDC", TEXT_DIM="#ABA488", TEXT_FAINT="#7D7660",
        BLUE="#6FB0E8", GREEN="#A8CF6A", AMBER="#F0A93B", DEATH="#928B72",
        SCROLL="#453F2E", SCROLL_HOVER="#564F3B",
        TOOLTIP_BG="#2A271E", CLOSE_HOVER="#C2402F",
    )


PALETTES: dict[str, Palette] = {p.key: p for p in (
    _default(), _jett(), _phoenix(), _reyna(), _sage(), _cypher(),
)}
DEFAULT_KEY = "valorant"


def palette(key: str | None = None) -> Palette:
    return PALETTES.get(key or "", _current) if key else _current


def palette_list() -> list[Palette]:
    return list(PALETTES.values())


# --------------------------------------------------- live module-level shortcuts
# Every other module does `from . import theme as T` and reads T.ACCENT etc.
# Rebinding these on theme switch is what makes those reads follow the palette.
_current: Palette = _default()
BG = SURFACE = SURFACE_2 = CARD = CARD_HOVER = BORDER = BORDER_HOVER = PRESSED = ""
GRAD_A = GRAD_B = ACCENT = ACCENT_HOVER = ACCENT_SOFT = ON_ACCENT = ""
TEXT = TEXT_DIM = TEXT_FAINT = BLUE = GREEN = AMBER = DEATH = ""
SCROLL = SCROLL_HOVER = TOOLTIP_BG = CLOSE_HOVER = ""
RADIUS = 10
QSS = ""


def _publish(p: Palette):
    global _current, QSS
    # Normalize the one translucent slot to Qt's #AARRGGBB *before* anything
    # reads the palette — `T.style()` renders from `asdict(_current)`, so a
    # web-style value here would leak into every inline template.
    soft = p.ACCENT_SOFT
    if len(soft) == 9 and soft.startswith("#"):
        p = replace(p, ACCENT_SOFT=f"#{soft[7:9]}{soft[1:7]}")
    _current = p
    for k, v in asdict(p).items():
        if k in ("key", "name", "desc"):
            continue
        globals()[k] = v
    QSS = build_qss(p)


def qalpha(color: str, alpha: str) -> str:
    """#RRGGBB + 'AA' → Qt '#AARRGGBB' (for QColor and QSS alike)."""
    if len(color) == 7 and color.startswith("#"):
        return f"#{alpha}{color[1:]}"
    return color


# ------------------------------------------------------------- dynamic styling
_STYLED: list[tuple[object, str]] = []


def style(widget, template: str):
    """Apply a `$KEY` template now, and re-apply it whenever the theme changes.

    Uses `string.Template` rather than `str.format` because Qt stylesheets are
    full of literal braces (`QPushButton { … }`) that `format` would choke on.
    """
    _STYLED[:] = [pair for pair in _STYLED if pair[0] is not widget]
    widget.setStyleSheet(_render(template))
    _STYLED.append((widget, template))
    return widget


def _render(template: str) -> str:
    return Template(template).safe_substitute(**asdict(_current))


_CURRENT_KEY = DEFAULT_KEY


def current_key() -> str:
    return _CURRENT_KEY


def build_qss(p: Palette) -> str:
    # `p` is already normalized by _publish (ACCENT_SOFT in #AARRGGBB order) —
    # converting here a second time would rotate the bytes again (#20FF4655 →
    # #5520FF46, i.e. opaque green), which is exactly the bug the nav bar had.
    return Template(_QSS_TEMPLATE).substitute(**asdict(p))


def apply_theme(app, key: str):
    """Rebind the palette, regenerate QSS, re-render every registered style."""
    global _CURRENT_KEY
    _CURRENT_KEY = key if key in PALETTES else DEFAULT_KEY
    _publish(palette(_CURRENT_KEY))

    alive = []
    for widget, tpl in _STYLED:
        try:
            widget.setStyleSheet(_render(tpl))
            alive.append((widget, tpl))
        except RuntimeError:            # underlying C++ object already deleted
            continue
    _STYLED[:] = alive

    if app is not None:
        app.setStyleSheet(QSS)
        for w in app.allWidgets():
            w.style().unpolish(w)
            w.style().polish(w)
    return _CURRENT_KEY


def init_theme(app, key: str) -> str:
    _STYLED.clear()
    return apply_theme(app, key)


def badge_style(color: str) -> str:
    return f"""
        background: {color}22; color: {color}; border: none;
        border-radius: 5px; padding: 2px 7px; font-size: 11px; font-weight: 600;
    """


# ------------------------------------------------------------------- stylesheet
_QSS_TEMPLATE = """
/* ---------- base ---------- */
QWidget {
    background: $BG;
    color: $TEXT;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog { background: $BG; }

/* ---------- sidebar ---------- */
#sidebar {
    background: $SURFACE;
    border-right: 1px solid $BORDER;
}
#navButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 9px 12px;
    text-align: left;
    color: $TEXT_DIM;
    font-size: 13.5px;
}
#navButton:hover { background: $SURFACE_2; color: $TEXT; }
#navButton[active="true"] {
    background: $ACCENT_SOFT;
    color: $TEXT;
    border-left: 3px solid $ACCENT;
    padding-left: 9px;
}
#brandLabel { color: $TEXT; font-size: 16px; font-weight: 700; letter-spacing: 0.5px; }
#brandSub { color: $TEXT_FAINT; font-size: 11px; }

/* ---------- title bar ---------- */
#titleBar { background: $SURFACE; }
#titleButton {
    background: transparent; border: none; border-radius: 6px;
    color: $TEXT_DIM; font-size: 14px; min-width: 42px; min-height: 30px;
}
#titleButton:hover { background: $SURFACE_2; color: $TEXT; }
#titleButton[role="close"]:hover { background: $CLOSE_HOVER; color: white; }

/* ---------- generic controls ---------- */
QPushButton {
    background: $SURFACE_2;
    border: 1px solid $BORDER;
    border-radius: 8px;
    padding: 7px 14px;
    color: $TEXT;
}
QPushButton:hover { background: $CARD_HOVER; border-color: $BORDER_HOVER; }
QPushButton:pressed { background: $PRESSED; }
QPushButton:disabled { color: $TEXT_FAINT; background: $SURFACE; }
QPushButton[accent="true"] {
    background: $ACCENT; border: none; color: $ON_ACCENT; font-weight: 600;
}
QPushButton[accent="true"]:hover { background: $ACCENT_HOVER; }
QPushButton[flat="true"] { background: transparent; border: none; padding: 6px; }

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    background: $SURFACE_2;
    border: 1px solid $BORDER;
    border-radius: 8px;
    padding: 6px 10px;
    color: $TEXT;
    min-height: 18px;
}
QComboBox:hover, QLineEdit:hover { border-color: $BORDER_HOVER; }
QLineEdit:focus, QComboBox:focus { border-color: $ACCENT; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: $SURFACE_2; border: 1px solid $BORDER;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT;
    outline: none; padding: 4px;
}
QLineEdit[role="search"] { padding-left: 30px; }

QCheckBox { spacing: 6px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid $BORDER; background: $SURFACE_2;
}
QCheckBox::indicator:checked { background: $ACCENT; border-color: $ACCENT; }

QSlider::groove:horizontal {
    height: 4px; background: $SURFACE_2; border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 12px; height: 12px; margin: -4px 0; border-radius: 6px;
    background: $TEXT;
}
QSlider::handle:horizontal:hover { background: $ACCENT; }
QSlider::sub-page:horizontal { background: $ACCENT; border-radius: 2px; }

QScrollBar:vertical {
    background: transparent; width: 10px; margin: 0;
}
QScrollBar::handle:vertical {
    background: $SCROLL; border-radius: 5px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: $SCROLL_HOVER; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { height: 10px; background: transparent; }
QScrollBar::handle:horizontal { background: $SCROLL; border-radius: 5px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QToolTip {
    background: $TOOLTIP_BG; color: $TEXT; border: 1px solid $BORDER; padding: 5px 8px;
}

/* ---------- list / grid ---------- */
QListView, QTreeView, QTableView {
    background: transparent; border: none; outline: none;
    selection-background-color: transparent;
}
QHeaderView::section {
    background: $SURFACE; color: $TEXT_DIM; border: none;
    padding: 8px; font-weight: 600;
}

/* ---------- panels ---------- */
#panel {
    background: $SURFACE; border: 1px solid $BORDER; border-radius: ${RADIUS}px;
}
#filterBar { background: $SURFACE; border-bottom: 1px solid $BORDER; }
#statLabel { color: $TEXT_DIM; font-size: 12px; }
#sectionTitle { color: $TEXT; font-size: 15px; font-weight: 600; }

/* ---------- theme picker ---------- */
#themeCard {
    background: $CARD; border: 2px solid $BORDER; border-radius: 10px;
    text-align: left; padding: 10px 12px;
}
#themeCard:hover { background: $CARD_HOVER; border-color: $BORDER_HOVER; }
#themeCard[active="true"] { border-color: $ACCENT; }
#themeName { color: $TEXT; font-size: 13px; font-weight: 600; }
#themeDesc { color: $TEXT_FAINT; font-size: 11px; }
#swatch { border-radius: 4px; border: 1px solid $BORDER; }

QProgressBar {
    background: $SURFACE_2; border: none; border-radius: 3px; height: 6px;
}
QProgressBar::chunk { background: $ACCENT; border-radius: 3px; }

QMenu {
    background: $SURFACE_2; border: 1px solid $BORDER; padding: 4px;
}
QMenu::item { padding: 7px 22px; border-radius: 5px; }
QMenu::item:selected { background: $ACCENT; color: $ON_ACCENT; }

QTabWidget::pane { border: none; background: $BG; }
QTabBar::tab {
    background: transparent; color: $TEXT_DIM; padding: 9px 16px;
    border: none; border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: $TEXT; border-bottom: 2px solid $ACCENT; }
"""


_publish(_default())

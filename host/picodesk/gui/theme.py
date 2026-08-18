"""Design tokens and the Qt stylesheet, lifted from picodeskgui.pen.

The Pencil deck is the visual spec; these names mirror its variables one for
one so a change there has an obvious counterpart here. Colors are chosen for
a dark engineering-instrument look: signals and values in a monospace face,
UI chrome in Inter, semantic colors distinct from the teal accent, and one
color per rate group so the fast path is identifiable at a glance.
"""

from __future__ import annotations

# --- palette (picodeskgui.pen variables) ------------------------------------
BG_APP = "#0E1014"
BG_SIDEBAR = "#12151B"
BG_PANEL = "#171B22"
BG_ELEVATED = "#1E232D"
BG_INSET = "#0A0C10"

BORDER = "#252B37"
BORDER_SOFT = "#1D232E"
BORDER_STRONG = "#323A49"

TEXT_PRIMARY = "#E8EBF1"
TEXT_SECONDARY = "#97A0B1"
TEXT_MUTED = "#5C6577"

ACCENT = "#2DD4BF"
ACCENT_SOFT = "#2DD4BF1F"
OK = "#4ADE80"
OK_SOFT = "#4ADE801F"
WARN = "#E8B44C"
WARN_SOFT = "#E8B44C1F"
ERR = "#EF6A5F"
ERR_SOFT = "#EF6A5F26"
INFO = "#5EA7F5"
INFO_SOFT = "#5EA7F51F"

#: One color per dispatcher rate group (RTE-002), used everywhere a group is
#: shown so the fast path reads instantly.
RATE_COLORS = {
    "fast_1ms": ("#F0925B", "#F0925B1F"),
    "slow_10ms": (INFO, INFO_SOFT),
    "slow_100ms": ("#9F8CF2", "#9F8CF21F"),
}
RATE_LABELS = {
    "fast_1ms": "FAST · 1 MS",
    "slow_10ms": "10 MS",
    "slow_100ms": "100 MS",
}

FONT_UI = "Inter"
FONT_MONO = "JetBrains Mono"
#: Faces that exist on a bare CI box; Qt falls back left to right.
FONT_UI_STACK = f'"{FONT_UI}", "DejaVu Sans", sans-serif'
FONT_MONO_STACK = f'"{FONT_MONO}", "DejaVu Sans Mono", monospace'


def severity_color(severity: str) -> str:
    return {"error": ERR, "warning": WARN, "note": INFO}.get(
        severity, TEXT_SECONDARY)


STYLESHEET = f"""
QWidget {{
    background: {BG_APP};
    color: {TEXT_PRIMARY};
    font-family: {FONT_UI_STACK};
    font-size: 13px;
}}
QMainWindow::separator {{ background: {BORDER}; width: 1px; height: 1px; }}

/* Labels inherit the app background otherwise, painting boxes over panels. */
QLabel, QCheckBox {{ background: transparent; }}

#Sidebar {{ background: {BG_SIDEBAR}; border-right: 1px solid {BORDER}; }}
#LogoText {{ font-size: 15px; font-weight: 600; }}
#LogoVersion {{ color: {TEXT_MUTED}; font-family: {FONT_MONO_STACK};
                font-size: 10px; }}

#NavItem {{
    background: transparent; border: none; border-radius: 6px;
    padding: 8px 10px; text-align: left; color: {TEXT_SECONDARY};
    font-weight: 500;
}}
#NavItem:hover {{ background: {BG_ELEVATED}; }}
#NavItem:checked {{ background: {ACCENT_SOFT}; color: {TEXT_PRIMARY}; }}

#Panel {{ background: {BG_PANEL}; border: 1px solid {BORDER};
          border-radius: 8px; }}
#PanelHeader {{ color: {TEXT_MUTED}; font-family: {FONT_MONO_STACK};
                font-size: 10px; font-weight: 600; letter-spacing: 0.8px; }}
#Title {{ font-size: 18px; font-weight: 600; }}
#Subtitle {{ color: {TEXT_MUTED}; font-size: 12px; }}
#Muted {{ color: {TEXT_MUTED}; }}
#Mono {{ font-family: {FONT_MONO_STACK}; }}

QPushButton {{
    background: {BG_ELEVATED}; border: 1px solid {BORDER_STRONG};
    border-radius: 6px; padding: 8px 14px; font-weight: 500;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; border-color: {BORDER_SOFT}; }}
QPushButton#Primary {{
    background: {ACCENT}; color: #0A0F0E; border: none; font-weight: 600;
}}
QPushButton#Primary:disabled {{ background: {BORDER_STRONG};
                                color: {TEXT_MUTED}; }}

QLineEdit {{
    background: {BG_INSET}; border: 1px solid {BORDER_SOFT};
    border-radius: 6px; padding: 6px 10px;
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QTreeView, QTableView, QListView {{
    background: {BG_PANEL}; border: none; alternate-background-color: {BG_PANEL};
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT_PRIMARY};
    outline: none;
}}
QTreeView::item, QTableView::item {{ padding: 5px 4px; }}
QHeaderView::section {{
    background: {BG_PANEL}; color: {TEXT_MUTED}; border: none;
    border-bottom: 1px solid {BORDER_SOFT}; padding: 7px 6px;
    font-family: {FONT_MONO_STACK}; font-size: 10px; font-weight: 600;
}}

QTextBrowser#Console {{
    background: {BG_INSET}; border: 1px solid {BORDER}; border-radius: 8px;
    font-family: {FONT_MONO_STACK}; font-size: 11px;
    selection-background-color: {ACCENT_SOFT};
}}

QProgressBar {{
    background: {BG_ELEVATED}; border: none; border-radius: 4px;
    height: 7px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG};
                               border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG};
                                 border-radius: 5px; min-width: 24px; }}

QSplitter::handle {{ background: {BORDER_SOFT}; }}
QToolTip {{ background: {BG_ELEVATED}; color: {TEXT_PRIMARY};
            border: 1px solid {BORDER_STRONG}; padding: 4px; }}
"""

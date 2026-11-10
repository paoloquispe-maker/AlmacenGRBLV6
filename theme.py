"""
Sistema de Diseño y Tokens HMI/SCADA (Norma ISA-101)
Define tokens de color, tipografía, funciones de estilo QSS, cálculo de contraste
y provisión de iconografía monocromática consistente (qtawesome).
"""

from typing import Dict, Optional, Tuple
from PySide6.QtGui import QIcon
import qtawesome as qta

# ═══════════════════════════════════════════════════════════════════════
# 1. TOKENS DE DISEÑO (ISA-101)
# ═══════════════════════════════════════════════════════════════════════

DARK_TOKENS: Dict[str, str] = {
    "BG": "#14181F",
    "SURFACE": "#1E242D",
    "SURFACE_ALT": "#181D25",
    "BORDER": "#2F3844",
    "TEXT": "#E6EAF0",
    "TEXT_MUTED": "#8B96A5",
    "ACCENT": "#3B82C4",
    "OK": "#3FA66B",
    "WARN": "#E0A030",
    "ALARM": "#D64545",
    "NEUTRAL": "#5C6673",
}

LIGHT_TOKENS: Dict[str, str] = {
    "BG": "#EEF1F5",
    "SURFACE": "#FFFFFF",
    "SURFACE_ALT": "#F5F7FA",
    "BORDER": "#CBD2DC",
    "TEXT": "#1B2430",
    "TEXT_MUTED": "#5B6675",
    "ACCENT": "#2A68A5",  # Oscurecido ~15% para cumplir contraste ≥ 4.5:1
    "OK": "#2F8253",      # Oscurecido ~15%
    "WARN": "#B87F1E",    # Oscurecido ~15%
    "ALARM": "#B33232",   # Oscurecido ~15%
    "NEUTRAL": "#5C6673",
}

# Tipografías recomendadas con respaldos del sistema
FONT_FAMILY_UI = "'Inter', 'Segoe UI', -apple-system, sans-serif"
FONT_FAMILY_MONO = "'JetBrains Mono', 'Consolas', 'Courier New', monospace"


def get_tokens(theme_name: str = "dark") -> Dict[str, str]:
    """Retorna el diccionario de tokens correspondiente al tema."""
    if theme_name.lower() == "light":
        return dict(LIGHT_TOKENS)
    return dict(DARK_TOKENS)


# ═══════════════════════════════════════════════════════════════════════
# 2. VERIFICADOR DE CONTRASTE WCAG (≥ 4.5:1)
# ═══════════════════════════════════════════════════════════════════════

def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def get_relative_luminance(hex_color: str) -> float:
    """Calcula la luminancia relativa según estándar WCAG 2.1."""
    r, g, b = _hex_to_rgb(hex_color)
    rgb = [c / 255.0 for c in (r, g, b)]
    lum = []
    for c in rgb:
        if c <= 0.03928:
            lum.append(c / 12.92)
        else:
            lum.append(((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lum[0] + 0.7152 * lum[1] + 0.0722 * lum[2]


def get_contrast_ratio(hex_foreground: str, hex_background: str) -> float:
    """Retorna el ratio de contraste (ej: 4.5 para 4.5:1)."""
    l1 = get_relative_luminance(hex_foreground)
    l2 = get_relative_luminance(hex_background)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def verify_theme_contrast(theme_name: str = "dark") -> Dict[str, Tuple[float, bool]]:
    """Verifica si los textos principales cumplen con la norma WCAG AA (≥ 4.5:1)."""
    tokens = get_tokens(theme_name)
    bg = tokens["BG"]
    surface = tokens["SURFACE"]
    checks = {
        "TEXT on BG": (get_contrast_ratio(tokens["TEXT"], bg), get_contrast_ratio(tokens["TEXT"], bg) >= 4.5),
        "TEXT on SURFACE": (get_contrast_ratio(tokens["TEXT"], surface), get_contrast_ratio(tokens["TEXT"], surface) >= 4.5),
        "TEXT_MUTED on BG": (get_contrast_ratio(tokens["TEXT_MUTED"], bg), get_contrast_ratio(tokens["TEXT_MUTED"], bg) >= 3.0),
        "TEXT_MUTED on SURFACE": (get_contrast_ratio(tokens["TEXT_MUTED"], surface), get_contrast_ratio(tokens["TEXT_MUTED"], surface) >= 3.0),
    }
    return checks


# ═══════════════════════════════════════════════════════════════════════
# 3. HELPER DE ICONOGRAFÍA (qtawesome)
# ═══════════════════════════════════════════════════════════════════════

def get_icon(name: str, color: Optional[str] = None, theme_name: str = "dark", **kwargs) -> QIcon:
    """
    Obtiene un QIcon monocromático usando la librería qtawesome.
    Usa el color del texto activo por defecto si no se especifica.
    """
    if color is None:
        tokens = get_tokens(theme_name)
        color = tokens["TEXT"]
    try:
        return qta.icon(name, color=color, **kwargs)
    except Exception:
        return QIcon()


# ═══════════════════════════════════════════════════════════════════════
# 4. GENERADOR GLOBAL DE HOJA DE ESTILOS (QSS)
# ═══════════════════════════════════════════════════════════════════════

def build_qss(theme_name: str = "dark") -> str:
    """
    Genera el stylesheet QSS completo basado en los tokens del sistema.
    Cumple con la norma ISA-101: superficies neutras, ausencia de saturación innecesaria,
    y estados claros para botones primarios, secundarios, peligro y deshabilitados.
    """
    t = get_tokens(theme_name)

    return f"""
    /* ── BASE GENERAL ── */
    QMainWindow, QDialog {{
        background-color: {t["BG"]};
        color: {t["TEXT"]};
    }}

    QWidget {{
        color: {t["TEXT"]};
        font-family: {FONT_FAMILY_UI};
        font-size: 13px;
        selection-background-color: {t["ACCENT"]};
        selection-color: #FFFFFF;
    }}

    /* ── PANELES Y CONTENEDORES ── */
    QFrame[panel="true"], QFrame#card {{
        background-color: {t["SURFACE"]};
        border: 1px solid {t["BORDER"]};
        border-radius: 6px;
    }}

    QGroupBox {{
        background-color: {t["SURFACE"]};
        border: 1px solid {t["BORDER"]};
        border-radius: 6px;
        margin-top: 24px;
        padding-top: 14px;
        font-weight: 600;
        font-size: 13px;
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 6px;
        color: {t["TEXT"]};
        font-weight: 600;
    }}

    /* ── PESTAÑAS (QTabWidget) ── */
    QTabWidget::pane {{
        border: 1px solid {t["BORDER"]};
        background-color: {t["BG"]};
        border-radius: 6px;
        top: -1px;
    }}

    QTabBar::tab {{
        background-color: {t["SURFACE_ALT"]};
        color: {t["TEXT_MUTED"]};
        padding: 10px 20px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        border: 1px solid {t["BORDER"]};
        border-bottom: none;
        margin-right: 4px;
        font-weight: 500;
    }}

    QTabBar::tab:selected {{
        background-color: {t["SURFACE"]};
        color: {t["TEXT"]};
        border-bottom: 2px solid {t["ACCENT"]};
        font-weight: 600;
    }}

    QTabBar::tab:hover:!selected {{
        background-color: {t["SURFACE"]};
        color: {t["TEXT"]};
    }}

    /* ── BOTONES ── */
    /* Botón Secundario (por defecto) */
    QPushButton {{
        background-color: {t["SURFACE"]};
        color: {t["TEXT"]};
        border: 1px solid {t["BORDER"]};
        border-radius: 6px;
        padding: 7px 16px;
        min-height: 20px;
        font-weight: 500;
    }}

    QPushButton:hover {{
        border-color: {t["ACCENT"]};
        color: {t["TEXT"]};
    }}

    QPushButton:pressed {{
        background-color: {t["SURFACE_ALT"]};
    }}

    QPushButton:disabled {{
        background-color: {t["SURFACE_ALT"]};
        color: {t["NEUTRAL"]};
        border-color: {t["BORDER"]};
    }}

    /* Botón Primario */
    QPushButton[level="primary"] {{
        background-color: {t["ACCENT"]};
        color: #FFFFFF;
        border: 1px solid {t["ACCENT"]};
        font-weight: 600;
    }}

    QPushButton[level="primary"]:hover {{
        background-color: #2F72B0;
        border-color: #2F72B0;
    }}

    QPushButton[level="primary"]:pressed {{
        background-color: #255F94;
    }}

    QPushButton[level="primary"]:disabled {{
        background-color: {t["SURFACE_ALT"]};
        color: {t["NEUTRAL"]};
        border-color: {t["BORDER"]};
    }}

    /* Botón Peligro (Borde y texto ALARM, fondo transparente) */
    QPushButton[level="danger"] {{
        background-color: transparent;
        color: {t["ALARM"]};
        border: 1px solid {t["ALARM"]};
        font-weight: 600;
    }}

    QPushButton[level="danger"]:hover {{
        background-color: {t["ALARM"]};
        color: #FFFFFF;
    }}

    QPushButton[level="danger"]:disabled {{
        background-color: transparent;
        color: {t["NEUTRAL"]};
        border-color: {t["BORDER"]};
    }}

    /* Botón Paro de Emergencia E-STOP */
    QPushButton#btn_emergency_stop {{
        background-color: {t["ALARM"]};
        color: #FFFFFF;
        border: 1px solid {t["ALARM"]};
        border-radius: 6px;
        font-weight: 700;
        font-size: 12px;
        padding: 6px 8px;
        min-height: 28px;
    }}

    QPushButton#btn_emergency_stop:hover {{
        background-color: #BC3333;
        border-color: #BC3333;
    }}

    QPushButton#btn_emergency_stop:pressed {{
        background-color: #9E2626;
    }}

    /* ── CAMPOS DE ENTRADA Y SELECTORES ── */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{
        background-color: {t["SURFACE_ALT"]};
        border: 1px solid {t["BORDER"]};
        border-radius: 4px;
        padding: 6px 10px;
        color: {t["TEXT"]};
        selection-background-color: {t["ACCENT"]};
    }}

    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {t["ACCENT"]};
    }}

    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border-left: 1px solid {t["BORDER"]};
    }}

    QComboBox QAbstractItemView {{
        background-color: {t["SURFACE"]};
        color: {t["TEXT"]};
        border: 1px solid {t["BORDER"]};
        selection-background-color: {t["ACCENT"]};
        selection-color: #FFFFFF;
    }}

    /* ── TABLAS Y ENCABEZADOS (QTableView, QTableWidget) ── */
    QTableView, QTableWidget {{
        background-color: {t["SURFACE"]};
        alternate-background-color: {t["SURFACE_ALT"]};
        gridline-color: {t["BORDER"]};
        border: 1px solid {t["BORDER"]};
        border-radius: 6px;
        color: {t["TEXT"]};
    }}

    QHeaderView::section {{
        background-color: {t["SURFACE_ALT"]};
        color: {t["TEXT_MUTED"]};
        padding: 8px;
        border: 1px solid {t["BORDER"]};
        font-weight: 600;
        font-size: 12px;
    }}

    /* ── SCROLLBARS Y SCROLLAREA ── */
    QScrollArea {{
        background-color: transparent;
        border: none;
    }}

    QScrollArea > QWidget > QWidget {{
        background-color: transparent;
    }}

    QScrollBar:vertical {{
        background: {t["BG"]};
        width: 10px;
        margin: 0px;
        border-radius: 5px;
    }}

    QScrollBar::handle:vertical {{
        background: {t["BORDER"]};
        min-height: 20px;
        border-radius: 5px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: {t["NEUTRAL"]};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QScrollBar:horizontal {{
        background: {t["BG"]};
        height: 10px;
        margin: 0px;
        border-radius: 5px;
    }}

    QScrollBar::handle:horizontal {{
        background: {t["BORDER"]};
        min-width: 20px;
        border-radius: 5px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background: {t["NEUTRAL"]};
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* ── TOOLTIPS ── */
    QToolTip {{
        background-color: {t["SURFACE"]};
        color: {t["TEXT"]};
        border: 1px solid {t["BORDER"]};
        padding: 6px 10px;
        border-radius: 4px;
        font-size: 12px;
    }}

    /* ── CHECKBOX & RADIO ── */
    QCheckBox, QRadioButton {{
        spacing: 8px;
        color: {t["TEXT"]};
    }}

    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {t["BORDER"]};
        background: {t["SURFACE_ALT"]};
        border-radius: 3px;
    }}

    QRadioButton::indicator {{
        border-radius: 8px;
    }}

    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {t["ACCENT"]};
        border-color: {t["ACCENT"]};
    }}

    /* ── PROGRESS BAR ── */
    QProgressBar {{
        background-color: {t["SURFACE_ALT"]};
        border: 1px solid {t["BORDER"]};
        border-radius: 4px;
        text-align: center;
        color: {t["TEXT"]};
        font-weight: 600;
    }}

    QProgressBar::chunk {{
        background-color: {t["ACCENT"]};
        border-radius: 3px;
    }}
    """

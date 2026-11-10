"""
Componentes Reutilizables de Interfaz HMI/SCADA (Norma ISA-101)
Incluye: StateChip, StatusBar, EmergencyBar, AlarmBanner, KpiCard,
CellTile, CellGridView, Warehouse2DCanvas, StepProgressBar.
"""

from typing import Dict, List, Optional, Callable, Any
from PySide6.QtCore import Qt, Signal, Slot, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PySide6.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QSizePolicy
)

import theme
from theme import FONT_FAMILY_UI, FONT_FAMILY_MONO


# ═══════════════════════════════════════════════════════════════════════
# 1. STATE CHIP (Pastilla de Estado: Icono + Texto + Token)
# ═══════════════════════════════════════════════════════════════════════

class StateChip(QFrame):
    """
    Pastilla compacta para visualizar estados industriales (OK, WARN, ALARM, ACCENT, NEUTRAL).
    Combina texto e icono para accesibilidad total (daltonismo y proyectores).
    """

    def __init__(
        self,
        text: str = "",
        state_type: str = "neutral",
        icon_name: Optional[str] = None,
        theme_name: str = "dark",
        parent=None
    ):
        super().__init__(parent)
        self.theme_name = theme_name
        self.state_type = state_type
        self.icon_name = icon_name

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(8, 4, 8, 4)
        self.layout.setSpacing(6)

        self.lbl_icon = QLabel()
        self.lbl_icon.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.layout.addWidget(self.lbl_icon)

        self.lbl_text = QLabel(text)
        self.lbl_text.setFont(QFont("Inter", 10, QFont.Bold))
        self.layout.addWidget(self.lbl_text)

        self.set_state(text, state_type, icon_name)

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self._apply_style()

    def set_state(self, text: str, state_type: str = "neutral", icon_name: Optional[str] = None):
        self.state_type = state_type
        self.lbl_text.setText(text)
        if icon_name is not None:
            self.icon_name = icon_name
        self._apply_style()

    def _apply_style(self):
        t = theme.get_tokens(self.theme_name)
        color_map = {
            "ok": t["OK"],
            "warn": t["WARN"],
            "alarm": t["ALARM"],
            "accent": t["ACCENT"],
            "neutral": t["NEUTRAL"],
        }
        accent_color = color_map.get(self.state_type.lower(), t["NEUTRAL"])

        # Icono con qtawesome si está definido
        if self.icon_name:
            pixmap = theme.get_icon(self.icon_name, color=accent_color, theme_name=self.theme_name).pixmap(14, 14)
            self.lbl_icon.setPixmap(pixmap)
            self.lbl_icon.show()
        else:
            self.lbl_icon.hide()

        # Estilo de la pastilla: fondo sutil (15% de opacidad) y borde sólido de 1px
        c = QColor(accent_color)
        bg_rgba = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.12)"

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_rgba};
                border: 1px solid {accent_color};
                border-radius: 4px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: {t["TEXT"]};
                font-family: {FONT_FAMILY_UI};
                font-size: 11px;
                font-weight: 600;
            }}
        """)


# ═══════════════════════════════════════════════════════════════════════
# 2. ALARM BANNER (Avisos de Alarma Reutilizables según ISA-101)
# ═══════════════════════════════════════════════════════════════════════

class AlarmBanner(QFrame):
    """
    Banner de notificaciones de alta prioridad con franja superior de 3 px
    y fondo neutro de superficie (evita saturación en bloque tipo pastel).
    Prioridad: alarm > conexion > almacen_lleno.
    """

    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self.theme_name = theme_name
        self.active_alarms: Dict[str, Dict] = {}
        self.hide()

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(16, 10, 16, 10)
        self.layout.setSpacing(12)

        self.lbl_icon = QLabel()
        self.layout.addWidget(self.lbl_icon)

        self.lbl_msg = QLabel("")
        self.lbl_msg.setFont(QFont("Inter", 11, QFont.Bold))
        self.layout.addWidget(self.lbl_msg, stretch=1)

        self.btn_action = QPushButton("")
        self.btn_action.hide()
        self.layout.addWidget(self.btn_action)

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self._render_highest_priority()

    def mostrar(
        self,
        tipo: str,
        mensaje: str,
        accion_btn_text: Optional[str] = None,
        accion_callback: Optional[Callable] = None
    ):
        """Muestra o actualiza una alarma específica."""
        self.active_alarms[tipo] = {
            "mensaje": mensaje,
            "accion_btn_text": accion_btn_text,
            "accion_callback": accion_callback
        }
        self._render_highest_priority()

    def ocultar(self, tipo: str):
        """Oculta una alarma por tipo."""
        if tipo in self.active_alarms:
            del self.active_alarms[tipo]
        self._render_highest_priority()

    def _render_highest_priority(self):
        if not self.active_alarms:
            self.hide()
            return

        priority_order = ["alarm", "conexion", "almacen_lleno"]
        target_tipo = None
        for p in priority_order:
            if p in self.active_alarms:
                target_tipo = p
                break

        if not target_tipo:
            target_tipo = list(self.active_alarms.keys())[0]

        info = self.active_alarms[target_tipo]
        t = theme.get_tokens(self.theme_name)

        if target_tipo == "alarm":
            top_color = t["ALARM"]
            icon_name = "mdi6.alert-octagon"
        elif target_tipo == "conexion":
            top_color = t["WARN"]
            icon_name = "mdi6.lan-disconnect"
        else:
            top_color = t["WARN"]
            icon_name = "mdi6.alert"

        self.lbl_msg.setText(info["mensaje"])
        pix = theme.get_icon(icon_name, color=top_color, theme_name=self.theme_name).pixmap(20, 20)
        self.lbl_icon.setPixmap(pix)

        # Franja superior de 3px, fondo SURFACE neutro, borde lateral y fondo sutil
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {t["SURFACE"]};
                border: 1px solid {t["BORDER"]};
                border-top: 3px solid {top_color};
                border-radius: 4px;
            }}
            QLabel {{
                color: {t["TEXT"]};
                background: transparent;
                border: none;
                font-family: {FONT_FAMILY_UI};
            }}
        """)

        if info["accion_btn_text"] and info["accion_callback"]:
            self.btn_action.setText(info["accion_btn_text"])
            self.btn_action.setProperty("level", "primary")
            self.btn_action.setStyleSheet(f"""
                QPushButton {{
                    background-color: {t["SURFACE_ALT"]};
                    border: 1px solid {top_color};
                    color: {top_color};
                    font-weight: 600;
                    padding: 5px 12px;
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: {top_color};
                    color: #FFFFFF;
                }}
            """)
            try:
                self.btn_action.clicked.disconnect()
            except Exception:
                pass
            self.btn_action.clicked.connect(info["accion_callback"])
            self.btn_action.show()
        else:
            self.btn_action.hide()

        self.show()


# ═══════════════════════════════════════════════════════════════════════
# 3. EMERGENCY BAR (Paro de Emergencia y Pausa Siempre Accesibles)
# ═══════════════════════════════════════════════════════════════════════

class EmergencyBar(QWidget):
    """
    Barra permanente con Pausa/Reanudar y Paro de Emergencia.
    El paro de emergencia actúa de forma inmediata SIN confirmación modal.
    """
    pause_clicked = Signal()
    resume_clicked = Signal()
    emergency_stop_clicked = Signal()

    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self.theme_name = theme_name
        self.is_paused = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Botón Pausa / Reanudar
        self.btn_pause = QPushButton("Pausar (!)")
        self.btn_pause.setFixedHeight(44)
        self.btn_pause.setFixedWidth(95)
        self.btn_pause.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_pause.setIcon(theme.get_icon("mdi6.pause", color=theme.get_tokens(theme_name)["WARN"], theme_name=theme_name))
        self.btn_pause.setToolTip("Pausa inmediata del movimiento (Feed Hold !)")
        self.btn_pause.clicked.connect(self._toggle_pause)
        layout.addWidget(self.btn_pause)

        # Botón Paro de Emergencia (E-STOP)
        self.btn_stop = QPushButton("PARO DE EMERGENCIA")
        self.btn_stop.setObjectName("btn_emergency_stop")
        self.btn_stop.setFixedHeight(44)
        self.btn_stop.setFixedWidth(210)
        self.btn_stop.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_stop.setIcon(theme.get_icon("mdi6.alert-octagon", color="#FFFFFF", theme_name=theme_name))
        self.btn_stop.setToolTip("Paro de emergencia inmediato (Soft Reset GRBL 0x18). Sin confirmación.")
        self.btn_stop.clicked.connect(self.emergency_stop_clicked.emit)
        layout.addWidget(self.btn_stop)

        self._apply_style()

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self._apply_style()

    def set_paused_state(self, paused: bool):
        self.is_paused = paused
        t = theme.get_tokens(self.theme_name)
        if paused:
            self.btn_pause.setText("Reanudar (~)")
            self.btn_pause.setIcon(theme.get_icon("mdi6.play", color=t["OK"], theme_name=self.theme_name))
            self.btn_pause.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {t["OK"]};
                    border: 1px solid {t["OK"]};
                    font-weight: 600;
                    padding: 8px 14px;
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background-color: {t["OK"]};
                    color: #FFFFFF;
                }}
            """)
        else:
            self.btn_pause.setText("Pausar (!)")
            self.btn_pause.setIcon(theme.get_icon("mdi6.pause", color=t["WARN"], theme_name=self.theme_name))
            self.btn_pause.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {t["WARN"]};
                    border: 1px solid {t["WARN"]};
                    font-weight: 600;
                    padding: 8px 14px;
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background-color: {t["WARN"]};
                    color: #FFFFFF;
                }}
            """)

    def _toggle_pause(self):
        if self.is_paused:
            self.resume_clicked.emit()
        else:
            self.pause_clicked.emit()

    def _apply_style(self):
        self.set_paused_state(self.is_paused)


# ═══════════════════════════════════════════════════════════════════════
# 4. STATUS BAR (Marco Fijo Superior de 56 px)
# ═══════════════════════════════════════════════════════════════════════

class StatusBar(QFrame):
    """
    Marco superior unificado de 56 px visible en todas las pestañas.
    Muestra: Título, Chips de conexión y equipo, Coordenadas X/Y/Z monoespaciadas,
    Selector COM + Conectar, y EmergencyBar.
    """
    connect_clicked = Signal(str)      # Emite el puerto COM seleccionado
    disconnect_clicked = Signal()

    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self.theme_name = theme_name
        self.is_connected = False
        self.origin_confirmed = False

        self.setFixedHeight(58)
        self.setObjectName("statusBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        # 1. Título Industrial
        self.lbl_title = QLabel("ALMACEN AS/RS")
        self.lbl_title.setFont(QFont("Inter", 12, QFont.Bold))
        layout.addWidget(self.lbl_title)

        # 2. Chips de Estado
        self.chip_conn = StateChip("Desconectado", "neutral", "mdi6.lan-disconnect", theme_name=theme_name)
        self.chip_state = StateChip("Desconectado", "neutral", "mdi6.power", theme_name=theme_name)
        layout.addWidget(self.chip_conn)
        layout.addWidget(self.chip_state)

        # 3. Lecturas de Posición X / Y / Z en Monoespaciada (Mismo Color de Texto)
        pos_box = QFrame()
        pos_lay = QHBoxLayout(pos_box)
        pos_lay.setContentsMargins(4, 2, 4, 2)
        pos_lay.setSpacing(4)

        self.cur_x, self.cur_y, self.cur_z = 0.0, 0.0, 0.0
        self.lbl_pos_coords = QLabel("X: 0.00  Y: 0.00  Z: 0.00")
        self.lbl_pos_coords.setFont(QFont("JetBrains Mono", 10, QFont.Bold))
        pos_lay.addWidget(self.lbl_pos_coords)

        self.lbl_origin_tag = QLabel("(Sin ref)")
        self.lbl_origin_tag.setFont(QFont("Inter", 9, QFont.Normal))
        pos_lay.addWidget(self.lbl_origin_tag)

        layout.addWidget(pos_box)
        layout.addStretch()

        # 4. Selector de Puerto COM y Conexión
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.addItems(["COM6", "COM3", "COM4", "COM5", "/dev/ttyUSB0"])
        self.port_combo.setFixedWidth(80)
        layout.addWidget(self.port_combo)

        self.btn_connect = QPushButton("Conectar")
        self.btn_connect.setFixedHeight(36)
        self.btn_connect.setIcon(theme.get_icon("mdi6.power", theme_name=theme_name))
        self.btn_connect.clicked.connect(self._handle_connect_toggle)
        layout.addWidget(self.btn_connect)

        # 5. Barra de Emergencia Fija
        self.emergency_bar = EmergencyBar(theme_name=theme_name)
        layout.addWidget(self.emergency_bar)

        self._apply_style()

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self.chip_conn.set_theme(theme_name)
        self.chip_state.set_theme(theme_name)
        self.emergency_bar.set_theme(theme_name)
        self._apply_style()

    def update_connection(self, connected: bool, port: str = ""):
        self.is_connected = connected
        if connected:
            self.chip_conn.set_state(f"Conectado ({port})", "ok", "mdi6.lan-connect")
            self.btn_connect.setText("Desconectar")
            self.btn_connect.setIcon(theme.get_icon("mdi6.power-plug-off", theme_name=self.theme_name))
        else:
            self.chip_conn.set_state("Desconectado", "neutral", "mdi6.lan-disconnect")
            self.chip_state.set_state("Desconectado", "neutral", "mdi6.power")
            self.btn_connect.setText("Conectar")
            self.btn_connect.setIcon(theme.get_icon("mdi6.power", theme_name=self.theme_name))
            self.set_origin_confirmed(False)
        self._apply_style()

    def update_machine_state(self, grbl_state: str):
        """Actualiza el chip de estado del equipo a partir del reporte GRBL."""
        st = grbl_state.strip()
        if not self.is_connected:
            self.chip_state.set_state("Desconectado", "neutral", "mdi6.power")
        elif "Alarm" in st:
            self.chip_state.set_state(f"Alarma ({st})", "alarm", "mdi6.alert-octagon")
        elif st.startswith("Hold"):
            self.chip_state.set_state("Pausa (Hold)", "warn", "mdi6.pause")
            self.emergency_bar.set_paused_state(True)
        elif "Run" in st:
            self.chip_state.set_state("En movimiento", "accent", "mdi6.motion-play")
            self.emergency_bar.set_paused_state(False)
        elif "Idle" in st:
            if self.origin_confirmed:
                self.chip_state.set_state("Listo", "ok", "mdi6.check-circle")
            else:
                self.chip_state.set_state("Sin referenciar", "warn", "mdi6.alert-circle-outline")
            self.emergency_bar.set_paused_state(False)
        else:
            self.chip_state.set_state(st, "neutral", "mdi6.cog")

    def update_coordinates(self, x: float, y: float, z: float):
        self.cur_x, self.cur_y, self.cur_z = x, y, z
        self.lbl_pos_coords.setText(f"X: {x:.2f}  Y: {y:.2f}  Z: {z:.2f}")

    def set_origin_confirmed(self, confirmed: bool):
        self.origin_confirmed = confirmed
        t = theme.get_tokens(self.theme_name)
        color = t["TEXT"] if confirmed else t["NEUTRAL"]
        self.lbl_pos_coords.setStyleSheet(f"color: {color};")
        self.lbl_origin_tag.setText("" if confirmed else "(Sin ref)")
        self.lbl_origin_tag.setStyleSheet(f"color: {t['WARN']}; font-weight: 600;")

    def _handle_connect_toggle(self):
        if self.is_connected:
            self.disconnect_clicked.emit()
        else:
            port = self.port_combo.currentText().strip() or "COM6"
            self.connect_clicked.emit(port)

    def _apply_style(self):
        t = theme.get_tokens(self.theme_name)
        self.setStyleSheet(f"""
            QFrame#statusBar {{
                background-color: {t["SURFACE"]};
                border-bottom: 1px solid {t["BORDER"]};
            }}
            QLabel {{
                color: {t["TEXT"]};
            }}
        """)
        self.set_origin_confirmed(self.origin_confirmed)


# ═══════════════════════════════════════════════════════════════════════
# 5. KPI CARD (Tarjetas de Indicadores Operativos)
# ═══════════════════════════════════════════════════════════════════════

class KpiCard(QFrame):
    """
    Tarjeta de indicador clave (Ocupación %, Movimientos).
    Título pequeño en TEXT_MUTED y valor grande monoespaciado.
    """

    def __init__(self, title: str, value: str = "0", unit: str = "", theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self.theme_name = theme_name

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 12, 14, 12)
        self.layout.setSpacing(4)

        self.lbl_title = QLabel(title.upper())
        self.lbl_title.setFont(QFont("Inter", 10, QFont.Bold))
        self.layout.addWidget(self.lbl_title)

        val_box = QHBoxLayout()
        val_box.setSpacing(4)
        self.lbl_val = QLabel(value)
        self.lbl_val.setFont(QFont("JetBrains Mono", 22, QFont.Bold))
        val_box.addWidget(self.lbl_val)

        if unit:
            self.lbl_unit = QLabel(unit)
            self.lbl_unit.setFont(QFont("Inter", 12, QFont.Normal))
            val_box.addWidget(self.lbl_unit, alignment=Qt.AlignBottom)

        val_box.addStretch()
        self.layout.addLayout(val_box)

        self._apply_style()

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self._apply_style()

    def set_value(self, value: str):
        self.lbl_val.setText(str(value))

    def _apply_style(self):
        t = theme.get_tokens(self.theme_name)
        self.setStyleSheet(f"""
            KpiCard {{
                background-color: {t["SURFACE"]};
                border: 1px solid {t["BORDER"]};
                border-radius: 6px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
        """)
        self.lbl_title.setStyleSheet(f"color: {t['TEXT_MUTED']};")
        self.lbl_val.setStyleSheet(f"color: {t['TEXT']};")


# ═══════════════════════════════════════════════════════════════════════
# 6. CELL TILE & CELL GRID VIEW (Malla de Celdas Actualizable en Sitio)
# ═══════════════════════════════════════════════════════════════════════

class CellTile(QPushButton):
    """
    Tarjeta individual de celda en la malla.
    Actualiza su apariencia en sitio (sin destruirse) según el estado:
    - Vacía: borde OK, texto "VACÍA"
    - Ocupada: borde ACCENT, texto "OCUPADA" y código
    - Reservada: borde WARN, texto "RESERVADA"
    - Sin posición: borde NEUTRAL discontinuo
    - Si el carro está sobre la celda (±5 mm): indicador "CARRO AQUÍ".
    """
    cell_clicked = Signal(str)

    def __init__(self, cell_id: str, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self.cell_id = cell_id
        self.theme_name = theme_name
        self.status = "empty"
        self.content = ""
        self.has_position = True
        self.is_car_here = False

        self.setFixedSize(120, 96)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(lambda: self.cell_clicked.emit(self.cell_id))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        top_box = QHBoxLayout()
        self.lbl_id = QLabel(cell_id)
        self.lbl_id.setFont(QFont("JetBrains Mono", 12, QFont.Bold))
        top_box.addWidget(self.lbl_id)

        self.lbl_car = QLabel("CARRO AQUI")
        self.lbl_car.setFont(QFont("Inter", 8, QFont.Bold))
        self.lbl_car.hide()
        top_box.addWidget(self.lbl_car, alignment=Qt.AlignRight)

        layout.addLayout(top_box)

        self.lbl_status = QLabel("VACIA")
        self.lbl_status.setFont(QFont("Inter", 10, QFont.Bold))
        layout.addWidget(self.lbl_status)

        self.lbl_content = QLabel("")
        self.lbl_content.setFont(QFont("Inter", 9))
        self.lbl_content.setWordWrap(True)
        layout.addWidget(self.lbl_content)

        layout.addStretch()
        self._apply_style()

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self._apply_style()

    def update_cell(self, status: str, content: str = "", has_position: bool = True, is_car_here: bool = False):
        self.status = status
        self.content = content or ""
        self.has_position = has_position
        self.is_car_here = is_car_here
        self._apply_style()

    def _apply_style(self):
        t = theme.get_tokens(self.theme_name)

        if not self.has_position:
            border_color = t["NEUTRAL"]
            border_style = "dashed"
            status_txt = "SIN POSICION"
            tint_color = t["NEUTRAL"]
        elif self.status == "occupied":
            border_color = t["ACCENT"]
            border_style = "solid"
            status_txt = "OCUPADA"
            tint_color = t["ACCENT"]
        elif self.status == "reserved":
            border_color = t["WARN"]
            border_style = "solid"
            status_txt = "RESERVADA"
            tint_color = t["WARN"]
        else:
            border_color = t["OK"]
            border_style = "solid"
            status_txt = "VACIA"
            tint_color = t["OK"]

        self.lbl_status.setText(status_txt)
        self.lbl_content.setText(self.content if self.status == "occupied" else "")

        if self.is_car_here:
            self.lbl_car.show()
            self.lbl_car.setStyleSheet(f"color: {t['ACCENT']}; font-weight: 700; background: {t['SURFACE_ALT']}; padding: 1px 4px; border-radius: 3px;")
        else:
            self.lbl_car.hide()

        c = QColor(tint_color)
        bg_tint = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.12)"

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg_tint};
                border: 2px {border_style} {border_color};
                border-radius: 6px;
                text-align: left;
            }}
            QPushButton:hover {{
                border-color: #FFFFFF;
                background-color: {t["SURFACE"]};
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
        """)
        self.lbl_id.setStyleSheet(f"color: {t['TEXT']};")
        self.lbl_status.setStyleSheet(f"color: {border_color};")
        self.lbl_content.setStyleSheet(f"color: {t['TEXT_MUTED']};")


class CellGridView(QWidget):
    """
    Vista de cuadrícula de celdas que actualiza sus tarjetas EN SITIO.
    No destruye ni reconstruye widgets en cada refresco (evita parpadeo).
    """
    cell_selected = Signal(str)

    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self.theme_name = theme_name
        self.tiles: Dict[str, CellTile] = {}

        self.grid_layout = QGridLayout(self)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        self.grid_layout.setSpacing(12)
        self.setStyleSheet("background: transparent;")

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        for tile in self.tiles.values():
            tile.set_theme(theme_name)

    def update_cells(self, status_dict: Dict[str, Dict], car_x: float = 0.0, car_y: float = 0.0):
        """
        Actualiza el estado de las celdas existentes o añade nuevas si cambiaron.
        Compara la posición del carro dentro de un umbral de ±5 mm.
        """
        row_map = {}
        for cid in status_dict:
            r_char = cid[0]
            if r_char not in row_map:
                row_map[r_char] = len(row_map)

        # 1. Eliminar celdas que ya no existen
        current_ids = set(status_dict.keys())
        for cid in list(self.tiles.keys()):
            if cid not in current_ids:
                tile = self.tiles.pop(cid)
                self.grid_layout.removeWidget(tile)
                tile.deleteLater()

        # 2. Actualizar o crear cada celda
        for cid, info in status_dict.items():
            st = info.get("status", "empty")
            content = info.get("content") or ""

            # Extraer coordenadas
            cx = info.get("x")
            cy = info.get("y")
            if cx is None and "coordinates" in info and isinstance(info["coordinates"], dict):
                cx = info["coordinates"].get("x")
                cy = info["coordinates"].get("y")

            has_pos = (cx is not None and cy is not None)
            is_car_here = False
            if has_pos:
                try:
                    is_car_here = (abs(float(cx) - car_x) <= 5.0 and abs(float(cy) - car_y) <= 5.0)
                except (ValueError, TypeError):
                    is_car_here = False

            if cid in self.tiles:
                self.tiles[cid].update_cell(status=st, content=content, has_position=has_pos, is_car_here=is_car_here)
            else:
                tile = CellTile(cid, theme_name=self.theme_name)
                tile.update_cell(status=st, content=content, has_position=has_pos, is_car_here=is_car_here)
                tile.cell_clicked.connect(self.cell_selected.emit)

                r_char = cid[0]
                try:
                    c_num = int(cid[1:]) - 1
                except ValueError:
                    c_num = 0
                r_idx = row_map.get(r_char, 0)

                self.tiles[cid] = tile
                self.grid_layout.addWidget(tile, r_idx, c_num)


# ═══════════════════════════════════════════════════════════════════════
# 7. WAREHOUSE 2D CANVAS (Plano Vectorial con Tokens ISA-101)
# ═══════════════════════════════════════════════════════════════════════

class Warehouse2DCanvas(QWidget):
    """
    Dibuja el área de trabajo en 2D (X-Y) según los límites $130 / $131,
    las celdas configuradas y el puntero en tiempo real de la posición GRBL.
    """

    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self.theme_name = theme_name
        self.max_x = 360.0
        self.max_y = 290.0
        self.cur_x = 0.0
        self.cur_y = 0.0
        self.cells_data: Dict[str, Dict] = {}
        self.setMinimumSize(280, 240)

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self.update()

    def set_limits(self, max_x: float, max_y: float):
        self.max_x = max(100.0, max_x)
        self.max_y = max(100.0, max_y)
        self.update()

    def set_position(self, x: float, y: float):
        self.cur_x = x
        self.cur_y = y
        self.update()

    def set_cells(self, cells: Dict[str, Dict]):
        self.cells_data = cells
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        t = theme.get_tokens(self.theme_name)
        margin = 35
        w = self.width() - (2 * margin)
        h = self.height() - (2 * margin)

        if w <= 0 or h <= 0:
            return

        scale_x = w / self.max_x
        scale_y = h / self.max_y
        scale = min(scale_x, scale_y)

        draw_w = self.max_x * scale
        draw_h = self.max_y * scale
        ox = margin + (w - draw_w) / 2
        oy = margin + (h - draw_h) / 2

        # 1. Marco del Área de Trabajo
        painter.setPen(QPen(QColor(t["BORDER"]), 1.5))
        painter.setBrush(QBrush(QColor(t["SURFACE_ALT"])))
        painter.drawRect(QRectF(ox, oy, draw_w, draw_h))

        # 2. Celdas del Almacén
        cell_w_real = 40.0
        cell_h_real = 40.0
        for cid, cinfo in self.cells_data.items():
            try:
                cell_x = cinfo.get("x")
                if cell_x is None and "coordinates" in cinfo and isinstance(cinfo["coordinates"], dict):
                    cell_x = cinfo["coordinates"].get("x")
                cell_x = float(cell_x or 0.0)

                cell_y = cinfo.get("y")
                if cell_y is None and "coordinates" in cinfo and isinstance(cinfo["coordinates"], dict):
                    cell_y = cinfo["coordinates"].get("y")
                cell_y = float(cell_y or 0.0)

                cx = ox + (cell_x * scale) - (cell_w_real * scale * 0.5)
                cy = (oy + draw_h) - (cell_y * scale) - (cell_h_real * scale * 0.5)

                status = cinfo.get("status", "empty")
                if status == "occupied":
                    border_color = QColor(t["ACCENT"])
                elif status == "reserved":
                    border_color = QColor(t["WARN"])
                else:
                    border_color = QColor(t["OK"])

                bg_c = QColor(border_color)
                bg_c.setAlpha(40)

                painter.setPen(QPen(border_color, 1.5))
                painter.setBrush(QBrush(bg_c))
                rect = QRectF(cx, cy, cell_w_real * scale, cell_h_real * scale)
                painter.drawRoundedRect(rect, 4, 4)

                painter.setPen(QColor(t["TEXT"]))
                painter.setFont(QFont("JetBrains Mono", 8, QFont.Bold))
                painter.drawText(rect, Qt.AlignCenter, cid)
            except Exception:
                pass

        # 3. Puntero Posición Actual
        px = ox + (min(max(0.0, self.cur_x), self.max_x) * scale)
        py = (oy + draw_h) - (min(max(0.0, self.cur_y), self.max_y) * scale)

        # Retícula sutil
        painter.setPen(QPen(QColor(t["BORDER"]), 1, Qt.DashLine))
        painter.drawLine(int(ox), int(py), int(ox + draw_w), int(py))
        painter.drawLine(int(px), int(oy), int(px), int(oy + draw_h))

        # Marcador circular
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.setBrush(QBrush(QColor(t["ACCENT"])))
        painter.drawEllipse(QPointF(px, py), 6, 6)

        # Leyenda de dimensiones
        painter.setPen(QColor(t["TEXT_MUTED"]))
        painter.setFont(QFont("JetBrains Mono", 8))
        painter.drawText(int(ox), int(oy - 8), f"X Max: {self.max_x:.0f}mm")
        painter.drawText(int(ox + draw_w - 75), int(oy + draw_h + 18), f"Y Max: {self.max_y:.0f}mm")


# ═══════════════════════════════════════════════════════════════════════
# 8. STEP PROGRESS BAR (Indicador de Pasos del Wizard)
# ═══════════════════════════════════════════════════════════════════════

class StepProgressBar(QWidget):
    """
    Indicador de progreso de pasos tipo breadcrumb para el asistente de calibración.
    Ej: [1 Selección] ➔ [2 Búsqueda] ➔ [3 Medición] ➔ [4 Resultado]
    """

    def __init__(self, steps: List[str], current_step: int = 0, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self.steps = steps
        self.current_step = current_step
        self.theme_name = theme_name

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(8, 4, 8, 4)
        self.layout.setSpacing(8)
        self.step_labels: List[QLabel] = []

        self._build_ui()

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self.set_current_step(self.current_step)

    def _build_ui(self):
        for i, title in enumerate(self.steps):
            lbl = QLabel(f"{i+1}. {title}")
            lbl.setFont(QFont("Inter", 10, QFont.Bold))
            lbl.setAlignment(Qt.AlignCenter)
            self.step_labels.append(lbl)
            self.layout.addWidget(lbl)

            if i < len(self.steps) - 1:
                arrow = QLabel("→")
                arrow.setFont(QFont("Inter", 11))
                self.layout.addWidget(arrow)

        self.set_current_step(self.current_step)

    def set_current_step(self, step_idx: int):
        self.current_step = step_idx
        t = theme.get_tokens(self.theme_name)

        for i, lbl in enumerate(self.step_labels):
            if i < self.current_step:
                # Completado
                lbl.setStyleSheet(f"color: {t['OK']}; background: {t['SURFACE_ALT']}; padding: 4px 8px; border-radius: 4px; border: 1px solid {t['OK']};")
            elif i == self.current_step:
                # Activo
                lbl.setStyleSheet(f"color: #FFFFFF; background: {t['ACCENT']}; padding: 4px 8px; border-radius: 4px; font-weight: 700;")
            else:
                # Pendiente
                lbl.setStyleSheet(f"color: {t['TEXT_MUTED']}; background: {t['SURFACE_ALT']}; padding: 4px 8px; border-radius: 4px; border: 1px solid {t['BORDER']};")


# ═══════════════════════════════════════════════════════════════════════
# 9. SENSOR INDICATOR & AUTO MODE CONTROL (Panel SCADA para Arduino)
# ═══════════════════════════════════════════════════════════════════════

class SensorIndicator(QFrame):
    """
    Panel de control y visualización del sensor de carga Arduino.
    Incluye:
    - Selector COM y conexión con Arduino
    - LED Virtual en pantalla de detección de objeto (estado en vivo)
    - Indicador de pulso de comunicación serie (heartbeat)
    - Conmutador y estado del MODO AUTOMATICO
    """
    connect_clicked = Signal(str)
    disconnect_clicked = Signal()
    auto_mode_toggled = Signal(bool)

    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self.theme_name = theme_name
        self.setProperty("panel", "true")
        self.is_connected = False
        self.is_detected = False
        self.is_auto_active = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # ── Título del Panel ──
        lbl_head = QLabel("MODO AUTOMATICO (SENSOR ARDUINO)")
        lbl_head.setFont(QFont("Inter", 11, QFont.Bold))
        layout.addWidget(lbl_head)

        # ── Fila 1: Conexión Serial con Arduino ──
        conn_lay = QHBoxLayout()
        conn_lay.setSpacing(8)

        lbl_port = QLabel("Puerto:")
        lbl_port.setFont(QFont("Inter", 10))
        conn_lay.addWidget(lbl_port)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setFixedWidth(85)
        # Auto-descubrir puertos COM
        try:
            import serial.tools.list_ports
            detected = [p.device for p in serial.tools.list_ports.comports()]
            if detected:
                self.port_combo.addItems(detected)
            else:
                self.port_combo.addItems(["COM4", "COM3", "COM5", "COM6", "COM7"])
        except Exception:
            self.port_combo.addItems(["COM4", "COM3", "COM5", "COM6", "COM7"])
        conn_lay.addWidget(self.port_combo)

        self.btn_connect = QPushButton("Conectar")
        self.btn_connect.setFixedHeight(32)
        self.btn_connect.setIcon(theme.get_icon("mdi6.usb", theme_name=theme_name))
        self.btn_connect.clicked.connect(self._handle_connect_toggle)
        conn_lay.addWidget(self.btn_connect)

        self.chip_conn = StateChip("Desconectado", "neutral", "mdi6.usb-port-off", theme_name=theme_name)
        conn_lay.addWidget(self.chip_conn)
        conn_lay.addStretch()

        layout.addLayout(conn_lay)

        # ── Fila 2: LED Virtual de Detección en Pantalla ──
        self.frame_led = QFrame()
        led_lay = QHBoxLayout(self.frame_led)
        led_lay.setContentsMargins(12, 8, 12, 8)
        led_lay.setSpacing(10)

        self.lbl_led_icon = QLabel()
        self.lbl_led_icon.setFixedSize(24, 24)
        led_lay.addWidget(self.lbl_led_icon)

        self.lbl_led_text = QLabel("SIN OBJETO EN SENSOR")
        self.lbl_led_text.setFont(QFont("Inter", 11, QFont.Bold))
        led_lay.addWidget(self.lbl_led_text, stretch=1)

        self.lbl_comms = QLabel("Comms: Inactivo")
        self.lbl_comms.setFont(QFont("JetBrains Mono", 9))
        led_lay.addWidget(self.lbl_comms)

        layout.addWidget(self.frame_led)

        # ── Fila 3: Botón y Estado de Modo Automático ──
        self.btn_auto_toggle = QPushButton("ACTIVAR MODO AUTOMATICO")
        self.btn_auto_toggle.setProperty("level", "primary")
        self.btn_auto_toggle.setFixedHeight(40)
        self.btn_auto_toggle.setIcon(theme.get_icon("mdi6.play-circle-outline", color="#FFFFFF", theme_name=theme_name))
        self.btn_auto_toggle.clicked.connect(self._handle_auto_toggle)
        layout.addWidget(self.btn_auto_toggle)

        self.lbl_auto_status = QLabel("Control manual activo. Presiona activar para esperar objetos automáticamente.")
        self.lbl_auto_status.setFont(QFont("Inter", 9))
        self.lbl_auto_status.setWordWrap(True)
        layout.addWidget(self.lbl_auto_status)

        self._apply_style()

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self.chip_conn.set_theme(theme_name)
        self._apply_style()

    def _apply_style(self):
        t = theme.get_tokens(self.theme_name)
        
        # Estilo del LED Virtual según estado de detección
        if not self.is_connected:
            led_bg = t["SURFACE_ALT"]
            led_border = t["BORDER"]
            text_color = t["TEXT_MUTED"]
            icon_color = t["NEUTRAL"]
            icon_name = "mdi6.checkbox-blank-circle-outline"
            text_str = "SENSOR ARDUINO DESCONECTADO"
            comms_str = "Comms: Sin conexión"
        elif self.is_detected:
            # ¡OBJETO DETECTADO! Color verde brillante con fondo resaltado
            led_bg = f"rgba(46, 204, 113, 0.20)" if self.theme_name == "dark" else "rgba(47, 130, 83, 0.15)"
            led_border = t["OK"]
            text_color = t["OK"]
            icon_color = t["OK"]
            icon_name = "mdi6.record-circle-outline"
            text_str = "¡OBJETO DETECTADO EN PLATAFORMA!"
            comms_str = "Comms: OK (Activo)"
        else:
            # Sensor conectado pero sin objeto
            led_bg = t["SURFACE_ALT"]
            led_border = t["BORDER"]
            text_color = t["TEXT"]
            icon_color = t["NEUTRAL"]
            icon_name = "mdi6.circle-outline"
            text_str = "PLATAFORMA LIBRE (Esperando caja...)"
            comms_str = "Comms: OK"

        self.frame_led.setStyleSheet(f"""
            QFrame {{
                background-color: {led_bg};
                border: 1px solid {led_border};
                border-radius: 6px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
        """)
        self.lbl_led_icon.setPixmap(theme.get_icon(icon_name, color=icon_color, theme_name=self.theme_name).pixmap(22, 22))
        self.lbl_led_text.setText(text_str)
        self.lbl_led_text.setStyleSheet(f"color: {text_color};")
        self.lbl_comms.setText(comms_str)
        self.lbl_comms.setStyleSheet(f"color: {t['TEXT_MUTED']};")

        # Estilo del botón de modo automático
        if self.is_auto_active:
            self.btn_auto_toggle.setText("DETENER MODO AUTOMATICO")
            self.btn_auto_toggle.setIcon(theme.get_icon("mdi6.stop-circle-outline", color="#FFFFFF", theme_name=self.theme_name))
            self.btn_auto_toggle.setStyleSheet(f"""
                QPushButton {{
                    background-color: {t["WARN"]};
                    color: #FFFFFF;
                    font-weight: 700;
                    border: none;
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background-color: #D4901C;
                }}
            """)
        else:
            self.btn_auto_toggle.setText("ACTIVAR MODO AUTOMATICO")
            self.btn_auto_toggle.setIcon(theme.get_icon("mdi6.play-circle-outline", color="#FFFFFF", theme_name=self.theme_name))
            self.btn_auto_toggle.setStyleSheet(f"""
                QPushButton {{
                    background-color: {t["ACCENT"]};
                    color: #FFFFFF;
                    font-weight: 700;
                    border: none;
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background-color: #2F6DA6;
                }}
            """)

    def set_connection_state(self, connected: bool, port: str = ""):
        self.is_connected = connected
        if connected:
            self.chip_conn.set_state(f"Conectado ({port})", "ok", "mdi6.usb-port")
            self.btn_connect.setText("Desconectar")
            self.btn_connect.setIcon(theme.get_icon("mdi6.usb-port-off", theme_name=self.theme_name))
        else:
            self.chip_conn.set_state("Desconectado", "neutral", "mdi6.usb-port-off")
            self.btn_connect.setText("Conectar")
            self.btn_connect.setIcon(theme.get_icon("mdi6.usb", theme_name=self.theme_name))
            self.set_detection_state(False)
            self.set_auto_mode_state(False)
        self._apply_style()

    def set_detection_state(self, detected: bool):
        """Actualiza el LED virtual según la presencia física de la caja."""
        self.is_detected = detected
        self._apply_style()

    def pulse_heartbeat(self):
        """Pulso visual breve que confirma comunicación activa."""
        t = theme.get_tokens(self.theme_name)
        self.lbl_comms.setText("Comms: OK ●")
        self.lbl_comms.setStyleSheet(f"color: {t['OK']};")

    def set_auto_mode_state(self, active: bool, msg: Optional[str] = None):
        self.is_auto_active = active
        if active:
            txt = msg or "MODO AUTOMATICO ACTIVO: Coloca una caja en las palas para iniciar ciclo."
        else:
            txt = msg or "Control manual activo. Presiona activar para esperar objetos automáticamente."
        self.lbl_auto_status.setText(txt)
        self._apply_style()

    def _handle_connect_toggle(self):
        if self.is_connected:
            self.disconnect_clicked.emit()
        else:
            port = self.port_combo.currentText().strip()
            self.connect_clicked.emit(port)

    def _handle_auto_toggle(self):
        self.auto_mode_toggled.emit(not self.is_auto_active)


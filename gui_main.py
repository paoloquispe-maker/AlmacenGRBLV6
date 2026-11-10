"""
Aplicación HMI/SCADA de Escritorio PySide6 para Almacén Automático AS/RS con GRBL.
Norma de Diseño Industrial ISA-101 (High-Performance HMI).
Estructura en 4 Pestañas:
1. Operación (Malla interactiva, Ingreso/Retiro guiado, KPIs, Auditoría reciente)
2. Historial (Consultas de auditoría append-only, filtros, exportación CSV)
3. Mantenimiento (Jog seguro por teclado/botones, Calibración con Wizard, Canvas 2D, Captura)
4. Configuración (Dimensionamiento de grid, edición manual, tema Claro/Oscuro, Zona de peligro)
"""

import sys
import os
import time
import logging
from typing import Dict, List, Optional, Callable, Any

from PySide6.QtCore import (
    Qt, QObject, QThread, QAbstractTableModel, QModelIndex, QRectF, QPointF, Signal, Slot,
    QThreadPool, QRunnable, QSettings, QEvent, QMetaObject
)
from PySide6.QtGui import (
    QColor, QFont, QKeySequence, QShortcut
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTableView,
    QHeaderView, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QFormLayout,
    QGroupBox, QMessageBox, QFileDialog, QStackedWidget, QListWidget, QProgressBar,
    QSplitter, QFrame, QDialog, QDialogButtonBox, QCheckBox, QRadioButton, QScrollArea,
    QSizePolicy
)

import theme
from theme import FONT_FAMILY_UI, FONT_FAMILY_MONO
from widgets import (
    StateChip, StatusBar, EmergencyBar, AlarmBanner, KpiCard,
    CellTile, CellGridView, Warehouse2DCanvas, StepProgressBar,
    SensorIndicator
)
from service_layer import WarehouseService
from history_db import HistoryDB
from grid_config import (
    load_config, save_config, generate_grid_cells, validate_coords_against_grbl,
    save_manual_cell_pos, create_config_backup
)
from workers import PositionPoller, JogWorker, CalibrationWorker, ArduinoSensorWorker, GoPosWorker  # CAMBIO: añadido GoPosWorker
from axis_calibrator import AxisCalibrator

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# TAREA ASÍNCRONA DE MOVIMIENTO A CELDA (QRunnable)
# ═══════════════════════════════════════════════

class MoveTask(QRunnable):
    """Ejecuta el movimiento de transporte seguro a una celda en QThreadPool (evita QThread.create)."""

    def __init__(self, service: WarehouseService, cell_id: str, on_finished: Optional[Callable[[bool, str], None]] = None):
        super().__init__()
        self.service = service
        self.cell_id = cell_id
        self.on_finished = on_finished

    def run(self):
        ok = self.service.move_to_cell(self.cell_id)
        if self.on_finished:
            self.on_finished(ok, self.cell_id)


# CAMBIO: Task para la secuencia completa de retiro físico con regreso a posición de carga.
# Usa service.recoger_objeto_de_celda() que incluye:
#   ir a celda → extender palas → asegurar carga → retraer palas → regresar a X0 Y0
class RecogerTask(QRunnable):
    """
    Ejecuta la secuencia cinemática completa de recogida en QThreadPool:
    Elevar → Trasladar → Extender palas → Elevar con carga → Retraer palas → Regresar a origen.
    """

    def __init__(self, service: WarehouseService, cell_id: str, on_finished: Optional[Callable[[bool, str], None]] = None):
        super().__init__()
        self.service = service
        self.cell_id = cell_id
        self.on_finished = on_finished

    def run(self):
        ok = self.service.recoger_objeto_de_celda(self.cell_id)
        if self.on_finished:
            self.on_finished(ok, self.cell_id)


class AutoDepositSignals(QObject):
    step_changed = Signal(int, str)
    finished = Signal(bool, str, str)


class AutoDepositTask(QRunnable):
    """
    Ejecuta el ciclo cinemático completo de depósito automático en segundo plano.
    Elevar -> Trasladar a celda -> Extender palas -> Posar -> Retraer -> Retornar a carga.
    """
    def __init__(self, service: WarehouseService, cell_id: str, item_code: str):
        super().__init__()
        self.service = service
        self.cell_id = cell_id
        self.item_code = item_code
        self.signals = AutoDepositSignals()

    def run(self):
        def on_step(step_idx, step_desc):
            self.signals.step_changed.emit(step_idx, step_desc)

        ok = self.service.depositar_objeto_en_celda(self.cell_id, on_step=on_step)
        if ok:
            self.service.registrar_entrada(
                cell_id=self.cell_id,
                item_code=self.item_code,
                origen="AUTOMATICO",
                notas=f"Almacenamiento automático vía sensor en celda {self.cell_id}"
            )
        self.signals.finished.emit(ok, self.cell_id, self.item_code)


# ═══════════════════════════════════════════════
# MODELO TABLA HISTORIAL (QAbstractTableModel)
# ═══════════════════════════════════════════════

class HistoryTableModel(QAbstractTableModel):
    """Modelo de solo lectura para visualizar registros de auditoría SQLite."""

    HEADERS = ["ID", "Fecha/Hora", "Celda", "Tipo", "Objeto", "Origen", "Notas"]

    def __init__(self, data: Optional[List[Dict]] = None):
        super().__init__()
        self._data = data or []

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._data)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None

        row_data = self._data[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return str(row_data.get("movement_id", ""))
            elif col == 1:
                return str(row_data.get("timestamp", ""))
            elif col == 2:
                return str(row_data.get("cell_id", ""))
            elif col == 3:
                return str(row_data.get("movement_type", ""))
            elif col == 4:
                return str(row_data.get("item_code", "")) or "-"
            elif col == 5:
                return str(row_data.get("origen", ""))
            elif col == 6:
                return str(row_data.get("notas", "")) or "-"

        elif role == Qt.TextAlignmentRole:
            if col in (0, 1, 2, 3, 5):
                return int(Qt.AlignCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        elif role == Qt.FontRole:
            if col in (0, 1, 2):
                return QFont("JetBrains Mono", 10)

        return None

    def set_data(self, data: List[Dict]):
        self.beginResetModel()
        self._data = data
        self.endResetModel()


# ═══════════════════════════════════════════════
# BOTÓN JOG CONTINUO (Press & Hold)
# ═══════════════════════════════════════════════

class JogButton(QPushButton):
    """Botón que emite señales pressed_jog y released_jog al presionar/soltar."""
    pressed_jog = Signal(str, int)
    released_jog = Signal()

    def __init__(self, text: str, axis: str, direction: int, parent=None):
        super().__init__(text, parent)
        self.axis = axis
        self.direction = direction
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.pressed_jog.emit(self.axis, self.direction)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.released_jog.emit()
        super().mouseReleaseEvent(event)


# ═══════════════════════════════════════════════
# VENTANA PRINCIPAL HMI/SCADA
# ═══════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sistema de Almacén Automático AS/RS — HMI de Control")
        self.resize(1200, 800)
        self.setMinimumSize(1000, 680)

        # Configuración persistente (QSettings)
        self.settings = QSettings("TECSUP", "AlmacenASRS")
        self.current_theme = self.settings.value("theme", "dark")

        # Variables de estado del sistema
        self.origin_confirmed = False
        self.machine_state_str = "Desconectado"
        self.maint_mode_unlocked = False
        self.selected_cell_id: Optional[str] = None
        self.calib_worker_thread: Optional[QThread] = None
        self.wiz_calibrator_instance: Optional[AxisCalibrator] = None

        # Variables de Modo Automático y Sensor Arduino
        self.auto_mode_active = False
        self.is_auto_cycle_running = False
        self.auto_stored_count = 0
        self.sensor_thread: Optional[QThread] = None
        self.sensor_worker: Optional[ArduinoSensorWorker] = None
        # CAMBIO: hilo y worker para la secuencia Ir a Posición de Carga
        self.go_pos_thread: Optional[QThread] = None
        self.go_pos_worker: Optional[GoPosWorker] = None

        # Capa de servicio
        self.service = WarehouseService()

        # Marco central y Layout Principal
        main_container = QWidget()
        self.setCentralWidget(main_container)
        self.main_layout = QVBoxLayout(main_container)
        self.main_layout.setContentsMargins(16, 12, 16, 12)
        self.main_layout.setSpacing(10)

        # 1. Barra de Estado Superior (Marco Fijo)
        self.status_bar = StatusBar(theme_name=self.current_theme)
        self.status_bar.connect_clicked.connect(self._handle_connect)
        self.status_bar.disconnect_clicked.connect(self._handle_disconnect)
        self.status_bar.emergency_bar.pause_clicked.connect(self._send_feed_hold)
        self.status_bar.emergency_bar.resume_clicked.connect(self._send_cycle_start)
        self.status_bar.emergency_bar.emergency_stop_clicked.connect(self._send_emergency_stop)
        self.main_layout.addWidget(self.status_bar)

        # 2. Alarm Banner (Prioritario, oculto por defecto)
        self.alarm_banner = AlarmBanner(theme_name=self.current_theme)
        self.main_layout.addWidget(self.alarm_banner)

        # 3. Pestañas Principales
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs, stretch=1)

        # Construcción de las 4 pestañas
        self._build_operation_tab()
        self._build_history_tab()
        self._build_maintenance_tab()
        self._build_config_tab()

        # Atajos de navegación entre pestañas (Ctrl+1...4)
        self._setup_tab_shortcuts()

        # Evento de cambio de pestaña (para seguridad en mantenimiento)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Workers Qt en hilos dedicados (moveToThread)
        self._setup_workers()

        # Aplicar tema global inicial
        self._apply_global_theme()

        # Cargar datos iniciales
        self.refresh_dashboard()

        # Actualizar habilitación centralizada
        self.apply_state(self.machine_state_str)

    # ═══════════════════════════════════════════════
    # GESTIÓN DE TEMA Y ESTILOS
    # ═══════════════════════════════════════════════

    def _apply_global_theme(self):
        """Aplica la hoja de estilo QSS generada por theme.py a la aplicación completa."""
        qss = theme.build_qss(self.current_theme)
        self.setStyleSheet(qss)

        # Notificar a componentes custom
        self.status_bar.set_theme(self.current_theme)
        self.alarm_banner.set_theme(self.current_theme)
        self.cell_grid.set_theme(self.current_theme)
        self.canvas_2d.set_theme(self.current_theme)
        self.kpi_occ.set_theme(self.current_theme)
        self.kpi_mov.set_theme(self.current_theme)
        if hasattr(self, 'sensor_panel'):
            self.sensor_panel.set_theme(self.current_theme)
        if hasattr(self, 'wiz_progress'):
            self.wiz_progress.set_theme(self.current_theme)

    def _on_theme_changed(self, new_theme: str):
        self.current_theme = new_theme.lower()
        self.settings.setValue("theme", self.current_theme)
        self._apply_global_theme()

    # ═══════════════════════════════════════════════
    # ATAJOS DE TECLADO Y NAVEGACIÓN
    # ═══════════════════════════════════════════════

    def _setup_tab_shortcuts(self):
        for idx, key in enumerate([Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4]):
            sc = QShortcut(QKeySequence(f"Ctrl+{idx+1}"), self)
            sc.activated.connect(lambda i=idx: self.tabs.setCurrentIndex(i))

    def _on_tab_changed(self, index: int):
        # Si se sale de la pestaña de mantenimiento, bloquear candado y desconfirmar origen
        if index != 2 and self.maint_mode_unlocked:
            self.maint_mode_unlocked = False
            self.origin_confirmed = False
            self.status_bar.set_origin_confirmed(False)
            self._update_maint_lock_ui()
            logger.info("Salida de modo mantenimiento: origen marcado como no confirmado por seguridad.")
            self.apply_state(self.machine_state_str)

    # ═══════════════════════════════════════════════
    # PESTAÑA 1: OPERACIÓN (Dashboard Principal)
    # ═══════════════════════════════════════════════

    def _build_operation_tab(self):
        self.tab_operation = QWidget()
        layout = QHBoxLayout(self.tab_operation)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)

        # ── Zona Izquierda (60%): Malla de Celdas y Detalle Lateral ──
        left_box = QFrame()
        left_box.setProperty("panel", "true")
        left_lay = QVBoxLayout(left_box)
        left_lay.setContentsMargins(12, 12, 12, 12)

        lbl_grid_title = QLabel("PLANO DEL ALMACEN")
        lbl_grid_title.setFont(QFont("Inter", 12, QFont.Bold))
        left_lay.addWidget(lbl_grid_title)

        grid_splitter = QSplitter(Qt.Horizontal)

        # Malla de Celdas
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")
        if scroll.viewport():
            scroll.viewport().setStyleSheet("background: transparent;")
        self.cell_grid = CellGridView(theme_name=self.current_theme)
        self.cell_grid.cell_selected.connect(self._on_cell_selected)
        scroll.setWidget(self.cell_grid)
        grid_splitter.addWidget(scroll)

        # Panel Lateral de Detalle de Celda
        self.panel_cell_detail = QFrame()
        self.panel_cell_detail.setProperty("panel", "true")
        self.panel_cell_detail.setFixedWidth(280)
        p_lay = QVBoxLayout(self.panel_cell_detail)
        p_lay.setContentsMargins(14, 14, 14, 14)
        p_lay.setSpacing(10)

        lbl_detail_head = QLabel("DETALLE DE CELDA")
        lbl_detail_head.setFont(QFont("Inter", 11, QFont.Bold))
        p_lay.addWidget(lbl_detail_head)

        self.lbl_det_cell_id = QLabel("Seleccione una celda")
        self.lbl_det_cell_id.setFont(QFont("JetBrains Mono", 14, QFont.Bold))
        p_lay.addWidget(self.lbl_det_cell_id)

        self.chip_det_status = StateChip("Vacía", "ok", "mdi6.checkbox-blank-circle-outline", theme_name=self.current_theme)
        p_lay.addWidget(self.chip_det_status)

        self.lbl_det_content = QLabel("Contenido: -")
        self.lbl_det_content.setWordWrap(True)
        p_lay.addWidget(self.lbl_det_content)

        self.lbl_det_coords = QLabel("Coordenadas: -")
        self.lbl_det_coords.setFont(QFont("JetBrains Mono", 10))
        p_lay.addWidget(self.lbl_det_coords)

        p_lay.addSpacing(10)

        # Acciones de la celda seleccionada
        self.btn_det_action1 = QPushButton("Registrar Entrada")
        self.btn_det_action1.clicked.connect(self._panel_action_entrada)
        p_lay.addWidget(self.btn_det_action1)

        self.btn_det_action2 = QPushButton("Registrar Salida")
        self.btn_det_action2.clicked.connect(self._panel_action_salida)
        p_lay.addWidget(self.btn_det_action2)

        self.btn_det_reserve = QPushButton("Marcar Reservada")
        self.btn_det_reserve.clicked.connect(self._panel_action_reserva)
        p_lay.addWidget(self.btn_det_reserve)

        self.btn_det_move = QPushButton("Mover Carro a esta Celda")
        self.btn_det_move.setIcon(theme.get_icon("mdi6.arrow-decision-auto", theme_name=self.current_theme))
        self.btn_det_move.clicked.connect(self._panel_action_mover)
        p_lay.addWidget(self.btn_det_move)

        p_lay.addStretch()
        grid_splitter.addWidget(self.panel_cell_detail)
        grid_splitter.setStretchFactor(0, 4)
        grid_splitter.setStretchFactor(1, 2)

        left_lay.addWidget(grid_splitter)
        layout.addWidget(left_box, stretch=6)

        # ── Zona Derecha (40%): Operación Rápida, KPIs y Movimientos ──
        right_lay = QVBoxLayout()
        right_lay.setSpacing(16)

        # Botones Principales de Operación (Ingresar / Retirar)
        op_box = QFrame()
        op_box.setProperty("panel", "true")
        op_lay = QVBoxLayout(op_box)
        op_lay.setContentsMargins(14, 14, 14, 14)
        op_lay.setSpacing(10)

        lbl_op_title = QLabel("OPERACIONES RAPIDAS")
        lbl_op_title.setFont(QFont("Inter", 11, QFont.Bold))
        op_lay.addWidget(lbl_op_title)

        btn_box = QHBoxLayout()
        self.btn_ingresar_item = QPushButton("Ingresar Item")
        self.btn_ingresar_item.setProperty("level", "primary")
        self.btn_ingresar_item.setFixedHeight(48)
        self.btn_ingresar_item.setIcon(theme.get_icon("mdi6.package-down", color="#FFFFFF", theme_name=self.current_theme))
        self.btn_ingresar_item.clicked.connect(self._flow_ingresar_item)
        btn_box.addWidget(self.btn_ingresar_item)

        self.btn_retirar_item = QPushButton("Retirar Item")
        self.btn_retirar_item.setFixedHeight(48)
        self.btn_retirar_item.setIcon(theme.get_icon("mdi6.package-up", theme_name=self.current_theme))
        self.btn_retirar_item.clicked.connect(self._flow_retirar_item)
        btn_box.addWidget(self.btn_retirar_item)

        op_lay.addLayout(btn_box)
        right_lay.addWidget(op_box)

        # Panel Sensor de Carga y Modo Automático (Arduino)
        self.sensor_panel = SensorIndicator(theme_name=self.current_theme)
        self.sensor_panel.connect_clicked.connect(self._handle_sensor_connect)
        self.sensor_panel.disconnect_clicked.connect(self._handle_sensor_disconnect)
        self.sensor_panel.auto_mode_toggled.connect(self._toggle_auto_mode)
        right_lay.addWidget(self.sensor_panel)

        # Indicadores KPIs
        kpi_lay = QHBoxLayout()
        self.kpi_occ = KpiCard("Ocupacion", "0.0%", theme_name=self.current_theme)
        self.kpi_mov = KpiCard("Movimientos Hoy", "0", theme_name=self.current_theme)
        kpi_lay.addWidget(self.kpi_occ)
        kpi_lay.addWidget(self.kpi_mov)
        right_lay.addLayout(kpi_lay)

        # Tabla de Últimos 7 Movimientos
        rec_box = QFrame()
        rec_box.setProperty("panel", "true")
        rec_lay = QVBoxLayout(rec_box)
        rec_lay.setContentsMargins(14, 14, 14, 14)

        lbl_rec_title = QLabel("ULTIMOS MOVIMIENTOS")
        lbl_rec_title.setFont(QFont("Inter", 11, QFont.Bold))
        rec_lay.addWidget(lbl_rec_title)

        self.table_recent = QTableWidget(0, 4)
        self.table_recent.setHorizontalHeaderLabels(["Hora", "Celda", "Tipo", "Objeto"])
        self.table_recent.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_recent.verticalHeader().setVisible(False)
        self.table_recent.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_recent.setSelectionBehavior(QTableWidget.SelectRows)
        rec_lay.addWidget(self.table_recent)

        right_lay.addWidget(rec_box, stretch=1)
        layout.addLayout(right_lay, stretch=4)

        self.tabs.addTab(self.tab_operation, "Operacion")

    def _on_cell_selected(self, cell_id: str):
        """Muestra los datos de la celda seleccionada en el panel lateral."""
        self.selected_cell_id = cell_id
        status_dict = self.service.get_cell_status()
        info = status_dict.get(cell_id, {})
        st = info.get("status", "empty")
        content = info.get("content") or "-"

        cx = info.get("x")
        cy = info.get("y")
        if cx is None and "coordinates" in info and isinstance(info["coordinates"], dict):
            cx = info["coordinates"].get("x")
            cy = info["coordinates"].get("y")

        coords_str = f"X: {cx} mm, Y: {cy} mm" if cx is not None else "Sin coordenadas"

        self.lbl_det_cell_id.setText(f"Celda {cell_id}")
        self.lbl_det_content.setText(f"Contenido: {content}")
        self.lbl_det_coords.setText(coords_str)

        if st == "occupied":
            self.chip_det_status.set_state("Ocupada", "accent", "mdi6.package-variant")
            self.btn_det_action1.setEnabled(False)
            self.btn_det_action2.setEnabled(True)
            self.btn_det_reserve.setEnabled(False)
        elif st == "reserved":
            self.chip_det_status.set_state("Reservada", "warn", "mdi6.clock-outline")
            self.btn_det_action1.setEnabled(True)
            self.btn_det_action2.setEnabled(False)
            self.btn_det_reserve.setEnabled(False)
        else:
            self.chip_det_status.set_state("Vacía", "ok", "mdi6.checkbox-blank-circle-outline")
            self.btn_det_action1.setEnabled(True)
            self.btn_det_action2.setEnabled(False)
            self.btn_det_reserve.setEnabled(True)

        can_move = (self.service.is_connected and self.origin_confirmed and self.machine_state_str == "Listo")
        self.btn_det_move.setEnabled(can_move)

    def _panel_action_entrada(self):
        if not self.selected_cell_id:
            return
        self._flow_ingresar_item(preselected_cell=self.selected_cell_id)

    def _panel_action_salida(self):
        if not self.selected_cell_id:
            return
        self._flow_retirar_item(preselected_cell=self.selected_cell_id)

    def _panel_action_reserva(self):
        if not self.selected_cell_id:
            return
        self.service.marcar_reservada(self.selected_cell_id)
        self.refresh_dashboard()
        self._on_cell_selected(self.selected_cell_id)

    def _panel_action_mover(self):
        if not self.selected_cell_id:
            return
        self._move_to_cell_bg(self.selected_cell_id)

    # ── FLUJOS RÁPIDOS EN ≤ 3 CLICS ──

    def _flow_ingresar_item(self, preselected_cell: Optional[str] = None):
        """Flujo guiado para ingresar un ítem proponiendo la primera celda vacía."""
        first_empty = preselected_cell or self.service.get_first_empty_cell()
        if not first_empty:
            QMessageBox.warning(self, "Almacén Lleno", "No existen celdas vacías disponibles para ingreso.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Ingresar Ítem al Almacén")
        dialog.setFixedWidth(360)
        d_lay = QVBoxLayout(dialog)

        d_lay.addWidget(QLabel("<b>Código o nombre del ítem:</b>"))
        txt_code = QLineEdit()
        txt_code.setPlaceholderText("Ej: SKU-1048, Caja A...")
        d_lay.addWidget(txt_code)

        d_lay.addWidget(QLabel("<b>Celda de destino:</b>"))
        cbo_cell = QComboBox()
        status_dict = self.service.get_cell_status()
        empty_cells = [cid for cid, info in sorted(status_dict.items()) if info.get("status") == "empty"]
        cbo_cell.addItems(empty_cells)
        if first_empty in empty_cells:
            cbo_cell.setCurrentText(first_empty)
        d_lay.addWidget(cbo_cell)

        chk_move = QCheckBox("Mover carro automáticamente a la celda tras registro")
        can_move = (self.service.is_connected and self.origin_confirmed)
        chk_move.setChecked(can_move)
        chk_move.setEnabled(can_move)
        d_lay.addWidget(chk_move)

        d_lay.addSpacing(10)
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.button(QDialogButtonBox.Ok).setText("Confirmar Ingreso")
        bbox.button(QDialogButtonBox.Ok).setProperty("level", "primary")
        bbox.accepted.connect(dialog.accept)
        bbox.rejected.connect(dialog.reject)
        d_lay.addWidget(bbox)

        txt_code.setFocus()

        if dialog.exec() == QDialog.Accepted:
            item_code = txt_code.text().strip() or "Ítem"
            dest_cell = cbo_cell.currentText()
            self.service.registrar_entrada(dest_cell, item_code)
            self.refresh_dashboard()
            self._on_cell_selected(dest_cell)

            if chk_move.isChecked():
                self._move_to_cell_bg(dest_cell)

    def _flow_retirar_item(self, preselected_cell: Optional[str] = None):
        """Flujo guiado para retirar un ítem de una celda ocupada."""
        status_dict = self.service.get_cell_status()
        occupied_cells = [cid for cid, info in sorted(status_dict.items()) if info.get("status") == "occupied"]

        if not occupied_cells:
            QMessageBox.information(self, "Almacén Vacío", "No hay celdas ocupadas para retirar.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Retirar Ítem del Almacén")
        dialog.setFixedWidth(360)
        d_lay = QVBoxLayout(dialog)

        d_lay.addWidget(QLabel("<b>Seleccione celda a retirar:</b>"))
        cbo_cell = QComboBox()
        for cid in occupied_cells:
            content = status_dict[cid].get("content", "")
            cbo_cell.addItem(f"{cid} — {content}", cid)

        if preselected_cell:
            for i in range(cbo_cell.count()):
                if cbo_cell.itemData(i) == preselected_cell:
                    cbo_cell.setCurrentIndex(i)
                    break
        d_lay.addWidget(cbo_cell)

        chk_move = QCheckBox("Mover carro a la celda para retiro físico (regresa automáticamente)")  # CAMBIO: texto aclarado
        can_move = (self.service.is_connected and self.origin_confirmed)
        chk_move.setChecked(can_move)
        chk_move.setEnabled(can_move)
        d_lay.addWidget(chk_move)

        d_lay.addSpacing(10)
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.button(QDialogButtonBox.Ok).setText("Confirmar Retiro")
        bbox.button(QDialogButtonBox.Ok).setProperty("level", "primary")
        bbox.accepted.connect(dialog.accept)
        bbox.rejected.connect(dialog.reject)
        d_lay.addWidget(bbox)

        if dialog.exec() == QDialog.Accepted:
            target_cell = cbo_cell.currentData()
            self.service.registrar_salida(target_cell)
            self.refresh_dashboard()
            self._on_cell_selected(target_cell)

            if chk_move.isChecked():
                # CAMBIO: usar RecogerTask en vez de _move_to_cell_bg.
                # recoger_objeto_de_celda() ejecuta la secuencia completa:
                #   elevar → ir a celda → extender palas → asegurar carga → retraer → REGRESAR a X0 Y0
                self._recoger_item_bg(target_cell)

    def refresh_dashboard(self):
        """Actualiza la malla en sitio, KPIs y tabla de últimos movimientos."""
        status_dict = self.service.get_cell_status()

        # Posición actual del carro
        car_x = self.canvas_2d.cur_x
        car_y = self.canvas_2d.cur_y

        # 1. Actualizar malla en sitio
        self.cell_grid.update_cells(status_dict, car_x, car_y)

        # 2. Actualizar Canvas 2D
        self.canvas_2d.set_cells(status_dict)

        # 3. Actualizar KPIs
        total_cells = len(status_dict)
        occupied_cnt = sum(1 for v in status_dict.values() if v.get("status") == "occupied")
        pct = (occupied_cnt / total_cells * 100.0) if total_cells > 0 else 0.0
        self.kpi_occ.set_value(f"{pct:.1f}%")

        daily_cnt = self.service.history_db.get_daily_movement_count()
        self.kpi_mov.set_value(str(daily_cnt))

        # Alarma de almacén lleno
        if total_cells > 0 and occupied_cnt >= total_cells:
            self.alarm_banner.mostrar("almacen_lleno", "Capacidad completa: todas las celdas están ocupadas.")
            self.btn_ingresar_item.setEnabled(False)
            self.btn_ingresar_item.setToolTip("Almacén lleno: no quedan celdas vacías")
        else:
            self.alarm_banner.ocultar("almacen_lleno")
            self.btn_ingresar_item.setToolTip("")

        # 4. Actualizar tabla recientes (7 movimientos)
        recents = self.service.history_db.get_movements(limit=7)
        self.table_recent.setRowCount(len(recents))
        for row, rdata in enumerate(recents):
            # Solo hora
            ts = rdata.get("timestamp", "")
            hora = ts.split(" ")[1] if " " in ts else ts
            self.table_recent.setItem(row, 0, QTableWidgetItem(hora))
            self.table_recent.setItem(row, 1, QTableWidgetItem(rdata.get("cell_id", "")))
            self.table_recent.setItem(row, 2, QTableWidgetItem(rdata.get("movement_type", "")))
            self.table_recent.setItem(row, 3, QTableWidgetItem(rdata.get("item_code", "") or "-"))

        # Actualizar panel lateral si hay celda seleccionada
        if self.selected_cell_id and self.selected_cell_id in status_dict:
            self._on_cell_selected(self.selected_cell_id)

    def _move_to_cell_bg(self, cell_id: str):
        """Ejecuta el movimiento a una celda en segundo plano vía QThreadPool (sin QThread.create)."""
        if not self.service.is_connected:
            QMessageBox.critical(self, "Sin Conexión", "Conecta con GRBL antes de mover los ejes.")
            return
        if not self.origin_confirmed:
            QMessageBox.warning(self, "Origen No Confirmado", "Referencia primero los ejes ($H o G92) antes de mover.")
            return

        def on_done(ok: bool, cid: str):
            logger.info(f"Movimiento a celda {cid}: {'completado' if ok else 'falló'}")

        task = MoveTask(self.service, cell_id, on_done)
        QThreadPool.globalInstance().start(task)

    # CAMBIO: método equivalente a _move_to_cell_bg pero para retiro completo con regreso.
    # Usa RecogerTask → service.recoger_objeto_de_celda() que incluye el retorno a X0 Y0.
    def _recoger_item_bg(self, cell_id: str):
        """
        Ejecuta la secuencia cinemática completa de retiro en segundo plano:
        elevar → ir a celda → extender palas → asegurar carga → retraer → regresar a X0 Y0.
        """
        if not self.service.is_connected:
            QMessageBox.critical(self, "Sin Conexión", "Conecta con GRBL antes de mover los ejes.")
            return
        if not self.origin_confirmed:
            QMessageBox.warning(self, "Origen No Confirmado", "Referencia primero los ejes ($H o G92) antes de mover.")
            return

        def on_done(ok: bool, cid: str):
            if ok:
                logger.info(f"Retiro completo de celda {cid}: carro regresó a posición de carga.")
            else:
                logger.error(f"Retiro de celda {cid} falló durante la secuencia cinemática.")

        task = RecogerTask(self.service, cell_id, on_done)
        QThreadPool.globalInstance().start(task)

    # ═══════════════════════════════════════════════
    # PESTAÑA 2: HISTORIAL (Auditoría Inmutable)
    # ═══════════════════════════════════════════════

    def _build_history_tab(self):
        self.tab_history = QWidget()
        layout = QVBoxLayout(self.tab_history)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Barra de Filtros en una fila
        filter_box = QFrame()
        filter_box.setProperty("panel", "true")
        f_lay = QHBoxLayout(filter_box)
        f_lay.setContentsMargins(12, 10, 12, 10)
        f_lay.setSpacing(10)

        f_lay.addWidget(QLabel("Celda:"))
        self.txt_filter_cell = QLineEdit()
        self.txt_filter_cell.setFixedWidth(70)
        f_lay.addWidget(self.txt_filter_cell)

        f_lay.addWidget(QLabel("Tipo:"))
        self.cbo_filter_type = QComboBox()
        self.cbo_filter_type.addItems(["TODOS", "ENTRADA", "SALIDA", "AJUSTE", "RESERVA"])
        f_lay.addWidget(self.cbo_filter_type)

        f_lay.addWidget(QLabel("Buscar:"))
        self.txt_filter_search = QLineEdit()
        self.txt_filter_search.setPlaceholderText("Código u observación...")
        f_lay.addWidget(self.txt_filter_search)

        btn_filter = QPushButton("Filtrar")
        btn_filter.setProperty("level", "primary")
        btn_filter.setIcon(theme.get_icon("mdi6.magnify", color="#FFFFFF", theme_name=self.current_theme))
        btn_filter.clicked.connect(self._apply_history_filter)
        f_lay.addWidget(btn_filter)

        btn_clear = QPushButton("Limpiar")
        btn_clear.clicked.connect(self._clear_history_filter)
        f_lay.addWidget(btn_clear)

        btn_export = QPushButton("Exportar CSV")
        btn_export.setIcon(theme.get_icon("mdi6.file-export", theme_name=self.current_theme))
        btn_export.clicked.connect(self._export_history_csv)
        f_lay.addWidget(btn_export)

        layout.addWidget(filter_box)

        # Tabla QTableView con modelo de solo lectura
        self.history_model = HistoryTableModel()
        self.table_history = QTableView()
        self.table_history.setModel(self.history_model)
        self.table_history.setAlternatingRowColors(True)
        self.table_history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_history.verticalHeader().setVisible(False)
        self.table_history.setSelectionBehavior(QTableView.SelectRows)
        layout.addWidget(self.table_history)

        self.tabs.addTab(self.tab_history, "Historial")
        self._apply_history_filter()

    def _apply_history_filter(self):
        data = self.service.history_db.get_movements(
            cell_id=self.txt_filter_cell.text(),
            movement_type=self.cbo_filter_type.currentText(),
            search_text=self.txt_filter_search.text()
        )
        self.history_model.set_data(data)

    def _clear_history_filter(self):
        self.txt_filter_cell.clear()
        self.cbo_filter_type.setCurrentIndex(0)
        self.txt_filter_search.clear()
        self._apply_history_filter()

    def _export_history_csv(self):
        filepath, _ = QFileDialog.getSaveFileName(self, "Exportar Historial a CSV", "", "CSV Files (*.csv)")
        if filepath:
            data = self.history_model._data
            cnt = self.service.history_db.export_to_csv(filepath, movements=data)
            QMessageBox.information(self, "Exportación Completada", f"Se exportaron {cnt} registros a {filepath}")

    # ═══════════════════════════════════════════════
    # PESTAÑA 3: MANTENIMIENTO (Jog, Calibración, Canvas 2D)
    # ═══════════════════════════════════════════════

    def _build_maintenance_tab(self):
        self.tab_maintenance = QWidget()
        layout = QVBoxLayout(self.tab_maintenance)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ── Banner de Candado de Seguridad ──
        self.frame_maint_lock = QFrame()
        self.frame_maint_lock.setProperty("panel", "true")
        lock_lay = QHBoxLayout(self.frame_maint_lock)
        lock_lay.setContentsMargins(14, 10, 14, 10)

        self.lbl_lock_msg = QLabel("Modo mantenimiento — el movimiento manual y calibración pueden alterar los límites físicos del equipo.")
        self.lbl_lock_msg.setFont(QFont("Inter", 11, QFont.Bold))
        lock_lay.addWidget(self.lbl_lock_msg, stretch=1)

        self.btn_toggle_maint = QPushButton("Entrar en modo mantenimiento")
        self.btn_toggle_maint.setProperty("level", "primary")
        self.btn_toggle_maint.setIcon(theme.get_icon("mdi6.shield-lock-open", color="#FFFFFF", theme_name=self.current_theme))
        self.btn_toggle_maint.clicked.connect(self._toggle_maintenance_lock)
        lock_lay.addWidget(self.btn_toggle_maint)

        layout.addWidget(self.frame_maint_lock)

        # ── Contenido de Mantenimiento ──
        self.maint_content = QWidget()
        m_lay = QHBoxLayout(self.maint_content)
        m_lay.setContentsMargins(0, 0, 0, 0)
        m_lay.setSpacing(16)

        # Columna 1: Cruceta Jog y Comandos Directos
        col1 = QVBoxLayout()
        col1.setSpacing(12)

        # Control de Jog Continuo
        jog_group = QGroupBox("CONTROL DE JOG CONTINUO")
        j_lay = QVBoxLayout(jog_group)

        spd_lay = QHBoxLayout()
        spd_lay.addWidget(QLabel("Velocidad (mm/min):"))
        self.spin_jog_speed = QSpinBox()
        self.spin_jog_speed.setRange(10, 3000)
        self.spin_jog_speed.setValue(500)
        spd_lay.addWidget(self.spin_jog_speed)
        j_lay.addLayout(spd_lay)

        grid_cross = QGridLayout()
        btn_y_plus = JogButton("Y+ ▲", "Y", +1)
        btn_y_minus = JogButton("Y- ▼", "Y", -1)
        btn_x_plus = JogButton("X+ ►", "X", +1)
        btn_x_minus = JogButton("X- ◄", "X", -1)

        grid_cross.addWidget(btn_y_plus, 0, 1)
        grid_cross.addWidget(btn_x_minus, 1, 0)
        grid_cross.addWidget(btn_x_plus, 1, 2)
        grid_cross.addWidget(btn_y_minus, 2, 1)
        j_lay.addLayout(grid_cross)

        # Eje Z (Palas Horizontales: Extender / Retraer)
        z_lay = QHBoxLayout()
        btn_z_plus = JogButton("Z+ (Extender)", "Z", +1)
        btn_z_minus = JogButton("Z- (Retraer)", "Z", -1)
        z_lay.addWidget(btn_z_plus)
        z_lay.addWidget(btn_z_minus)
        j_lay.addLayout(z_lay)

        self.jog_buttons = [btn_y_plus, btn_y_minus, btn_x_plus, btn_x_minus, btn_z_plus, btn_z_minus]
        for btn in self.jog_buttons:
            btn.pressed_jog.connect(self._on_jog_pressed)
            btn.released_jog.connect(self._on_jog_released)

        col1.addWidget(jog_group)

        # Comandos Directos GRBL
        cmd_group = QGroupBox("COMANDOS DE CONTROL GRBL")
        cmd_lay = QGridLayout(cmd_group)

        self.btn_home = QPushButton("Home ($H)")
        self.btn_home.setToolTip("Ejecuta ciclo de homing ($H)")
        self.btn_home.clicked.connect(self._exec_home_and_confirm_origin)

        self.btn_unlock = QPushButton("Unlock ($X)")
        self.btn_unlock.setToolTip("Desbloquea alarma de GRBL ($X)")
        self.btn_unlock.clicked.connect(lambda: self.service.grbl.send_command("$X"))

        self.btn_zero = QPushButton("Zero Aquí (G92)")
        self.btn_zero.setToolTip("Fija el origen de trabajo temporal en la posición actual (G92 X0 Y0 Z0)")
        self.btn_zero.clicked.connect(self._exec_zero_and_confirm_origin)

        # CAMBIO: Botón de soft-homing sin finales de carrera.
        # Equivalente al flujo de almacen_automatico.py: $X + G92 + ir_a_posicion_carga().
        # Se ejecuta en hilo secundario (GoPosWorker) para no congelar la UI.
        self.btn_pos_carga = QPushButton("Ir a Pos. de Carga")
        self.btn_pos_carga.setToolTip(
            "Secuencia completa de referenciado sin finales de carrera:\n"
            "1. Desbloquea alarma ($X)\n"
            "2. Fija origen en posición actual (G92 X0 Y0 Z0)\n"
            "3. Mueve el carro a la posición de carga (X0 Y0)"
        )
        self.btn_pos_carga.clicked.connect(self._exec_ir_a_posicion_carga)

        cmd_lay.addWidget(self.btn_home, 0, 0)
        cmd_lay.addWidget(self.btn_unlock, 0, 1)
        cmd_lay.addWidget(self.btn_zero, 1, 0, 1, 2)
        cmd_lay.addWidget(self.btn_pos_carga, 2, 0, 1, 2)  # CAMBIO: fila 2, ancho completo

        # CAMBIO: Botón para corregir giro de motores en GRBL ($3=2: Y invertido, Z corregido)
        self.btn_invert_yz = QPushButton("Corregir Dir. Ejes ($3=2)")
        self.btn_invert_yz.setToolTip(
            "Configura $3=2 en GRBL:\n"
            "• Mantiene Eje Y: Y+ sube y Y- baja\n"
            "• Invierte Eje Z: Z+ extiende las palas y Z- las retrae\n"
            "Queda guardado en la memoria EEPROM permanentemente."
        )
        self.btn_invert_yz.clicked.connect(self._exec_invert_yz_dirs)
        cmd_lay.addWidget(self.btn_invert_yz, 3, 0, 1, 2)

        col1.addWidget(cmd_group)

        # Captura de Celda en Posición Actual
        btn_cap = QPushButton("Capturar Celda en Posición Actual")
        btn_cap.setFixedHeight(38)
        btn_cap.setIcon(theme.get_icon("mdi6.crosshairs-gps", theme_name=self.current_theme))
        btn_cap.clicked.connect(self._show_capture_cell_dialog)
        col1.addWidget(btn_cap)

        m_lay.addLayout(col1, stretch=2)

        # Columna 2: Canvas 2D
        col2 = QVBoxLayout()
        canvas_group = QGroupBox("MONITOREO DE PLANO 2D (X-Y)")
        c_lay = QVBoxLayout(canvas_group)
        self.canvas_2d = Warehouse2DCanvas(theme_name=self.current_theme)
        c_lay.addWidget(self.canvas_2d)
        col2.addWidget(canvas_group)
        m_lay.addLayout(col2, stretch=3)

        # Columna 3: Asistente de Calibración
        col3 = QVBoxLayout()
        calib_group = QGroupBox("ASISTENTE DE CALIBRACIÓN")
        c3_lay = QVBoxLayout(calib_group)

        # Indicador de Pasos
        self.wiz_progress = StepProgressBar(["Eje", "Búsqueda", "Medición", "Resultado"], current_step=0, theme_name=self.current_theme)
        c3_lay.addWidget(self.wiz_progress)

        self.wizard_stack = QStackedWidget()

        # Paso 1: Selección
        w_step1 = QWidget()
        w1_lay = QVBoxLayout(w_step1)
        w1_lay.addWidget(QLabel("<b>Paso 1:</b> Selecciona eje a calibrar:"))
        self.cbo_wiz_axis = QComboBox()
        self.cbo_wiz_axis.addItems(["X", "Y", "Z"])
        w1_lay.addWidget(self.cbo_wiz_axis)

        w1_lay.addWidget(QLabel("<b>Modo de calibración:</b>"))
        self.rad_calib_auto = QRadioButton("Automática (finales de carrera)")
        self.rad_calib_manual = QRadioButton("Manual (jog a cada extremo)")
        self.rad_calib_auto.setChecked(True)
        w1_lay.addWidget(self.rad_calib_auto)
        w1_lay.addWidget(self.rad_calib_manual)

        btn_wiz_start = QPushButton("Iniciar Calibración")
        btn_wiz_start.setProperty("level", "primary")
        btn_wiz_start.clicked.connect(self._wiz_start_search)
        w1_lay.addWidget(btn_wiz_start)
        w1_lay.addStretch()
        self.wizard_stack.addWidget(w_step1)

        # Paso 2: Progreso Búsqueda
        w_step2 = QWidget()
        w2_lay = QVBoxLayout(w_step2)
        w2_lay.addWidget(QLabel("<b>Paso 2:</b> Buscando extremos físicos..."))
        self.list_wiz_log = QListWidget()
        w2_lay.addWidget(self.list_wiz_log)
        btn_wiz_cancel = QPushButton("Cancelar")
        btn_wiz_cancel.clicked.connect(self._wiz_cancel)
        w2_lay.addWidget(btn_wiz_cancel)
        self.wizard_stack.addWidget(w_step2)

        # Paso 3: Medición Real
        w_step3 = QWidget()
        w3_lay = QVBoxLayout(w_step3)
        self.lbl_wiz_reported = QLabel("Distancia reportada: 0.000 mm")
        self.lbl_wiz_reported.setFont(QFont("JetBrains Mono", 11, QFont.Bold))
        w3_lay.addWidget(self.lbl_wiz_reported)
        w3_lay.addWidget(QLabel("<b>Paso 3:</b> Distancia real medida con instrumento (mm):"))
        self.spin_wiz_real = QDoubleSpinBox()
        self.spin_wiz_real.setRange(0.1, 1000.0)
        self.spin_wiz_real.setValue(100.0)
        w3_lay.addWidget(self.spin_wiz_real)
        btn_wiz_apply = QPushButton("Calcular y Aplicar")
        btn_wiz_apply.setProperty("level", "primary")
        btn_wiz_apply.clicked.connect(self._wiz_apply_calibration)
        w3_lay.addWidget(btn_wiz_apply)
        w3_lay.addStretch()
        self.wizard_stack.addWidget(w_step3)

        # Paso 4: Resultado
        w_step4 = QWidget()
        w4_lay = QVBoxLayout(w_step4)
        w4_lay.addWidget(QLabel("<b>Paso 4:</b> Calibración completada con éxito."))
        self.lbl_wiz_result = QLabel("")
        self.lbl_wiz_result.setFont(QFont("JetBrains Mono", 10))
        w4_lay.addWidget(self.lbl_wiz_result)
        self.btn_apply_sug_limit = QPushButton("Aplicar Límite Sugerido ($13x)")
        self.btn_apply_sug_limit.clicked.connect(self._wiz_apply_suggested_limit)
        w4_lay.addWidget(self.btn_apply_sug_limit)
        btn_wiz_finish = QPushButton("Finalizar")
        btn_wiz_finish.clicked.connect(lambda: [self.wizard_stack.setCurrentIndex(0), self.wiz_progress.set_current_step(0)])
        w4_lay.addWidget(btn_wiz_finish)
        w4_lay.addStretch()
        self.wizard_stack.addWidget(w_step4)

        # Paso Manual: Marcar Extremos
        w_step_manual = QWidget()
        wm_lay = QVBoxLayout(w_step_manual)
        wm_lay.addWidget(QLabel("<b>Calibración Manual:</b> Mueve con Jog y marca cada extremo."))
        self.lbl_wiz_manual_info = QLabel("Extremo 1: Sin marcar\nExtremo 2: Sin marcar")
        self.lbl_wiz_manual_info.setFont(QFont("JetBrains Mono", 10))
        wm_lay.addWidget(self.lbl_wiz_manual_info)

        self.btn_wiz_mark1 = QPushButton("Marcar Extremo 1 Aquí")
        self.btn_wiz_mark1.clicked.connect(lambda: self._wiz_mark_manual_point(1))
        self.btn_wiz_mark2 = QPushButton("Marcar Extremo 2 Aquí")
        self.btn_wiz_mark2.setEnabled(False)
        self.btn_wiz_mark2.clicked.connect(lambda: self._wiz_mark_manual_point(2))

        wm_lay.addWidget(self.btn_wiz_mark1)
        wm_lay.addWidget(self.btn_wiz_mark2)
        btn_m_cancel = QPushButton("Cancelar")
        btn_m_cancel.clicked.connect(self._wiz_cancel)
        wm_lay.addWidget(btn_m_cancel)
        wm_lay.addStretch()
        self.wizard_stack.addWidget(w_step_manual)

        c3_lay.addWidget(self.wizard_stack)
        col3.addWidget(calib_group)
        m_lay.addLayout(col3, stretch=3)

        layout.addWidget(self.maint_content, stretch=1)
        self.tabs.addTab(self.tab_maintenance, "Mantenimiento")

        self._update_maint_lock_ui()

    def _toggle_maintenance_lock(self):
        self.maint_mode_unlocked = not self.maint_mode_unlocked
        self._update_maint_lock_ui()
        self.apply_state(self.machine_state_str)

    def _update_maint_lock_ui(self):
        t = theme.get_tokens(self.current_theme)
        if self.maint_mode_unlocked:
            self.lbl_lock_msg.setText("MODO MANTENIMIENTO ACTIVO — Controles manuales desbloqueados.")
            self.lbl_lock_msg.setStyleSheet(f"color: {t['WARN']};")
            self.btn_toggle_maint.setText("Bloquear Mantenimiento")
            self.btn_toggle_maint.setIcon(theme.get_icon("mdi6.shield-lock", color="#FFFFFF", theme_name=self.current_theme))
            self.maint_content.setEnabled(True)
        else:
            self.lbl_lock_msg.setText("Modo mantenimiento — el movimiento manual y calibración pueden alterar los límites físicos del equipo.")
            self.lbl_lock_msg.setStyleSheet(f"color: {t['TEXT_MUTED']};")
            self.btn_toggle_maint.setText("Entrar en modo mantenimiento")
            self.btn_toggle_maint.setIcon(theme.get_icon("mdi6.shield-lock-open", color="#FFFFFF", theme_name=self.current_theme))
            self.maint_content.setEnabled(False)

    # ── JOG SEGURO Y PARADA POR TECLADO ──

    def _on_jog_pressed(self, axis: str, direction: int):
        spd = float(self.spin_jog_speed.value())
        self.jog_worker.start_jog(axis, direction, spd)

    def _on_jog_released(self):
        self.jog_worker.stop_jog()

    def keyPressEvent(self, event):
        """Manejo seguro de pulsación de teclado ignorando repetición automática."""
        if event.isAutoRepeat():
            return super().keyPressEvent(event)

        # Solo permitir jog en la pestaña de mantenimiento si está desbloqueada
        if self.tabs.currentIndex() == 2 and self.maint_mode_unlocked:
            key_map = {
                Qt.Key_Left: ("X", -1),
                Qt.Key_Right: ("X", +1),
                Qt.Key_Up: ("Y", +1),
                Qt.Key_Down: ("Y", -1),
                Qt.Key_PageUp: ("Z", +1),
                Qt.Key_PageDown: ("Z", -1),
            }
            if event.key() in key_map:
                axis, dirn = key_map[event.key()]
                self._on_jog_pressed(axis, dirn)
                event.accept()
                return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        """Detención garantizada del movimiento al soltar la tecla."""
        if event.isAutoRepeat():
            return super().keyReleaseEvent(event)

        jog_keys = (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown)
        if event.key() in jog_keys:
            self._on_jog_released()
            event.accept()
            return

        super().keyReleaseEvent(event)

    def changeEvent(self, event):
        """Si la ventana pierde foco, detener inmediatamente cualquier jog activo."""
        if event.type() == QEvent.ActivationChange:
            if not self.isActiveWindow():
                self._on_jog_released()
        super().changeEvent(event)

    # ── COMANDOS GRBL Y CONFIRMACIÓN DE ORIGEN ──

    def _send_feed_hold(self):
        if self.service.is_connected and self.service.grbl:
            self.service.grbl.feed_hold()

    def _send_cycle_start(self):
        if self.service.is_connected and self.service.grbl:
            self.service.grbl.cycle_start()

    def _send_emergency_stop(self):
        """Paro de emergencia inmediato sin confirmación modal."""
        if self.service.is_connected and self.service.grbl:
            self.service.grbl.emergency_stop()
            self.machine_state_str = "Alarma"
            self.status_bar.update_machine_state("Alarm:Abort")
            self.alarm_banner.mostrar("alarm", "PARO DE EMERGENCIA EJECUTADO. Desbloquea con $X cuando sea seguro.")
            self.apply_state(self.machine_state_str)

    def _exec_home_and_confirm_origin(self):
        if self.service.is_connected:
            if self.service.grbl.send_command("$H"):
                self.origin_confirmed = True
                self.status_bar.set_origin_confirmed(True)
                self.status_bar.update_machine_state("Idle")
                self.apply_state(self.machine_state_str)
            else:
                # CAMBIO: Aviso amigable explicando error:5 si se trabaja sin finales de carrera
                QMessageBox.warning(
                    self, "Homing no habilitado ($H)",
                    "GRBL rechazó el comando con 'error:5'.\n\n"
                    "Causa: El ciclo de homing no está activo ($22=0) o la máquina no tiene finales de carrera físicos instalados.\n\n"
                    "Solución sin finales de carrera:\n"
                    "1. Coloca el carro en la posición inicial (origen 0,0,0).\n"
                    "2. Pulsa el botón 'Zero Aquí (G92)' para confirmar el origen."
                )

    def _exec_zero_and_confirm_origin(self):
        if self.service.is_connected:
            if self.service.grbl.send_command("G92 X0 Y0 Z0"):
                self.origin_confirmed = True
                self.status_bar.set_origin_confirmed(True)
                self.status_bar.update_machine_state("Idle")
                self.apply_state(self.machine_state_str)

    # CAMBIO: Secuencia completa de soft-homing sin finales de carrera.
    # Equivalente al flujo de almacen_automatico.py ($X + G92 + ir_a_posicion_carga).
    # Se corre en GoPosWorker (hilo secundario) para no bloquear la UI.
    def _exec_ir_a_posicion_carga(self):
        """Inicia la secuencia: $X → G92 X0 Y0 Z0 → ir_a_posicion_carga() en hilo secundario."""
        if not self.service.is_connected or not self.service.grbl:
            QMessageBox.critical(self, "Sin Conexión", "Conecta con GRBL antes de ejecutar la secuencia.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirmar: Ir a Posición de Carga",
            "Esta operación:\n"
            "  1. Desbloqueará la alarma GRBL ($X)\n"
            "  2. Fijará la posición actual como origen (G92 X0 Y0 Z0)\n"
            "  3. Moverá el carro a la posición de carga (X0, Y0)\n\n"
            "Asegúrate de que el camino esté despejado antes de continuar.\n\n"
            "¿Continuar?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        # Deshabilitar botón durante la ejecución para evitar doble disparo
        self.btn_pos_carga.setEnabled(False)
        self.btn_pos_carga.setText("Ejecutando...")

        # Crear worker y hilo
        self.go_pos_thread = QThread()
        self.go_pos_worker = GoPosWorker(self.service)
        self.go_pos_worker.moveToThread(self.go_pos_thread)

        self.go_pos_thread.started.connect(self.go_pos_worker.run)
        self.go_pos_worker.step_progress.connect(self._on_go_pos_step)
        self.go_pos_worker.finished.connect(self._on_go_pos_finished)
        self.go_pos_worker.finished.connect(self.go_pos_thread.quit)
        self.go_pos_thread.finished.connect(self.go_pos_thread.deleteLater)

        self.go_pos_thread.start()

    def _on_go_pos_step(self, paso: int, texto: str):
        """Muestra el progreso de cada paso en el banner de alarma (informativo)."""
        self.alarm_banner.mostrar("info", f"[Paso {paso}] {texto}")

    def _on_go_pos_finished(self, ok: bool):
        """Callback al terminar la secuencia de Ir a Posición de Carga."""
        self.btn_pos_carga.setText("Ir a Pos. de Carga")
        if ok:
            # Marcar origen como confirmado (equivalente a haber hecho $H o G92 exitoso)
            self.origin_confirmed = True
            self.status_bar.set_origin_confirmed(True)
            self.status_bar.update_machine_state("Idle")
            self.alarm_banner.ocultar()
            self.apply_state(self.machine_state_str)
        else:
            self.alarm_banner.mostrar(
                "alarm",
                "Error durante 'Ir a Pos. de Carga'. Verifica la conexión y el estado GRBL."
            )
        self.apply_state(self.machine_state_str)

    # CAMBIO: Aplicar $3=2 en GRBL para corregir el giro del motor Z manteniendo Y corregido
    def _exec_invert_yz_dirs(self):
        """Aplica $3=2 en GRBL: Eje Y invertido (sube/baja bien) y Eje Z corregido (extender/retraer bien)."""
        if not self.service.is_connected or not self.service.grbl:
            QMessageBox.critical(self, "Sin Conexión", "Conecta con GRBL antes de configurar parámetros.")
            return

        confirm = QMessageBox.question(
            self,
            "Corregir Sentido de Motor Z",
            "¿Deseas enviar el comando <b>$3=2</b> a la placa GRBL?<br><br>"
            "Esto configurará:<br>"
            "  • <b>Eje Y (Bit 1 = 1)</b>: Mantiene el carro subiendo con Y+ y bajando con Y-.<br>"
            "  • <b>Eje Z (Bit 2 = 0)</b>: Invierte las palas para que <b>Z+ las extienda (saque)</b> y <b>Z- las retraiga (meta)</b>.<br><br>"
            "El ajuste se guardará permanentemente en la memoria EEPROM de la placa.<br><br>"
            "¿Deseas aplicar el cambio ahora?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if confirm != QMessageBox.Yes:
            return

        if self.service.grbl.send_command("$3=2"):
            QMessageBox.information(
                self,
                "Configuración Guardada",
                "<b>$3=2 aplicado con éxito.</b><br><br>"
                "Las direcciones de movimiento ahora son:<br>"
                "  • <b>Y+ ▲</b>: Sube el carro<br>"
                "  • <b>Y- ▼</b>: Baja el carro<br>"
                "  • <b>Z+ (Extender)</b>: Saca las palas hacia la celda<br>"
                "  • <b>Z- (Retraer)</b>: Guarda las palas adentro"
            )
        else:
            QMessageBox.warning(self, "Error al Enviar Comando", "GRBL no respondió o rechazó el comando $3=2.")

    # ── CAPTURA DE CELDA (DIÁLOGO UNIFICADO COMPARATIVO) ──

    def _show_capture_cell_dialog(self):
        x = self.canvas_2d.cur_x
        y = self.canvas_2d.cur_y

        dialog = QDialog(self)
        dialog.setWindowTitle("Capturar Celda en Posición Actual")
        dialog.setFixedWidth(380)
        d_lay = QVBoxLayout(dialog)

        lbl_cur = QLabel(f"Posición actual del carro: <b>X = {x:.3f} mm, Y = {y:.3f} mm</b>")
        lbl_cur.setFont(QFont("Inter", 11))
        d_lay.addWidget(lbl_cur)

        form = QFormLayout()
        cbo_cell = QComboBox()
        status_dict = self.service.get_cell_status()
        existing = list(status_dict.keys())
        cbo_cell.addItems(existing + ["Nueva celda..."])
        form.addRow("Celda:", cbo_cell)

        txt_new_id = QLineEdit()
        txt_new_id.setPlaceholderText("Ej: C3, D1...")
        txt_new_id.setEnabled(False)
        form.addRow("ID Personalizado:", txt_new_id)

        lbl_compare = QLabel("Coordenadas actuales de la celda: -")
        lbl_compare.setFont(QFont("JetBrains Mono", 10))
        form.addRow(lbl_compare)

        spin_z_safe = QDoubleSpinBox()
        spin_z_safe.setRange(0, 500)
        spin_z_safe.setValue(120.0)
        form.addRow("Z Safe (mm):", spin_z_safe)

        spin_z_pickup = QDoubleSpinBox()
        spin_z_pickup.setRange(0, 500)
        spin_z_pickup.setValue(0.0)
        form.addRow("Z Pickup (mm):", spin_z_pickup)

        d_lay.addLayout(form)

        def on_cell_changed(text):
            is_new = (text == "Nueva celda...")
            txt_new_id.setEnabled(is_new)
            if not is_new and text in status_dict:
                cinfo = status_dict[text]
                lbl_compare.setText(f"Existente: X={cinfo.get('x', 0):.2f}, Y={cinfo.get('y', 0):.2f}")
            else:
                lbl_compare.setText("Nueva celda no configurada")

        cbo_cell.currentTextChanged.connect(on_cell_changed)
        on_cell_changed(cbo_cell.currentText())

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.button(QDialogButtonBox.Ok).setText("Guardar Celda")
        bbox.button(QDialogButtonBox.Ok).setProperty("level", "primary")
        bbox.accepted.connect(dialog.accept)
        bbox.rejected.connect(dialog.reject)
        d_lay.addWidget(bbox)

        if dialog.exec() == QDialog.Accepted:
            if cbo_cell.currentText() == "Nueva celda...":
                cell_id = txt_new_id.text().strip().upper()
                overwrite = True
            else:
                cell_id = cbo_cell.currentText().strip().upper()
                overwrite = True

            if not cell_id:
                QMessageBox.warning(self, "Atención", "Ingresa un ID de celda válido.")
                return

            ok, msg = save_manual_cell_pos(
                cell_id=cell_id,
                x=x,
                y=y,
                z_safe=spin_z_safe.value(),
                z_pickup=spin_z_pickup.value(),
                overwrite=overwrite,
                grbl_limits=self.service.grbl_limits
            )

            if ok:
                self.service.reload_config()
                self.refresh_dashboard()
                self._load_config_into_ui()
                QMessageBox.information(self, "Guardado", msg)
            else:
                QMessageBox.warning(self, "Error", msg)

    # ── ASISTENTE DE CALIBRACIÓN DE EJES (WIZARD CON CICLO DE HILOS LIMPIO) ──

    def _cleanup_calib_thread(self):
        """Detiene y libera de forma segura el hilo de calibración anterior (Fix Bug 4)."""
        if self.calib_worker_thread is not None:
            if self.calib_worker_thread.isRunning():
                self.calib_worker_thread.quit()
                self.calib_worker_thread.wait(2000)
            self.calib_worker_thread = None

    def _wiz_start_search(self):
        if not self.service.is_connected:
            QMessageBox.critical(self, "Sin Conexión", "Conecta con GRBL antes de calibrar.")
            return

        axis = self.cbo_wiz_axis.currentText()
        self.wiz_current_axis = axis
        self.list_wiz_log.clear()

        if self.rad_calib_manual.isChecked():
            self.wiz_punto1 = None
            self.wiz_punto2 = None
            self.lbl_wiz_manual_info.setText(f"Eje {axis} — Extremo 1: Sin marcar\nExtremo 2: Sin marcar")
            self.btn_wiz_mark1.setEnabled(True)
            self.btn_wiz_mark2.setEnabled(False)
            self.wizard_stack.setCurrentIndex(4)
            self.wiz_progress.set_current_step(1)
        else:
            self.wizard_stack.setCurrentIndex(1)
            self.wiz_progress.set_current_step(1)
            self._cleanup_calib_thread()

            self.calib_worker_thread = QThread()
            self.calib_worker = CalibrationWorker(self.service.grbl)
            self.wiz_calibrator_instance = AxisCalibrator(self.service.grbl)
            self.calib_worker.calibrator = self.wiz_calibrator_instance
            self.calib_worker.moveToThread(self.calib_worker_thread)

            self.calib_worker.progress.connect(self._wiz_on_progress)
            self.calib_worker.extremos_found.connect(self._wiz_on_extremos_found)
            self.calib_worker.error_occurred.connect(self._wiz_on_error)

            self.calib_worker_thread.started.connect(lambda: self.calib_worker.do_buscar_extremos(axis))
            self.calib_worker_thread.start()

    def _wiz_mark_manual_point(self, point_index: int):
        axis = self.wiz_current_axis
        self._cleanup_calib_thread()

        self.calib_worker_thread = QThread()
        self.calib_worker = CalibrationWorker(self.service.grbl)
        if not self.wiz_calibrator_instance:
            self.wiz_calibrator_instance = AxisCalibrator(self.service.grbl)
        self.calib_worker.calibrator = self.wiz_calibrator_instance
        self.calib_worker.moveToThread(self.calib_worker_thread)

        self.calib_worker.punto_manual_captured.connect(self._wiz_on_manual_point_captured)
        self.calib_worker.error_occurred.connect(self._wiz_on_error)

        self.calib_worker_thread.started.connect(
            lambda: self.calib_worker.do_capturar_punto_manual(axis, point_index)
        )
        self.calib_worker_thread.start()

    def _wiz_on_manual_point_captured(self, point_index: int, pos: float):
        self._cleanup_calib_thread()
        axis = self.wiz_current_axis
        if point_index == 1:
            self.wiz_punto1 = pos
            self.btn_wiz_mark2.setEnabled(True)
            self.lbl_wiz_manual_info.setText(
                f"Eje {axis} — Extremo 1: {pos:.3f} mm\nExtremo 2: Mueve el eje y presiona 'Marcar Extremo 2'"
            )
        elif point_index == 2:
            self.wiz_punto2 = pos
            dist_rep = abs(self.wiz_punto2 - self.wiz_punto1)
            self.wiz_dist_rep = dist_rep
            self.lbl_wiz_reported.setText(f"Distancia reportada por GRBL: {dist_rep:.3f} mm")
            self.wizard_stack.setCurrentIndex(2)
            self.wiz_progress.set_current_step(2)

    def _wiz_on_progress(self, msg: str):
        self.list_wiz_log.addItem(msg)
        self.list_wiz_log.scrollToBottom()

    def _wiz_on_extremos_found(self, dist_rep: float):
        self._cleanup_calib_thread()
        self.wiz_dist_rep = dist_rep
        self.lbl_wiz_reported.setText(f"Distancia reportada por GRBL: {dist_rep:.3f} mm")
        self.wizard_stack.setCurrentIndex(2)
        self.wiz_progress.set_current_step(2)

    def _wiz_apply_calibration(self):
        dist_real = self.spin_wiz_real.value()
        self._cleanup_calib_thread()

        self.calib_worker_thread = QThread()
        self.calib_worker = CalibrationWorker(self.service.grbl)
        # Asegurar preservación del calibrador para evitar error de inicialización (Fix Bug 2)
        if not self.wiz_calibrator_instance:
            self.wiz_calibrator_instance = AxisCalibrator(self.service.grbl)
        self.calib_worker.calibrator = self.wiz_calibrator_instance
        self.calib_worker.moveToThread(self.calib_worker_thread)

        self.calib_worker.progress.connect(self._wiz_on_progress)
        self.calib_worker.calibration_completed.connect(self._wiz_on_completed)
        self.calib_worker.error_occurred.connect(self._wiz_on_error)

        self.calib_worker_thread.started.connect(
            lambda: self.calib_worker.do_aplicar_calibracion(self.wiz_current_axis, self.wiz_dist_rep, dist_real)
        )
        self.calib_worker_thread.start()

    def _wiz_on_completed(self, res):
        self._cleanup_calib_thread()
        self.wiz_last_result = res
        txt = (
            f"Eje: {res.axis}\n"
            f"Pasos/mm Anterior: {res.pasos_mm_anterior:.3f}\n"
            f"Pasos/mm Nuevo:    {res.pasos_mm_nuevo:.3f}\n"
            f"Dist. Reportada:   {res.distancia_reportada_mm:.3f} mm\n"
            f"Dist. Real Medida: {res.distancia_real_mm:.3f} mm\n"
            f"Límite Sugerido:   {res.limite_recorrido_sugerido_mm:.1f} mm ($13{res.axis.lower()})"
        )
        self.lbl_wiz_result.setText(txt)
        self.wizard_stack.setCurrentIndex(3)
        self.wiz_progress.set_current_step(3)

    def _wiz_apply_suggested_limit(self):
        if hasattr(self, 'wiz_last_result') and self.wiz_last_result:
            ax = self.wiz_last_result.axis.upper()
            val = self.wiz_last_result.limite_recorrido_sugerido_mm
            param = f"$13{0 if ax=='X' else 1 if ax=='Y' else 2}"
            if self.service.grbl.send_command(f"{param}={val:.1f}"):
                self.service.refresh_grbl_limits()
                self.canvas_2d.set_limits(self.service.grbl_limits['X'], self.service.grbl_limits['Y'])
                QMessageBox.information(self, "Límite Guardado", f"{param}={val:.1f} aplicado a EEPROM")

    def _wiz_cancel(self):
        if hasattr(self, 'calib_worker') and self.calib_worker:
            self.calib_worker.do_cancelar()
        self._cleanup_calib_thread()
        self.wizard_stack.setCurrentIndex(0)
        self.wiz_progress.set_current_step(0)

    def _wiz_on_error(self, err_msg: str):
        self._cleanup_calib_thread()
        QMessageBox.critical(self, "Error de Calibración", err_msg)
        self.wizard_stack.setCurrentIndex(0)
        self.wiz_progress.set_current_step(0)

    # ═══════════════════════════════════════════════
    # PESTAÑA 4: CONFIGURACIÓN Y ZONA DE PELIGRO
    # ═══════════════════════════════════════════════

    def _build_config_tab(self):
        self.tab_config = QWidget()
        layout = QHBoxLayout(self.tab_config)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)

        # Lado Izquierdo: Generador y Edición Manual
        left_lay = QVBoxLayout()
        left_lay.setSpacing(14)

        # Generador de Grid
        auto_group = QGroupBox("GENERADOR DE CUADRÍCULA")
        auto_form = QFormLayout(auto_group)

        self.spin_rows = QSpinBox()
        self.spin_rows.setRange(1, 10)
        self.spin_rows.setValue(2)
        auto_form.addRow("Filas:", self.spin_rows)

        self.spin_cols = QSpinBox()
        self.spin_cols.setRange(1, 10)
        self.spin_cols.setValue(2)
        auto_form.addRow("Columnas:", self.spin_cols)

        self.spin_x0 = QDoubleSpinBox()
        self.spin_x0.setRange(0, 500)
        self.spin_x0.setValue(140.0)
        auto_form.addRow("Origen X0 (mm):", self.spin_x0)

        self.spin_y0 = QDoubleSpinBox()
        self.spin_y0.setRange(0, 500)
        self.spin_y0.setValue(0.0)
        auto_form.addRow("Origen Y0 (mm):", self.spin_y0)

        self.spin_dx = QDoubleSpinBox()
        self.spin_dx.setRange(1, 500)
        self.spin_dx.setValue(70.0)
        auto_form.addRow("Paso dx (mm):", self.spin_dx)

        self.spin_dy = QDoubleSpinBox()
        self.spin_dy.setRange(1, 500)
        self.spin_dy.setValue(110.0)
        auto_form.addRow("Paso dy (mm):", self.spin_dy)

        self.chk_keep_manual = QCheckBox("Conservar celdas calibradas manualmente")
        self.chk_keep_manual.setChecked(True)
        auto_form.addRow(self.chk_keep_manual)

        btn_gen_grid = QPushButton("Generar y Sobrescribir Cuadrícula")
        btn_gen_grid.setProperty("level", "primary")
        btn_gen_grid.clicked.connect(self._generate_grid_clicked)
        auto_form.addRow(btn_gen_grid)

        left_lay.addWidget(auto_group)

        # Edición Manual de Celdas
        man_group = QGroupBox("EDICIÓN MANUAL DE COORDENADAS")
        man_lay = QVBoxLayout(man_group)

        self.table_cells_config = QTableWidget(0, 5)
        self.table_cells_config.setHorizontalHeaderLabels(["ID", "X (mm)", "Y (mm)", "Z Safe", "Z Pickup"])
        self.table_cells_config.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_cells_config.verticalHeader().setVisible(False)
        man_lay.addWidget(self.table_cells_config)

        btn_c_box = QHBoxLayout()
        btn_add_c = QPushButton("Agregar")
        btn_add_c.clicked.connect(self._add_manual_cell_row)
        btn_del_c = QPushButton("Eliminar")
        btn_del_c.clicked.connect(self._del_manual_cell_row)
        btn_save_c = QPushButton("Guardar Cambios")
        btn_save_c.setProperty("level", "primary")
        btn_save_c.clicked.connect(self._save_manual_cells)

        btn_c_box.addWidget(btn_add_c)
        btn_c_box.addWidget(btn_del_c)
        btn_c_box.addWidget(btn_save_c)
        man_lay.addLayout(btn_c_box)

        left_lay.addWidget(man_group)
        layout.addLayout(left_lay, stretch=3)

        # Lado Derecho: Preferencias de Tema y Zona de Peligro
        right_lay = QVBoxLayout()
        right_lay.setSpacing(14)

        # Selector de Tema
        theme_group = QGroupBox("PREFERENCIAS DE INTERFAZ")
        t_lay = QVBoxLayout(theme_group)

        t_lay.addWidget(QLabel("<b>Tema Visual:</b>"))
        self.rad_theme_dark = QRadioButton("Tema Oscuro (Planta / Estándar ISA-101)")
        self.rad_theme_light = QRadioButton("Tema Claro (Presentación / Proyector)")

        if self.current_theme == "light":
            self.rad_theme_light.setChecked(True)
        else:
            self.rad_theme_dark.setChecked(True)

        self.rad_theme_dark.toggled.connect(lambda checked: self._on_theme_changed("dark") if checked else None)
        self.rad_theme_light.toggled.connect(lambda checked: self._on_theme_changed("light") if checked else None)

        t_lay.addWidget(self.rad_theme_dark)
        t_lay.addWidget(self.rad_theme_light)
        t_lay.addStretch()

        right_lay.addWidget(theme_group)

        # ── Zona de Peligro (Confirmación Escrita) ──
        danger_group = QGroupBox("ZONA DE PELIGRO")
        t_tokens = theme.get_tokens(self.current_theme)
        danger_group.setStyleSheet(f"""
            QGroupBox {{
                border: 1px solid {t_tokens['ALARM']};
                margin-top: 24px;
            }}
            QGroupBox::title {{
                color: {t_tokens['ALARM']};
            }}
        """)
        d_lay = QVBoxLayout(danger_group)
        d_lay.setContentsMargins(14, 14, 14, 14)
        d_lay.setSpacing(10)

        d_msg = QLabel("<b>Acción destructiva:</b> Vaciar todas las celdas marca el inventario completo como vacío y emite registros de salida para cada ítem.")
        d_msg.setWordWrap(True)
        d_lay.addWidget(d_msg)

        d_lay.addWidget(QLabel("Para desbloquear el vaciado, escribe <b>VACIAR</b>:"))
        self.txt_confirm_vaciar = QLineEdit()
        self.txt_confirm_vaciar.setPlaceholderText("Escribe VACIAR aquí...")
        d_lay.addWidget(self.txt_confirm_vaciar)

        self.btn_vaciar_todo = QPushButton("Vaciar Todo el Almacén")
        self.btn_vaciar_todo.setProperty("level", "danger")
        self.btn_vaciar_todo.setFixedHeight(40)
        self.btn_vaciar_todo.setEnabled(False)
        self.btn_vaciar_todo.setToolTip("Escribe VACIAR para habilitar este botón")
        self.btn_vaciar_todo.clicked.connect(self._vaciar_almacen_confirmed)
        d_lay.addWidget(self.btn_vaciar_todo)

        self.txt_confirm_vaciar.textChanged.connect(self._check_vaciar_input)

        right_lay.addWidget(danger_group)
        right_lay.addStretch()

        layout.addLayout(right_lay, stretch=2)
        self.tabs.addTab(self.tab_config, "Configuracion")

        self._load_config_into_ui()

    def _check_vaciar_input(self, text: str):
        valid = (text.strip() == "VACIAR")
        self.btn_vaciar_todo.setEnabled(valid)
        if valid:
            self.btn_vaciar_todo.setToolTip("Presiona para ejecutar el vaciado completo del almacén")
        else:
            self.btn_vaciar_todo.setToolTip("Escribe VACIAR para habilitar este botón")

    def _vaciar_almacen_confirmed(self):
        reply = QMessageBox.warning(
            self, "Confirmar Vaciado General",
            "¿Confirmas que deseas vaciar todas las celdas del almacén?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.service.vaciar_todo()
            self.txt_confirm_vaciar.clear()
            self.refresh_dashboard()
            QMessageBox.information(self, "Almacén Vaciado", "Se marcaron todas las celdas como vacías.")

    def _load_config_into_ui(self):
        cfg = load_config()
        grid_info = cfg.get("grid", {})
        self.spin_rows.setValue(grid_info.get("rows", 2))
        self.spin_cols.setValue(grid_info.get("cols", 2))
        self.spin_x0.setValue(grid_info.get("x0", 140.0))
        self.spin_y0.setValue(grid_info.get("y0", 0.0))
        self.spin_dx.setValue(grid_info.get("dx", 70.0))
        self.spin_dy.setValue(grid_info.get("dy", 110.0))

        cells = cfg.get("cells", {})
        self.table_cells_config.setRowCount(len(cells))
        for r, (cid, cdata) in enumerate(cells.items()):
            self.table_cells_config.setItem(r, 0, QTableWidgetItem(cid))
            self.table_cells_config.setItem(r, 1, QTableWidgetItem(str(cdata.get("x", 0.0))))
            self.table_cells_config.setItem(r, 2, QTableWidgetItem(str(cdata.get("y", 0.0))))
            self.table_cells_config.setItem(r, 3, QTableWidgetItem(str(cdata.get("z_safe", 120.0))))
            self.table_cells_config.setItem(r, 4, QTableWidgetItem(str(cdata.get("z_pickup", 0.0))))

    def _generate_grid_clicked(self):
        reply = QMessageBox.warning(
            self, "Confirmar Regeneración",
            "¿Deseas generar la nueva cuadrícula? Se creará una copia de respaldo automática.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            create_config_backup()
            new_cfg = generate_grid_cells(
                rows=self.spin_rows.value(),
                cols=self.spin_cols.value(),
                x0=self.spin_x0.value(),
                y0=self.spin_y0.value(),
                dx=self.spin_dx.value(),
                dy=self.spin_dy.value(),
                overwrite_manual=not self.chk_keep_manual.isChecked()
            )
            save_config(new_cfg)
            self.service.reload_config()
            self._load_config_into_ui()
            self.refresh_dashboard()
            QMessageBox.information(self, "Cuadrícula Generada", "Configuración de celdas actualizada correctamente.")

    def _add_manual_cell_row(self):
        r = self.table_cells_config.rowCount()
        self.table_cells_config.insertRow(r)
        self.table_cells_config.setItem(r, 0, QTableWidgetItem(f"C{r+1}"))
        self.table_cells_config.setItem(r, 1, QTableWidgetItem("100.0"))
        self.table_cells_config.setItem(r, 2, QTableWidgetItem("100.0"))
        self.table_cells_config.setItem(r, 3, QTableWidgetItem("120.0"))
        self.table_cells_config.setItem(r, 4, QTableWidgetItem("0.0"))

    def _del_manual_cell_row(self):
        curr = self.table_cells_config.currentRow()
        if curr >= 0:
            self.table_cells_config.removeRow(curr)

    def _save_manual_cells(self):
        new_cells = {}
        limits = self.service.grbl_limits

        for r in range(self.table_cells_config.rowCount()):
            cid = self.table_cells_config.item(r, 0).text().strip().upper()
            try:
                x = float(self.table_cells_config.item(r, 1).text())
                y = float(self.table_cells_config.item(r, 2).text())
                z_safe = float(self.table_cells_config.item(r, 3).text())
                z_pickup = float(self.table_cells_config.item(r, 4).text())
            except (ValueError, AttributeError):
                QMessageBox.critical(self, "Error de Formato", f"Fila {r+1}: Valores numéricos no válidos.")
                return

            valid, err_msg = validate_coords_against_grbl(x, y, z_safe, z_pickup, limits)
            if not valid:
                QMessageBox.critical(self, "Límite Excedido", f"Celda {cid}: {err_msg}")
                return

            new_cells[cid] = {
                "x": x, "y": y, "z_safe": z_safe, "z_pickup": z_pickup, "manual": True
            }

        cfg = load_config()
        cfg["cells"] = new_cells
        save_config(cfg)
        self.service.reload_config()
        self.refresh_dashboard()
        QMessageBox.information(self, "Guardado", "Celdas guardadas correctamente.")

    # ═══════════════════════════════════════════════
    # MATRIZ CENTRALIZADA DE HABILITACIÓN (apply_state)
    # ═══════════════════════════════════════════════

    def apply_state(self, state: str):
        """
        Reglas de habilitación industrial centralizadas (Norma ISA-101 Sección 7).
        Aplica tooltips claros indicando el motivo de cada control deshabilitado.
        """
        self.machine_state_str = state
        is_conn = self.service.is_connected
        is_orig = self.origin_confirmed
        is_maint = (self.tabs.currentIndex() == 2 and self.maint_mode_unlocked)

        # 1. Ingresar / Retirar Ítem
        if not is_conn:
            self.btn_ingresar_item.setEnabled(True)
            self.btn_ingresar_item.setToolTip("Registro en base de datos activo (sin movimiento GRBL)")
            self.btn_retirar_item.setEnabled(True)
        elif "Alarm" in state:
            self.btn_ingresar_item.setEnabled(False)
            self.btn_ingresar_item.setToolTip("Equipo en alarma: desbloquea con $X")
            self.btn_retirar_item.setEnabled(False)
        elif state.startswith("Hold"):
            self.btn_ingresar_item.setEnabled(False)
            self.btn_ingresar_item.setToolTip("Equipo en pausa: reanuda el movimiento (~)")
            self.btn_retirar_item.setEnabled(False)
        elif "Run" in state:
            self.btn_ingresar_item.setEnabled(False)
            self.btn_ingresar_item.setToolTip("Equipo en movimiento: espera que finalice")
            self.btn_retirar_item.setEnabled(False)
        else:
            self.btn_ingresar_item.setEnabled(True)
            self.btn_ingresar_item.setToolTip("")
            self.btn_retirar_item.setEnabled(True)

        # 2. Mover carro a celda
        can_move_cell = (is_conn and is_orig and state == "Listo")
        self.btn_det_move.setEnabled(can_move_cell)
        if not is_conn:
            self.btn_det_move.setToolTip("GRBL no conectado")
        elif not is_orig:
            self.btn_det_move.setToolTip("Referencia primero los ejes ($H o G92)")
        elif state != "Listo":
            self.btn_det_move.setToolTip(f"Equipo no disponible ({state})")
        else:
            self.btn_det_move.setToolTip("Envía el carro a la celda seleccionada")

        # 3. Jog manual (solo en mantenimiento y desbloqueado)
        can_jog = (is_conn and is_maint and not "Alarm" in state and not state.startswith("Hold"))
        for btn in self.jog_buttons:
            btn.setEnabled(can_jog)
            if not is_conn:
                btn.setToolTip("Conecta con GRBL primero")
            elif not is_maint:
                btn.setToolTip("Desbloquea el modo mantenimiento primero")
            elif "Alarm" in state:
                btn.setToolTip("Equipo en alarma: desbloquea con $X")
            elif state.startswith("Hold"):
                btn.setToolTip("Pausa activa: reanuda con (~)")
            else:
                btn.setToolTip("Mantén presionado para desplazar el eje de forma continua")

        # 4. Comandos Home / Unlock / Zero / Ir a Pos. de Carga
        can_home = (is_conn and is_maint and not "Alarm" in state and not state.startswith("Hold"))
        self.btn_home.setEnabled(can_home)
        self.btn_unlock.setEnabled(is_conn)
        self.btn_zero.setEnabled(is_conn and is_maint)
        # CAMBIO: btn_pos_carga habilitado si conectado, en mantenimiento, y no corriendo otro movimiento
        can_go_pos = (is_conn and is_maint and not state.startswith("Hold") and not state.startswith("Run"))
        self.btn_pos_carga.setEnabled(can_go_pos)
        self.btn_invert_yz.setEnabled(is_conn and is_maint)

    # ═══════════════════════════════════════════════
    # CONEXIÓN SERIAL Y THREADING WORKERS
    # ═══════════════════════════════════════════════

    def _setup_workers(self):
        # 1. Hilo de Polling de Posición y Estado
        self.poll_thread = QThread()
        self.poller = PositionPoller(self.service.grbl, interval_ms=150, timeout_seconds=3.0)
        self.poller.moveToThread(self.poll_thread)

        self.poller.position_updated.connect(self._on_position_updated)
        self.poller.status_parsed.connect(self._on_status_parsed)
        self.poller.connection_status.connect(self._on_connection_status_poll)
        self.poller.conexion_perdida.connect(self._on_conexion_perdida)
        self.poll_thread.started.connect(self.poller.start_polling)

        # 2. Hilo de Jog Continuo
        self.jog_thread = QThread()
        self.jog_worker = JogWorker(self.service.grbl)
        self.jog_worker.moveToThread(self.jog_thread)
        self.jog_thread.start()

        # 3. Hilo de Sensor de Carga Arduino (Modo Automático)
        self.sensor_thread = QThread()
        self.sensor_worker = ArduinoSensorWorker(interval_ms=50)
        self.sensor_worker.moveToThread(self.sensor_thread)

        self.sensor_worker.connection_status.connect(self._on_sensor_conn_status)
        self.sensor_worker.object_detected.connect(self._on_sensor_object_detected)
        self.sensor_worker.heartbeat.connect(self._on_sensor_heartbeat)
        self.sensor_worker.raw_message.connect(self._on_sensor_raw_message)
        self.sensor_thread.start()

    def _handle_connect(self, port: str):
        if self.service.connect(port=port):
            # CAMBIO: Sincronizar la instancia activa de GRBLController con los workers
            # (ya que connect_grbl crea una nueva instancia de GRBLController en WarehouseManager)
            if hasattr(self, 'poller'):
                self.poller.grbl = self.service.grbl
                self.poller._last_success_time = time.time()
                QMetaObject.invokeMethod(self.poller, "start_polling", Qt.QueuedConnection)
            if hasattr(self, 'jog_worker'):
                self.jog_worker.grbl = self.service.grbl

            self.status_bar.update_connection(True, port=port)
            self.alarm_banner.ocultar("conexion")
            self.canvas_2d.set_limits(self.service.grbl_limits['X'], self.service.grbl_limits['Y'])
            if not self.poll_thread.isRunning():
                self.poll_thread.start()
            self.machine_state_str = "Listo" if self.origin_confirmed else "Sin referenciar"
            self.status_bar.update_machine_state("Idle")
            self.apply_state(self.machine_state_str)
        else:
            QMessageBox.critical(self, "Error de Conexión", f"No fue posible establecer conexión serial con GRBL en {port}.")

    def _handle_disconnect(self):
        # CAMBIO: Detener poller de forma segura invocando el slot en su propio hilo
        if hasattr(self, 'poller'):
            QMetaObject.invokeMethod(self.poller, "stop_polling", Qt.QueuedConnection)
        if hasattr(self, 'poll_thread') and self.poll_thread.isRunning():
            self.poll_thread.quit()
        self.service.disconnect()
        self.status_bar.update_connection(False)
        self.machine_state_str = "Desconectado"
        self.status_bar.update_machine_state("Desconectado")
        self.apply_state(self.machine_state_str)

    @Slot(str, str, float, float, float)
    def _on_status_parsed(self, state_str: str, pn_str: str, x: float, y: float, z: float):
        self.status_bar.update_machine_state(state_str)

        # Alarma GRBL
        if "Alarm" in state_str:
            self.alarm_banner.mostrar(
                "alarm",
                f"ALARMA GRBL DETECTADA ({state_str})",
                accion_btn_text="Desbloquear ($X)",
                accion_callback=lambda: self._unlock_and_clear_alarm()
            )
            self.machine_state_str = "Alarma"
        else:
            self.alarm_banner.ocultar("alarm")
            if state_str.startswith("Hold"):
                self.machine_state_str = "Pausa"
            elif "Run" in state_str:
                self.machine_state_str = "En movimiento"
            elif self.origin_confirmed:
                self.machine_state_str = "Listo"
            else:
                self.machine_state_str = "Sin referenciar"

        self.apply_state(self.machine_state_str)

    def _unlock_and_clear_alarm(self):
        if self.service.is_connected:
            self.service.grbl.send_command("$X")
            self.alarm_banner.ocultar("alarm")
            self.machine_state_str = "Listo" if self.origin_confirmed else "Sin referenciar"
            self.apply_state(self.machine_state_str)

    @Slot()
    def _on_conexion_perdida(self):
        logger.warning("Pérdida de conexión serial detectada.")
        # CAMBIO: Detener poller de forma segura invocando el slot en su propio hilo
        if hasattr(self, 'poller'):
            QMetaObject.invokeMethod(self.poller, "stop_polling", Qt.QueuedConnection)
        if hasattr(self, 'poll_thread') and self.poll_thread.isRunning():
            self.poll_thread.quit()
        self.service.disconnect()
        self.status_bar.update_connection(False)
        self.machine_state_str = "Desconectado"
        self.status_bar.update_machine_state("Desconectado")
        self._wiz_cancel()
        self.alarm_banner.mostrar(
            "conexion",
            "Pérdida de comunicación con GRBL (Timeout). Verifica el cable USB y presiona Conectar."
        )
        self.apply_state(self.machine_state_str)

    @Slot(float, float, float)
    def _on_position_updated(self, x: float, y: float, z: float):
        self.status_bar.update_coordinates(x, y, z)
        self.canvas_2d.set_position(x, y)
        # Actualizar marcador de carro en la malla
        status_dict = self.service.get_cell_status()
        self.cell_grid.update_cells(status_dict, x, y)

    @Slot(bool)
    def _on_connection_status_poll(self, connected: bool):
        # CAMBIO: Solo registrar o actualizar indicador si es necesario.
        # NO llamar a _handle_disconnect() aquí de forma inmediata:
        # si hay un delay momentáneo en la lectura de '?', PositionPoller
        # gestiona su propio timeout y disparará 'conexion_perdida'
        # si realmente se perdió la comunicación.
        pass

    # ── MODO AUTOMATICO Y SENSOR ARDUINO ──

    def _handle_sensor_connect(self, port: str):
        if hasattr(self, 'sensor_worker'):
            self.sensor_worker.req_connect.emit(port, 9600)

    def _handle_sensor_disconnect(self):
        if hasattr(self, 'sensor_worker'):
            self.sensor_worker.req_disconnect.emit()
        if self.auto_mode_active:
            self._toggle_auto_mode(False)

    @Slot(bool, str)
    def _on_sensor_conn_status(self, connected: bool, info: str):
        if connected:
            self.sensor_panel.set_connection_state(True, port=info)
            logger.info(f"Sensor Arduino conectado en {info}")
        else:
            self.sensor_panel.set_connection_state(False)
            if info:
                QMessageBox.warning(self, "Sensor Arduino", f"No se pudo conectar con el Arduino en el puerto {self.sensor_panel.port_combo.currentText()}.\nError: {info}")
            if self.auto_mode_active:
                self._toggle_auto_mode(False)

    @Slot()
    def _on_sensor_heartbeat(self):
        self.sensor_panel.pulse_heartbeat()

    @Slot(str)
    def _on_sensor_raw_message(self, msg: str):
        logger.debug(f"Arduino sensor: {msg}")

    @Slot(bool)
    def _on_sensor_object_detected(self, detected: bool):
        # 1. Actualizar el LED Virtual en la interfaz gráfica
        self.sensor_panel.set_detection_state(detected)

        # 2. Si el modo automático está activo y se detecta objeto:
        if detected and self.auto_mode_active:
            if self.is_auto_cycle_running:
                logger.info("Ciclo de almacenamiento automático ya en curso, ignorando detección duplicada.")
                return

            if not self.service.is_connected:
                QMessageBox.warning(self, "Atención", "GRBL no está conectado. Conecta y referencia antes de activar el almacenamiento.")
                self._toggle_auto_mode(False)
                return

            if not self.origin_confirmed:
                QMessageBox.warning(self, "Origen No Confirmado", "Debes confirmar el origen primero ('Zero Aquí G92') antes de operar en automático.")
                self._toggle_auto_mode(False)
                return

            empty_cell = self.service.get_first_empty_cell()
            if not empty_cell:
                self.alarm_banner.mostrar("almacen_lleno", "ALMACÉN LLENO: Todas las celdas están ocupadas. Retira un objeto para continuar.")
                self.sensor_panel.set_auto_mode_state(True, "ALMACEN LLENO: No hay celdas vacías disponibles.")
                return

            # Iniciar almacenamiento automático
            self._start_auto_deposit_cycle(empty_cell)

    def _toggle_auto_mode(self, activate: bool):
        if activate:
            if not self.sensor_panel.is_connected:
                QMessageBox.warning(self, "Sensor No Conectado", "Conecta primero el Arduino del sensor en el puerto COM correspondiente.")
                return
            if not self.service.is_connected:
                QMessageBox.warning(self, "GRBL No Conectado", "Conecta primero la placa de control GRBL.")
                return
            if not self.origin_confirmed:
                QMessageBox.warning(self, "Origen No Confirmado", "Referencia primero los ejes ('Zero Aquí G92' en Mantenimiento) antes de activar el modo automático.")
                return

            self.auto_mode_active = True
            self.sensor_panel.set_auto_mode_state(True, "MODO AUTOMATICO ACTIVO: Coloca un objeto en las palas para almacenarlo.")
            logger.info("Modo automático activado.")
        else:
            self.auto_mode_active = False
            self.sensor_panel.set_auto_mode_state(False)
            logger.info("Modo automático desactivado.")

    def _start_auto_deposit_cycle(self, cell_id: str):
        self.is_auto_cycle_running = True
        self.auto_stored_count += 1
        item_code = f"Caja_Auto_{self.auto_stored_count:02d}"

        self.sensor_panel.set_auto_mode_state(True, f"ALMACENANDO: Llevando objeto a celda {cell_id}...")
        self.apply_state("Ocupado")

        task = AutoDepositTask(self.service, cell_id, item_code)
        task.signals.step_changed.connect(self._on_auto_deposit_step)
        task.signals.finished.connect(self._on_auto_deposit_finished)
        QThreadPool.globalInstance().start(task)

    @Slot(int, str)
    def _on_auto_deposit_step(self, step_idx: int, step_desc: str):
        self.sensor_panel.set_auto_mode_state(True, f"AUTO ({step_idx}/7): {step_desc}")

    @Slot(bool, str, str)
    def _on_auto_deposit_finished(self, ok: bool, cell_id: str, item_code: str):
        self.is_auto_cycle_running = False
        # Resetear estado de detección para permitir la siguiente caja
        if hasattr(self, 'sensor_worker'):
            self.sensor_worker.req_reset.emit()

        if ok:
            logger.info(f"Ciclo automático completado: {item_code} almacenado en {cell_id}")
            self.refresh_dashboard()
            self._on_cell_selected(cell_id)
            if self.auto_mode_active:
                self.sensor_panel.set_auto_mode_state(
                    True,
                    f"¡{item_code} guardado en {cell_id}! Listo para el siguiente objeto."
                )
        else:
            logger.error(f"Fallo durante el ciclo de almacenamiento en {cell_id}")
            if self.auto_mode_active:
                self.sensor_panel.set_auto_mode_state(
                    True,
                    f"Atención: Movimiento interrumpido hacia {cell_id}."
                )

        self.apply_state("Listo" if self.origin_confirmed else "Sin referenciar")

    def closeEvent(self, event):
        """Cierre ordenado de hilos y puerto serial."""
        if hasattr(self, 'sensor_worker') and self.sensor_worker:
            self.sensor_worker.req_disconnect.emit()
        if hasattr(self, 'sensor_thread') and self.sensor_thread.isRunning():
            self.sensor_thread.quit()
            self.sensor_thread.wait(1000)
        if hasattr(self, 'poller'):
            QMetaObject.invokeMethod(self.poller, "stop_polling", Qt.QueuedConnection)
        if hasattr(self, 'poll_thread') and self.poll_thread.isRunning():
            self.poll_thread.quit()
        if hasattr(self, 'jog_thread') and self.jog_thread.isRunning():
            self.jog_thread.quit()
        self._cleanup_calib_thread()
        if self.service.is_connected:
            self.service.disconnect()
        event.accept()


# ═══════════════════════════════════════════════
# PUNTO DE ENTRADA PRINCIPAL
# ═══════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

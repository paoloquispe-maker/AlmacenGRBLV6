"""
Workers para hilos secundarios (QThread + QObject, patrón moveToThread).
Garantiza que la comunicación serial no congele la interfaz gráfica Qt.
"""

import time
import logging
import serial
import serial.tools.list_ports
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot, QTimer

from warehouse_grbl_system import GRBLController
from axis_calibrator import AxisCalibrator, CalibrationResult, CalibrationError

logger = logging.getLogger(__name__)


class PositionPoller(QObject):
    """
    Worker para polling de posición y estado GRBL en tiempo real (5-10 Hz).
    Detecta automáticamente pérdida de conexión serial (timeout).
    """
    position_updated = Signal(float, float, float)
    status_parsed = Signal(str, str, float, float, float)  # (state, pn, x, y, z)
    connection_status = Signal(bool)
    conexion_perdida = Signal()

    def __init__(self, grbl: GRBLController, interval_ms: int = 150, timeout_seconds: float = 3.0):
        super().__init__()
        self.grbl = grbl
        self.interval_ms = interval_ms
        self.timeout_seconds = timeout_seconds
        self._timer: Optional[QTimer] = None
        self._running = False
        self._last_success_time = time.time()

    @Slot()
    def start_polling(self):
        """Inicia el temporizador de polling en el hilo del worker."""
        if self._running:
            return
        self._running = True
        self._last_success_time = time.time()
        self._timer = QTimer()
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._poll_position)
        self._timer.start()
        logger.info("PositionPoller iniciado")

    @Slot()
    def stop_polling(self):
        """Detiene el temporizador de polling de manera segura dentro de su propio hilo."""
        self._running = False
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        logger.info("PositionPoller detenido")

    def _poll_position(self):
        """Lee el estado mediante comando '?' y emite señales de posición y estado."""
        if not self.grbl or not self.grbl.is_connected or not self.grbl.serial_conn:
            self.connection_status.emit(False)
            if time.time() - self._last_success_time > self.timeout_seconds:
                logger.warning("PositionPoller: Timeout de conexión (GRBL no conectado)")
                self.conexion_perdida.emit()
                self.stop_polling()
            return

        try:
            conn = self.grbl.serial_conn
            has_lock = hasattr(self.grbl, 'lock')
            if has_lock:
                self.grbl.lock.acquire()
            try:
                conn.write(b'?')
                time.sleep(0.04)

                if not conn.in_waiting:
                    if time.time() - self._last_success_time > self.timeout_seconds:
                        logger.warning("PositionPoller: Timeout sin respuesta en '?' de GRBL")
                        self.conexion_perdida.emit()
                        self.stop_polling()
                    return

                raw = conn.read(conn.in_waiting).decode(errors='replace').strip()
            finally:
                if has_lock:
                    self.grbl.lock.release()

            if '<' in raw and '>' in raw:
                self._last_success_time = time.time()
                self.connection_status.emit(True)

                body = raw[raw.find('<')+1 : raw.find('>')]
                parts = body.split('|')
                state_str = parts[0]
                pn_str = ""
                pos_x, pos_y, pos_z = 0.0, 0.0, 0.0

                for p in parts[1:]:
                    if p.startswith('MPos:') or p.startswith('WPos:'):
                        try:
                            coords = p.split(':')[1].split(',')
                            pos_x, pos_y, pos_z = float(coords[0]), float(coords[1]), float(coords[2])
                        except (ValueError, IndexError):
                            pass
                    elif p.startswith('Pn:'):
                        pn_str = p[3:]

                self.position_updated.emit(pos_x, pos_y, pos_z)
                self.status_parsed.emit(state_str, pn_str, pos_x, pos_y, pos_z)

            elif time.time() - self._last_success_time > self.timeout_seconds:
                logger.warning("PositionPoller: Respuesta corrupta o sin respuesta válida de GRBL")
                self.conexion_perdida.emit()
                self.stop_polling()

        except Exception as e:
            logger.error(f"Error en PositionPoller: {e}")
            if time.time() - self._last_success_time > self.timeout_seconds:
                self.conexion_perdida.emit()
                self.stop_polling()


class JogWorker(QObject):
    """
    Worker para movimiento de jog continuo por teclado o botones.
    Envía $J=G91 G21... al presionar y byte 0x85 al soltar.
    """
    jog_error = Signal(str)

    def __init__(self, grbl: GRBLController):
        super().__init__()
        self.grbl = grbl
        self._is_jogging = False

    @Slot(str, int, float)
    def start_jog(self, axis: str, direction: int, speed: float):
        """
        Inicia un jog continuo hacia un eje y dirección (+1 o -1).

        Args:
            axis: 'X', 'Y', o 'Z'
            direction: 1 o -1
            speed: Velocidad en mm/min
        """
        if not self.grbl or not self.grbl.is_connected or not self.grbl.serial_conn:
            self.jog_error.emit("GRBL no está conectado")
            return

        # Si ya hay un jog en curso sin cancelar, ignorar comando duplicado
        if self._is_jogging:
            return

        dist = 500.0 * (1 if direction > 0 else -1)
        cmd = f"$J=G91 G21 {axis.upper()}{dist:.1f} F{speed:.0f}"
        
        try:
            self.grbl.send_command(cmd, wait_ok=False)
            self._is_jogging = True
            logger.debug(f"Jog iniciado: {cmd}")
        except Exception as e:
            self.jog_error.emit(f"Error al iniciar jog: {e}")

    @Slot()
    def stop_jog(self):
        """
        Envía inmediatamente el byte de cancelación de jog 0x85 a la placa GRBL.
        """
        if not self.grbl or not self.grbl.is_connected or not self.grbl.serial_conn:
            self._is_jogging = False
            return

        try:
            # Cancelación inmediata de jog en tiempo real en GRBL 1.1 protegida por lock
            if hasattr(self.grbl, 'jog_cancel'):
                self.grbl.jog_cancel()
            else:
                self.grbl.serial_conn.write(bytes([0x85]))
            self._is_jogging = False
            logger.debug("Jog cancelado con byte 0x85")
        except Exception as e:
            self.jog_error.emit(f"Error al cancelar jog: {e}")
            self._is_jogging = False


class CalibrationWorker(QObject):
    """
    Worker para orquestar la rutina de AxisCalibrator en hilo secundario.
    """
    progress = Signal(str)
    extremos_found = Signal(float)
    punto_manual_captured = Signal(int, float)  # (point_index 1 or 2, position_mm)
    calibration_completed = Signal(object)  # Emite CalibrationResult
    error_occurred = Signal(str)

    def __init__(self, grbl: GRBLController):
        super().__init__()
        self.grbl = grbl
        self.calibrator: Optional[AxisCalibrator] = None

    @Slot(str, int)
    def do_capturar_punto_manual(self, axis: str, point_index: int):
        """Captura manualmente la posición actual como extremo 1 o 2."""
        if not self.grbl or not self.grbl.is_connected:
            self.error_occurred.emit("GRBL no está conectado")
            return

        try:
            if not self.calibrator:
                self.calibrator = AxisCalibrator(self.grbl)
            pos = self.calibrator.capturar_punto_manual(axis)
            self.punto_manual_captured.emit(point_index, pos)
        except CalibrationError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"Error inesperado al capturar posición: {e}")

    @Slot(str)
    def do_buscar_extremos(self, axis: str):
        """Paso 1: Búsqueda de extremos."""
        if not self.grbl or not self.grbl.is_connected:
            self.error_occurred.emit("GRBL no está conectado")
            return

        try:
            self.calibrator = AxisCalibrator(self.grbl)
            dist_rep = self.calibrator.buscar_extremos(
                axis=axis,
                on_progress=lambda msg: self.progress.emit(msg)
            )
            self.extremos_found.emit(dist_rep)
        except CalibrationError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"Error inesperado: {e}")

    @Slot(str, float, float)
    def do_aplicar_calibracion(self, axis: str, dist_rep: float, dist_real: float):
        """Paso 2: Aplicar calibración."""
        if not self.calibrator:
            self.error_occurred.emit("No se ha iniciado la calibración")
            return

        try:
            result = self.calibrator.aplicar_calibracion(
                axis=axis,
                distancia_reportada=dist_rep,
                distancia_real_mm=dist_real,
                on_progress=lambda msg: self.progress.emit(msg)
            )
            self.calibration_completed.emit(result)
        except CalibrationError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"Error inesperado: {e}")

    @Slot()
    def do_cancelar(self):
        """Cancela el proceso de calibración."""
        if self.calibrator:
            try:
                self.calibrator.cancelar(on_progress=lambda msg: self.progress.emit(msg))
            except Exception as e:
                logger.warning(f"Error al cancelar calibración: {e}")
            self.progress.emit("Calibración cancelada.")


class ArduinoSensorWorker(QObject):
    """
    Worker para comunicación serie con Arduino (sensor ultrasónico / óptico de carga).
    Monitorea en tiempo real sin bloquear la interfaz gráfica Qt.
    """
    connection_status = Signal(bool, str)       # (conectado, puerto/error)
    sensor_ready = Signal()                     # Emitido cuando Arduino reporta SENSOR_READY
    object_detected = Signal(bool)              # True = objeto detectado, False = sin objeto
    heartbeat = Signal()                        # Pulso de datos recibidos (comunicación viva)
    raw_message = Signal(str)                   # Mensaje crudo recibido

    # Señales para invocación segura entre hilos (thread-safe requests)
    req_connect = Signal(str, int)
    req_disconnect = Signal()
    req_reset = Signal()

    def __init__(self, interval_ms: int = 50):
        super().__init__()
        self.interval_ms = interval_ms
        self.serial_conn: Optional[serial.Serial] = None
        self._timer: Optional[QTimer] = None
        self._running = False
        self.port = ""
        self.baudrate = 9600
        self.last_detection_state = False
        self.is_ready = False
        self.last_heartbeat_time = 0.0

        # Conectar señales de solicitud a los slots del worker
        self.req_connect.connect(self.connect_arduino)
        self.req_disconnect.connect(self.disconnect_arduino)
        self.req_reset.connect(self.reset_detection_state)

    @Slot(str, int)
    def connect_arduino(self, port: str, baudrate: int = 9600):
        """Abre la conexión serial con el Arduino."""
        self.port = port
        self.baudrate = baudrate
        self.disconnect_arduino()

        try:
            self.serial_conn = serial.Serial(port, baudrate, timeout=0.1)
            time.sleep(1.5)  # Esperar auto-reset de Arduino
            self.serial_conn.reset_input_buffer()
            self._running = True
            self.last_heartbeat_time = time.time()

            if self._timer is None:
                self._timer = QTimer()
                self._timer.setInterval(self.interval_ms)
                self._timer.timeout.connect(self._read_serial)
            self._timer.start()

            logger.info(f"Arduino sensor conectado en {port} ({baudrate} baud)")
            self.connection_status.emit(True, port)

        except Exception as e:
            logger.error(f"Error conectando con Arduino en {port}: {e}")
            self.serial_conn = None
            self._running = False
            self.connection_status.emit(False, str(e))

    @Slot()
    def disconnect_arduino(self):
        """Cierra la conexión con el Arduino de forma segura."""
        self._running = False
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

        if self.serial_conn:
            try:
                if self.serial_conn.is_open:
                    self.serial_conn.close()
            except Exception as e:
                logger.warning(f"Error cerrando puerto Arduino: {e}")
            self.serial_conn = None

        self.last_detection_state = False
        self.is_ready = False
        logger.info("Arduino sensor desconectado")
        self.connection_status.emit(False, "")

    @Slot()
    def reset_detection_state(self):
        """Reinicia el estado de detección tras procesar un objeto."""
        self.last_detection_state = False
        self.object_detected.emit(False)

    def _read_serial(self):
        """Lee los mensajes entrantes del Arduino."""
        if not self.serial_conn or not self.serial_conn.is_open:
            return

        try:
            while self.serial_conn.in_waiting:
                line = self.serial_conn.readline().decode(errors='replace').strip()
                if not line:
                    continue

                self.last_heartbeat_time = time.time()
                self.heartbeat.emit()
                self.raw_message.emit(line)

                if "SENSOR_READY" in line:
                    self.is_ready = True
                    self.sensor_ready.emit()

                if "OBJETO_DETECTADO" in line or "OBJETO:1" in line:
                    if not self.last_detection_state:
                        self.last_detection_state = True
                        self.object_detected.emit(True)

                elif "SIN_OBJETO" in line or "OBJETO:0" in line:
                    if self.last_detection_state:
                        self.last_detection_state = False
                        self.object_detected.emit(False)

        except Exception as e:
            logger.error(f"Error de lectura serial Arduino: {e}")
            self.disconnect_arduino()


# CAMBIO: Worker para ejecutar la secuencia "Ir a Posición de Carga" en hilo secundario.
# Evita congelar la UI durante los movimientos G0 (que son síncronos en GRBLController.move).
class GoPosWorker(QObject):
    """
    Ejecuta la secuencia de soft-homing sin finales de carrera en hilo secundario:
      1. $X  → desbloquear alarma
      2. G92 X0 Y0 Z0 → fijar origen en posición actual
      3. ir_a_posicion_carga() → Y=120, Z=0, X=0 Y=0
    Emite step_progress(paso, texto) para actualizar la UI en tiempo real.
    Emite finished(ok) al terminar (True = éxito, False = error).
    """
    step_progress = Signal(int, str)   # (número de paso, descripción)
    finished      = Signal(bool)       # True = completado sin error

    def __init__(self, service):
        super().__init__()
        self._service = service

    @Slot()
    def run(self):
        try:
            grbl = self._service.grbl
            if not self._service.is_connected or not grbl:
                logger.error("GoPosWorker: GRBL no conectado")
                self.finished.emit(False)
                return

            # Paso 1: Desbloquear alarma
            self.step_progress.emit(1, "Desbloqueando alarma ($X)...")
            grbl.send_command("$X")

            # Paso 2: Fijar origen en posición actual
            self.step_progress.emit(2, "Fijando origen de trabajo (G92 X0 Y0 Z0)...")
            if not grbl.send_command("G92 X0 Y0 Z0"):
                logger.error("GoPosWorker: G92 no aceptado por GRBL")
                self.finished.emit(False)
                return

            # Paso 3: Mover a posición de carga usando la secuencia de service_layer
            ok = self._service.ir_a_posicion_carga(
                on_step=lambda n, txt: self.step_progress.emit(n + 2, txt)
            )
            self.finished.emit(ok)

        except Exception as e:
            logger.error(f"GoPosWorker: excepción inesperada: {e}")
            self.finished.emit(False)


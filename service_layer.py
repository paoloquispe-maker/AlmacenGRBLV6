"""
Capa de servicio que envuelve WarehouseManager, GRBLController, AxisCalibrator e HistoryDB.
Proporciona la lógica de negocio central unificada para la GUI.
"""

import logging
from typing import Dict, List, Optional, Tuple, Any

from warehouse_grbl_system import WarehouseManager, CellStatus
from history_db import HistoryDB
from grid_config import load_config, save_config, validate_coords_against_grbl

logger = logging.getLogger(__name__)


class WarehouseService:
    """Servicio principal para operaciones del almacén y hardware."""

    def __init__(self, config_file: str = "warehouse_config.json", state_file: str = "warehouse_state.json"):
        self.config_file = config_file
        self.state_file = state_file
        self.warehouse = WarehouseManager(config_file=config_file, state_file=state_file)
        self.history_db = HistoryDB()

        # Cache de límites GRBL ($130, $131, $132) por defecto
        self.grbl_limits: Dict[str, float] = {'X': 360.0, 'Y': 290.0, 'Z': 150.0}

    @property
    def grbl(self):
        return self.warehouse.grbl

    @property
    def is_connected(self) -> bool:
        return self.grbl is not None and self.grbl.is_connected

    def connect(self, port: str = "COM6", baudrate: int = 115200) -> bool:
        """Conecta con la placa GRBL y actualiza los límites."""
        success = self.warehouse.connect_grbl(port=port, baudrate=baudrate)
        if success:
            self.refresh_grbl_limits()
        return success

    def disconnect(self) -> None:
        """Desconecta la placa GRBL."""
        self.warehouse.disconnect_grbl()

    def refresh_grbl_limits(self) -> Dict[str, float]:
        """Obtiene los límites reales $130, $131, $132 enviando $$ a GRBL."""
        if not self.is_connected or not self.grbl.serial_conn:
            return self.grbl_limits

        try:
            self.grbl.serial_conn.reset_input_buffer()
            self.grbl.serial_conn.write(b'$$\n')
            import time
            time.sleep(0.2)

            buffer = ""
            start = time.time()
            while time.time() - start < 2.0:
                if self.grbl.serial_conn.in_waiting:
                    buffer += self.grbl.serial_conn.read(self.grbl.serial_conn.in_waiting).decode(errors='ignore')
                    if 'ok' in buffer:
                        break
                time.sleep(0.05)

            for line in buffer.splitlines():
                line = line.strip()
                if line.startswith("$130="):
                    self.grbl_limits['X'] = float(line.split('=')[1])
                elif line.startswith("$131="):
                    self.grbl_limits['Y'] = float(line.split('=')[1])
                elif line.startswith("$132="):
                    self.grbl_limits['Z'] = float(line.split('=')[1])

            logger.info(f"Límites GRBL actualizados: {self.grbl_limits}")
        except Exception as e:
            logger.warning(f"No se pudieron leer los límites de GRBL: {e}")

        return self.grbl_limits

    def get_cell_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Devuelve el estado de las celdas consolidado desde SQLite cells_state,
        combinado con las coordenadas físicas de warehouse_config.json.
        """
        sqlite_states = self.history_db.get_all_cell_states()
        res = {}
        for cid, cell in self.warehouse.cells.items():
            db_info = sqlite_states.get(cid, {})
            st = db_info.get("status") or cell.status.value
            cnt = db_info.get("content") if "content" in db_info else cell.content
            res[cid] = {
                'status': st,
                'content': cnt,
                'x': cell.x,
                'y': cell.y,
                'z_safe': cell.z_safe,
                'z_pickup': cell.z_pickup,
                'coordinates': {'x': cell.x, 'y': cell.y}
            }
        return res

    def registrar_entrada(self, cell_id: str, item_code: str, origen: str = "MANUAL", notas: Optional[str] = None) -> bool:
        """Registra la entrada de un objeto en una celda y actualiza SQLite atómicamente."""
        if cell_id not in self.warehouse.cells:
            return False

        cell = self.warehouse.cells[cell_id]
        cell.status = CellStatus.OCCUPIED
        cell.content = item_code

        self.history_db.record_cell_movement_tx(
            cell_id=cell_id,
            movement_type="ENTRADA",
            status="occupied",
            content=item_code,
            origen=origen,
            notas=notas or f"Entrada manual de {item_code}"
        )
        return True

    def registrar_salida(self, cell_id: str, origen: str = "MANUAL", notas: Optional[str] = None) -> bool:
        """Registra la salida de un objeto de una celda y actualiza SQLite atómicamente."""
        if cell_id not in self.warehouse.cells:
            return False

        cell = self.warehouse.cells[cell_id]
        prev_item = cell.content or "Objeto"
        cell.status = CellStatus.EMPTY
        cell.content = None

        self.history_db.record_cell_movement_tx(
            cell_id=cell_id,
            movement_type="SALIDA",
            status="empty",
            content=None,
            origen=origen,
            notas=notas or f"Salida manual de {prev_item}"
        )
        return True

    def marcar_reservada(self, cell_id: str, notas: Optional[str] = None) -> bool:
        """Marca una celda como RESERVADA y actualiza SQLite atómicamente."""
        if cell_id not in self.warehouse.cells:
            return False

        cell = self.warehouse.cells[cell_id]
        cell.status = CellStatus.RESERVED
        cell.content = "Reservado"

        self.history_db.record_cell_movement_tx(
            cell_id=cell_id,
            movement_type="RESERVA",
            status="reserved",
            content="Reservado",
            origen="MANUAL",
            notas=notas or "Celda marcada como reservada"
        )
        return True

    def vaciar_todo(self) -> None:
        """Vacía todas las celdas del almacén atómicamente en SQLite."""
        current_states = self.history_db.get_all_cell_states()
        for cell_id, cell in self.warehouse.cells.items():
            db_info = current_states.get(cell_id, {})
            st = db_info.get("status", cell.status.value)
            if st != "empty":
                prev_item = db_info.get("content") or cell.content or "Objeto"
                cell.status = CellStatus.EMPTY
                cell.content = None
                self.history_db.record_cell_movement_tx(
                    cell_id=cell_id,
                    movement_type="SALIDA",
                    status="empty",
                    content=None,
                    origen="MANUAL",
                    notas="Vaciado general de almacén"
                )

    def reload_config(self) -> None:
        """Vuelve a cargar las celdas en el WarehouseManager tras cambios en la configuración."""
        self.warehouse._load_configuration()
        self.warehouse._load_state()

    def move_to_cell(self, cell_id: str) -> bool:
        """Realiza el movimiento seguro a una celda utilizando GRBLController.move()."""
        if not self.is_connected:
            return False

        if cell_id not in self.warehouse.cells:
            return False

        cell = self.warehouse.cells[cell_id]
        # Primero elevar a altura de transporte segura
        if not self.grbl.move("G0 Y120"):
            return False

        # Mover a las coordenadas X, Y de la celda
        return self.grbl.move(f"G0 X{cell.x} Y{cell.y}")

    def get_first_empty_cell(self) -> Optional[str]:
        """Retorna el ID de la primera celda vacía o None si el almacén está lleno."""
        status = self.get_cell_status()
        for cid, info in sorted(status.items()):
            if info.get("status") == "empty":
                return cid
        return None

    def ir_a_posicion_carga(self, on_step: Optional[Callable[[int, str], None]] = None) -> bool:
        """Regresa el carro a la posición de carga inicial (X0 Y0 Z0) con elevación previa."""
        if not self.is_connected or not self.grbl:
            return False
        if on_step: on_step(1, "Elevando a altura de transporte segura (Y=120)...")
        if not self.grbl.move("G0 Y120"):
            return False
        if on_step: on_step(2, "Retrayendo palas (Z=0)...")
        if not self.grbl.move("G0 Z0"):
            return False
        if on_step: on_step(3, "Retornando a origen de carga (X0 Y0)...")
        return self.grbl.move("G0 X0 Y0")

    def depositar_objeto_en_celda(self, cell_id: str, on_step: Optional[Callable[[int, str], None]] = None) -> bool:
        """
        Secuencia mecánica completa para depositar un objeto en una celda física:
        1. Y a altura de transporte segura (Y=120)
        2. Mover XY a la posición de la celda
        3. Subir Y un offset (Y + 10) para preparar depósito
        4. Extender palas Z (Z=100)
        5. Bajar Y (Y=cell.y) posando el objeto en la celda
        6. Retraer palas Z (Z=0)
        7. Y a transporte y retorno a posición de carga inicial (X0 Y0 Z0)
        """
        if not self.is_connected or not self.grbl:
            return False

        if cell_id not in self.warehouse.cells:
            return False

        cell = self.warehouse.cells[cell_id]
        y_transporte = 120.0
        z_retraido = 0.0
        z_extendido = 100.0
        y_offset_depositar = 10.0

        if on_step: on_step(1, "Elevando carro a altura de transporte (Y=120)...")
        if not self.grbl.move(f"G0 Y{y_transporte}"):
            return False

        if on_step: on_step(2, f"Moviendo a celda {cell_id} (X={cell.x}, Y={cell.y})...")
        if not self.grbl.move(f"G0 X{cell.x} Y{cell.y}"):
            return False

        if on_step: on_step(3, f"Alineando altura para depósito (Y={cell.y + y_offset_depositar})...")
        if not self.grbl.move(f"G0 Y{cell.y + y_offset_depositar}"):
            return False

        if on_step: on_step(4, f"Extendiendo palas hacia la celda (Z={z_extendido})...")
        if not self.grbl.move(f"G0 Z{z_extendido}"):
            return False

        if on_step: on_step(5, f"Posando objeto en repisa (Y={cell.y})...")
        if not self.grbl.move(f"G0 Y{cell.y}"):
            return False

        if on_step: on_step(6, f"Retrayendo palas (Z={z_retraido})...")
        if not self.grbl.move(f"G0 Z{z_retraido}"):
            return False

        if on_step: on_step(7, "Retornando carro a posición de carga inicial...")
        if not self.grbl.move(f"G0 Y{y_transporte}"):
            return False
        if not self.grbl.move("G0 X0 Y0"):
            return False

        return True

    def recoger_objeto_de_celda(self, cell_id: str, on_step: Optional[Callable[[int, str], None]] = None) -> bool:
        """
        Secuencia mecánica completa para recoger un objeto de una celda física:
        1. Y a altura de transporte (Y=120)
        2. Mover XY a la celda
        3. Extender palas Z (Z=100) debajo del objeto
        4. Subir Y un offset (Y + 10) para asegurar la carga
        5. Retraer palas Z (Z=0) con el objeto
        6. Y a transporte y retorno a posición de carga (X0 Y0)
        """
        if not self.is_connected or not self.grbl:
            return False

        if cell_id not in self.warehouse.cells:
            return False

        cell = self.warehouse.cells[cell_id]
        y_transporte = 120.0
        z_retraido = 0.0
        z_extendido = 100.0
        y_offset_asegurar = 10.0

        if on_step: on_step(1, "Elevando carro a altura de transporte...")
        if not self.grbl.move(f"G0 Y{y_transporte}"):
            return False

        if on_step: on_step(2, f"Moviendo a celda {cell_id} (X={cell.x}, Y={cell.y})...")
        if not self.grbl.move(f"G0 X{cell.x} Y{cell.y}"):
            return False

        if on_step: on_step(3, f"Extendiendo palas (Z={z_extendido})...")
        if not self.grbl.move(f"G0 Z{z_extendido}"):
            return False

        if on_step: on_step(4, f"Elevando para asegurar carga (Y={cell.y + y_offset_asegurar})...")
        if not self.grbl.move(f"G0 Y{cell.y + y_offset_asegurar}"):
            return False

        if on_step: on_step(5, f"Retrayendo palas con la carga (Z={z_retraido})...")
        if not self.grbl.move(f"G0 Z{z_retraido}"):
            return False

        if on_step: on_step(6, "Regresando a posición de carga (X0 Y0)...")
        if not self.grbl.move(f"G0 Y{y_transporte}"):
            return False
        if not self.grbl.move("G0 X0 Y0"):
            return False

        return True

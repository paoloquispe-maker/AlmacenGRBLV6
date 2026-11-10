"""
Sistema de Control de Almacén Automático 2x2 con GRBL
Controla un rack de almacenamiento mediante Annoy Tools + GRBL
Versión: 2.0 - Configuración desde cero
"""

import serial
import time
import json
import logging
import threading
from typing import Dict, Optional, List
from dataclasses import dataclass
from enum import Enum

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CellStatus(Enum):
    """Estados posibles de una celda"""
    EMPTY = "empty"
    OCCUPIED = "occupied"
    RESERVED = "reserved"


@dataclass
class Cell:
    """Representa una celda del almacén"""
    id: str
    x: float
    y: float
    z_safe: float
    z_pickup: float
    status: CellStatus
    content: Optional[str] = None


class GRBLController:
    """Controlador de comunicación con GRBL"""
    
    def __init__(self, port: str = 'COM6', baudrate: int = 115200, timeout: float = 2.0):
        """
        Inicializa la conexión con GRBL
        
        Args:
            port: Puerto serial del Annoy Tools
            baudrate: Velocidad de comunicación
            timeout: Tiempo máximo de espera por respuesta
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn: Optional[serial.Serial] = None
        self.is_connected = False
        self.lock = threading.Lock()
        
    def connect(self) -> bool:
        """Establece conexión con GRBL"""
        try:
            self.serial_conn = serial.Serial(
                self.port, 
                self.baudrate, 
                timeout=self.timeout
            )
            time.sleep(2)  # Esperar reset de Arduino
            
            # Limpiar buffer inicial
            self.serial_conn.reset_input_buffer()
            
            # Leer mensaje de bienvenida de GRBL
            welcome = self.serial_conn.read_until(b'\n').decode().strip()
            logger.info(f"GRBL conectado: {welcome}")
            
            self.is_connected = True
            return True
            
        except serial.SerialException as e:
            logger.error(f"Error al conectar con GRBL: {e}")
            self.is_connected = False
            return False
    
    def disconnect(self):
        """Cierra la conexión serial"""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            logger.info("Conexión GRBL cerrada")
        self.is_connected = False
    
    def send_command(self, command: str, wait_ok: bool = True) -> bool:
        """
        Envía un comando G-code a GRBL
        
        Args:
            command: Comando G-code (ej: "G0 X50 Y100")
            wait_ok: Si debe esperar confirmación "ok"
            
        Returns:
            True si el comando fue exitoso
        """
        if not self.is_connected or not self.serial_conn:
            logger.error("No hay conexión con GRBL")
            return False
        
        with self.lock:
            try:
                # Enviar comando
                cmd = command.strip() + '\n'
                self.serial_conn.write(cmd.encode())
                logger.debug(f"Enviado: {command}")
                
                if wait_ok:
                    # Esperar respuesta "ok"
                    response = self.serial_conn.read_until(b'ok\r\n').decode().strip()
                    
                    if 'ok' in response:
                        logger.debug(f"Respuesta: {response}")
                        return True
                    elif 'error' in response:
                        logger.error(f"GRBL error: {response}")
                        return False
                    else:
                        logger.warning(f"Respuesta inesperada: {response}")
                        return False
                
                return True
                
            except serial.SerialException as e:
                logger.error(f"Error de comunicación: {e}")
                return False
            except Exception as e:
                logger.error(f"Error inesperado: {e}")
                return False
    
    def get_position(self) -> Optional[Dict[str, float]]:
        """Obtiene la posición actual (comando ?)"""
        if not self.is_connected or not self.serial_conn:
            return None
        
        with self.lock:
            try:
                self.serial_conn.write(b'?\n')
                response = self.serial_conn.read_until(b'\r\n').decode().strip()
                
                # Parsear respuesta tipo: <Idle|MPos:0.000,0.000,0.000|...>
                if 'MPos:' in response:
                    pos_str = response.split('MPos:')[1].split('|')[0]
                    x, y, z = map(float, pos_str.split(','))
                    return {'x': x, 'y': y, 'z': z}
                
                return None
                
            except Exception as e:
                logger.error(f"Error al obtener posición: {e}")
                return None
    
    def emergency_stop(self):
        """Detención de emergencia (soft reset)"""
        if self.serial_conn:
            with self.lock:
                try:
                    self.serial_conn.write(b'\x18')  # Ctrl-X
                    logger.warning("EMERGENCY STOP ejecutado")
                except Exception as e:
                    logger.error(f"Error en emergency_stop: {e}")

    def feed_hold(self) -> bool:
        """Pausa inmediata del movimiento (comando en tiempo real !)"""
        if self.serial_conn:
            with self.lock:
                try:
                    self.serial_conn.write(b'!')
                    logger.info("Feed hold enviado (!)")
                    return True
                except Exception as e:
                    logger.error(f"Error enviando feed hold (!): {e}")
                    return False
        return False

    def cycle_start(self) -> bool:
        """Reanudación de movimiento (comando en tiempo real ~)"""
        if self.serial_conn:
            with self.lock:
                try:
                    self.serial_conn.write(b'~')
                    logger.info("Cycle start enviado (~)")
                    return True
                except Exception as e:
                    logger.error(f"Error enviando cycle start (~): {e}")
                    return False
        return False

    def jog_cancel(self) -> bool:
        """Cancela jog en tiempo real en GRBL 1.1 (byte 0x85)"""
        if self.serial_conn:
            with self.lock:
                try:
                    self.serial_conn.write(bytes([0x85]))
                    logger.debug("Jog cancel enviado (0x85)")
                    return True
                except Exception as e:
                    logger.error(f"Error enviando jog cancel (0x85): {e}")
                    return False
        return False
    
    def wait_idle(self, timeout: float = 30.0) -> bool:
        """
        Espera hasta que GRBL reporte estado Idle
        
        Args:
            timeout: Tiempo máximo de espera en segundos
            
        Returns:
            True si GRBL reportó Idle, False si timeout
        """
        if not self.is_connected or not self.serial_conn:
            return False
        
        start = time.time()
        while time.time() - start < timeout:
            try:
                self.serial_conn.write(b'?')
                time.sleep(0.1)
                
                if self.serial_conn.in_waiting:
                    response = self.serial_conn.read(self.serial_conn.in_waiting).decode().strip()
                    
                    if '<Idle' in response:
                        return True
                    elif '<Alarm' in response:
                        logger.error(f"GRBL en estado de alarma: {response}")
                        return False
                
                time.sleep(0.15)
                
            except Exception as e:
                logger.error(f"Error al esperar Idle: {e}")
                return False
        
        logger.warning(f"Timeout esperando Idle ({timeout}s)")
        return False
    
    def move(self, gcode: str, timeout: float = 30.0) -> bool:
        """
        Envía un comando de movimiento y espera a que termine
        
        Args:
            gcode: Comando G-code de movimiento (ej: "G0 X50 Y100")
            timeout: Tiempo máximo de espera
            
        Returns:
            True si el movimiento fue exitoso y GRBL volvió a Idle
        """
        if self.send_command(gcode):
            return self.wait_idle(timeout)
        return False


class WarehouseManager:
    """Gestor del almacén y su estado"""
    
    def __init__(self, config_file: str = 'warehouse_config.json', 
                 state_file: str = 'warehouse_state.json'):
        """
        Inicializa el gestor del almacén
        
        Args:
            config_file: Archivo JSON con configuración de celdas
            state_file: Archivo JSON con estado actual del almacén
        """
        self.config_file = config_file
        self.state_file = state_file
        self.cells: Dict[str, Cell] = {}
        self.grbl = None
        
        self._load_configuration()
        self._load_state()
    
    def _load_configuration(self):
        """Carga la configuración de celdas desde JSON"""
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
            
            for cell_id, cell_data in config['cells'].items():
                self.cells[cell_id] = Cell(
                    id=cell_id,
                    x=cell_data['x'],
                    y=cell_data['y'],
                    z_safe=cell_data['z_safe'],
                    z_pickup=cell_data['z_pickup'],
                    status=CellStatus.EMPTY
                )
            
            logger.info(f"Configuración cargada: {len(self.cells)} celdas")
            
        except FileNotFoundError:
            logger.warning(f"Archivo {self.config_file} no encontrado, creando configuración por defecto")
            self._create_default_configuration()
    
    def _create_default_configuration(self):
        """Crea una configuración por defecto 2x2"""
        config = {
            'cells': {
                'A1': {'x': 140.0, 'y': 0.0, 'z_safe': 120.0, 'z_pickup': 0.0},
                'A2': {'x': 210.0, 'y': 0.0, 'z_safe': 120.0, 'z_pickup': 0.0},
                'B1': {'x': 140.0, 'y': 110.0, 'z_safe': 120.0, 'z_pickup': 0.0},
                'B2': {'x': 210.0, 'y': 110.0, 'z_safe': 120.0, 'z_pickup': 0.0}
            }
        }
        
        for cell_id, cell_data in config['cells'].items():
            self.cells[cell_id] = Cell(
                id=cell_id,
                x=cell_data['x'],
                y=cell_data['y'],
                z_safe=cell_data['z_safe'],
                z_pickup=cell_data['z_pickup'],
                status=CellStatus.EMPTY
            )
        
        # Guardar configuración por defecto
        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        logger.info("Configuración por defecto 2x2 creada")
    
    def _load_state(self):
        """Carga el estado del almacén desde JSON"""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            for cell_id, cell_state in state['cells'].items():
                if cell_id in self.cells:
                    self.cells[cell_id].status = CellStatus(cell_state['status'])
                    self.cells[cell_id].content = cell_state.get('content')
            
            logger.info("Estado del almacén cargado")
            
        except FileNotFoundError:
            logger.info("No se encontró estado previo, almacén vacío")
            self._save_state()
    
    def _save_state(self):
        """Guarda el estado actual del almacén"""
        state = {
            'cells': {
                cell_id: {
                    'status': cell.status.value,
                    'content': cell.content
                }
                for cell_id, cell in self.cells.items()
            }
        }
        
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
        
        logger.debug("Estado guardado")
    
    def connect_grbl(self, port: str = 'COM6', baudrate: int = 115200) -> bool:
        """Conecta con el controlador GRBL"""
        self.grbl = GRBLController(port, baudrate)
        return self.grbl.connect()
    
    def disconnect_grbl(self):
        """Desconecta GRBL"""
        if self.grbl:
            self.grbl.disconnect()
    
    def get_empty_cells(self) -> List[str]:
        """Retorna lista de celdas vacías"""
        return [
            cell_id for cell_id, cell in self.cells.items() 
            if cell.status == CellStatus.EMPTY
        ]
    
    def get_status(self) -> Dict:
        """Retorna el estado actual del almacén"""
        return {
            cell_id: {
                'status': cell.status.value,
                'content': cell.content,
                'coordinates': {'x': cell.x, 'y': cell.y}
            }
            for cell_id, cell in self.cells.items()
        }


if __name__ == "__main__":
    print("Sistema de Almacén Automático 2x2 - Módulo Base")
    print("Este archivo no se ejecuta directamente.")
    print("Usa main.py o almacen_automatico.py")
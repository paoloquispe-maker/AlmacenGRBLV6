"""
Módulo para generación automática de grid y validación de coordenadas contra límites GRBL.
"""

import json
import logging
from typing import Dict, Tuple, Optional, Any

logger = logging.getLogger(__name__)

CONFIG_FILE = "warehouse_config.json"


def load_config(filename: str = CONFIG_FILE) -> Dict[str, Any]:
    """
    Carga la configuración del almacén desde JSON.

    Soporta tanto el formato heredado (solo "cells") como el extendido (con "grid" y "manual").
    """
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        if "cells" not in config:
            config["cells"] = {}
        if "grid" not in config:
            config["grid"] = {"rows": 2, "cols": 2, "x0": 140.0, "y0": 0.0, "dx": 70.0, "dy": 110.0}

        # Asegurar atributo 'manual' en cada celda
        for cell_data in config["cells"].values():
            if "manual" not in cell_data:
                cell_data["manual"] = False
            if "z_safe" not in cell_data:
                cell_data["z_safe"] = 120.0
            if "z_pickup" not in cell_data:
                cell_data["z_pickup"] = 0.0

        return config
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Error al cargar {filename} ({e}). Creando estructura por defecto.")
        default_config = {
            "grid": {"rows": 2, "cols": 2, "x0": 140.0, "y0": 0.0, "dx": 70.0, "dy": 110.0},
            "cells": {
                "A1": {"x": 140.0, "y": 0.0, "z_safe": 120.0, "z_pickup": 0.0, "manual": False},
                "A2": {"x": 210.0, "y": 0.0, "z_safe": 120.0, "z_pickup": 0.0, "manual": False},
                "B1": {"x": 140.0, "y": 110.0, "z_safe": 120.0, "z_pickup": 0.0, "manual": False},
                "B2": {"x": 210.0, "y": 110.0, "z_safe": 120.0, "z_pickup": 0.0, "manual": False}
            }
        }
        save_config(default_config, filename)
        return default_config


def save_config(config_data: Dict[str, Any], filename: str = CONFIG_FILE) -> None:
    """Guarda la configuración del almacén en el archivo JSON."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)
    logger.info(f"Configuración guardada en {filename}")


def create_config_backup(filename: str = CONFIG_FILE) -> Optional[str]:
    """
    Crea una copia de respaldo simple de warehouse_config.json (warehouse_config.backup_<timestamp>.json).
    """
    import os
    import shutil
    from datetime import datetime

    if not os.path.exists(filename):
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"warehouse_config.backup_{timestamp}.json"
    try:
        shutil.copy(filename, backup_filename)
        logger.info(f"Copia de respaldo de configuración creada: {backup_filename}")
        return backup_filename
    except Exception as e:
        logger.error(f"Error al crear copia de respaldo de {filename}: {e}")
        return None


def generate_grid_cells(
    rows: int,
    cols: int,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    z_safe: float = 120.0,
    z_pickup: float = 0.0,
    overwrite_manual: bool = False,
    current_config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Genera un nuevo diccionario de celdas basado en las filas y columnas indicadas.

    Nombres de celdas: A1, A2..., B1, B2...
    Si overwrite_manual es False y existen celdas marcadas como "manual": True, se conservan.
    """
    if current_config is None:
        current_config = load_config()

    new_cells: Dict[str, Any] = {}

    # Si se deben conservar las manuales existentes
    if not overwrite_manual:
        for cid, cdata in current_config.get("cells", {}).items():
            if cdata.get("manual", False):
                new_cells[cid] = cdata

    for r in range(rows):
        letter = chr(65 + r)  # A, B, C...
        for c in range(cols):
            cell_id = f"{letter}{c + 1}"
            
            # Si ya existe como manual y no se sobrescribe, no tocarla
            if cell_id in new_cells and new_cells[cell_id].get("manual", False):
                continue

            x = x0 + (c * dx)
            y = y0 + (r * dy)

            new_cells[cell_id] = {
                "x": round(x, 2),
                "y": round(y, 2),
                "z_safe": z_safe,
                "z_pickup": z_pickup,
                "manual": False
            }

    updated_config = {
        "grid": {
            "rows": rows,
            "cols": cols,
            "x0": x0,
            "y0": y0,
            "dx": dx,
            "dy": dy
        },
        "cells": new_cells
    }
    return updated_config


def validate_coords_against_grbl(
    x: float,
    y: float,
    z_safe: float,
    z_pickup: float,
    grbl_limits: Dict[str, float]
) -> Tuple[bool, str]:
    """
    Valida las coordenadas de una celda contra los límites de recorrido de GRBL ($130, $131, $132).

    Args:
        x, y, z_safe, z_pickup: Coordenadas a probar.
        grbl_limits: Dict con límites de recorrido ej. {'X': 360.0, 'Y': 290.0, 'Z': 150.0}

    Returns:
        (es_valido: bool, mensaje_error: str)
    """
    max_x = grbl_limits.get('X', 360.0)
    max_y = grbl_limits.get('Y', 290.0)
    max_z = grbl_limits.get('Z', 150.0)

    if x < 0 or x > max_x:
        return False, f"Coordenada X ({x} mm) fuera de límites [0 - {max_x} mm] (GRBL $130)"
    if y < 0 or y > max_y:
        return False, f"Coordenada Y ({y} mm) fuera de límites [0 - {max_y} mm] (GRBL $131)"
    if z_safe < 0 or z_safe > max_z:
        return False, f"Coordenada Z safe ({z_safe} mm) fuera de límites [0 - {max_z} mm] (GRBL $132)"
    if z_pickup < 0 or z_pickup > max_z:
        return False, f"Coordenada Z pickup ({z_pickup} mm) fuera de límites [0 - {max_z} mm] (GRBL $132)"

    return True, ""


def save_manual_cell_pos(
    cell_id: str,
    x: float,
    y: float,
    z_safe: float = 120.0,
    z_pickup: float = 0.0,
    overwrite: bool = False,
    filename: str = CONFIG_FILE,
    grbl_limits: Optional[Dict[str, float]] = None
) -> Tuple[bool, str]:
    """
    Guarda una celda manual con las coordenadas dadas (ej. capturadas desde la posición en vivo).
    
    Args:
        cell_id: ID de la celda (ej: 'A1')
        x: Coordenada X (mm)
        y: Coordenada Y (mm)
        z_safe: Altura Z segura
        z_pickup: Altura Z de recogida
        overwrite: Si True, permite sobrescribir una celda existente
        filename: Archivo JSON de configuración
        grbl_limits: Límites de GRBL para validación

    Returns:
        (éxito: bool, mensaje: str)
    """
    if grbl_limits:
        valid, err_msg = validate_coords_against_grbl(x, y, z_safe, z_pickup, grbl_limits)
        if not valid:
            return False, err_msg

    config = load_config(filename)
    cells = config.get("cells", {})

    cell_id = cell_id.strip().upper()
    if not cell_id:
        return False, "El ID de la celda no puede estar vacío."

    if cell_id in cells and not overwrite:
        return False, f"La celda '{cell_id}' ya existe en la configuración. Marca 'Sobrescribir' para reemplazarla."

    cells[cell_id] = {
        "x": round(x, 2),
        "y": round(y, 2),
        "z_safe": round(z_safe, 2),
        "z_pickup": round(z_pickup, 2),
        "manual": True
    }

    config["cells"] = cells
    save_config(config, filename)
    logger.info(f"Celda manual '{cell_id}' guardada en ({x:.2f}, {y:.2f})")
    return True, f"Celda '{cell_id}' guardada exitosamente."


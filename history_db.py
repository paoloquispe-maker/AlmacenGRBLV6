"""
Capa de persistencia SQLite para el historial de movimientos del almacén (append-only).
"""

import sqlite3
import csv
import logging
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

DB_FILENAME = "historial.db"


class HistoryDB:
    """Gestiona la base de datos de auditoría de movimientos (SQLite append-only)."""

    def __init__(self, db_path: str = DB_FILENAME):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Crea las tablas 'movements' y 'cells_state' si no existen."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS movements (
                    movement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    cell_id TEXT NOT NULL,
                    movement_type TEXT NOT NULL,
                    item_code TEXT,
                    origen TEXT DEFAULT 'MANUAL',
                    notas TEXT
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cells_state (
                    cell_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    content TEXT,
                    last_updated TEXT NOT NULL
                );
            """)
            conn.commit()
        logger.info(f"Base de datos de historial inicializada: {self.db_path}")
        self.migrate_legacy_state()

    def record_cell_movement_tx(
        self,
        cell_id: str,
        movement_type: str,
        status: str,
        content: Optional[str] = None,
        origen: str = "MANUAL",
        notas: Optional[str] = None
    ) -> int:
        """
        Registra un movimiento en 'movements' y actualiza 'cells_state' en una sola transacción atómica SQLite.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO movements (timestamp, cell_id, movement_type, item_code, origen, notas)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (now_str, cell_id.upper(), movement_type.upper(), content, origen.upper(), notas))
            last_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO cells_state (cell_id, status, content, last_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cell_id) DO UPDATE SET
                    status=excluded.status,
                    content=excluded.content,
                    last_updated=excluded.last_updated
            """, (cell_id.upper(), status.lower(), content, now_str))

            conn.commit()
            logger.info(f"Transacción atómica #{last_id} completada: Celda {cell_id} -> status='{status}', content='{content}'")
            return last_id

    def get_all_cell_states(self) -> Dict[str, Dict[str, Optional[str]]]:
        """Devuelve el estado actual de todas las celdas almacenadas en SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT cell_id, status, content, last_updated FROM cells_state")
            rows = cursor.fetchall()
            return {
                r["cell_id"]: {
                    "status": r["status"],
                    "content": r["content"],
                    "last_updated": r["last_updated"]
                }
                for r in rows
            }

    def set_cell_state_only(self, cell_id: str, status: str, content: Optional[str] = None) -> None:
        """Actualiza únicamente el estado de una celda en SQLite."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO cells_state (cell_id, status, content, last_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cell_id) DO UPDATE SET
                    status=excluded.status,
                    content=excluded.content,
                    last_updated=excluded.last_updated
            """, (cell_id.upper(), status.lower(), content, now_str))
            conn.commit()

    def migrate_legacy_state(self, legacy_file: str = "warehouse_state.json") -> bool:
        """
        Si existe warehouse_state.json antiguo y cells_state está vacía, importa los datos una sola vez
        y renombrar el JSON viejo a warehouse_state.json.migrated.
        """
        import os
        import json

        if not os.path.exists(legacy_file):
            return False

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM cells_state")
            count = cursor.fetchone()[0]
            if count > 0:
                return False

            try:
                with open(legacy_file, 'r', encoding='utf-8') as f:
                    legacy_data = json.load(f)

                cells_map = legacy_data.get("cells", {})
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                for cid, cinfo in cells_map.items():
                    cursor.execute("""
                        INSERT INTO cells_state (cell_id, status, content, last_updated)
                        VALUES (?, ?, ?, ?)
                    """, (cid.upper(), cinfo.get("status", "empty").lower(), cinfo.get("content"), now_str))

                conn.commit()
                logger.info(f"Migrados {len(cells_map)} estados de celdas desde {legacy_file} a SQLite cells_state.")

                migrated_file = legacy_file + ".migrated"
                os.rename(legacy_file, migrated_file)
                logger.info(f"Renombrado archivo legacy a {migrated_file}")
                return True
            except Exception as e:
                logger.error(f"Error durante migración de {legacy_file}: {e}")
                return False

    def add_movement(
        self,
        cell_id: str,
        movement_type: str,
        item_code: Optional[str] = None,
        origen: str = "MANUAL",
        notas: Optional[str] = None
    ) -> int:
        """
        Inserta un nuevo registro de movimiento (append-only).

        Args:
            cell_id: ID de la celda (ej: 'A1')
            movement_type: 'ENTRADA', 'SALIDA', 'AJUSTE', 'RESERVA'
            item_code: Código o nombre del objeto
            origen: 'MANUAL' o 'AUTOMATICO'
            notas: Observaciones adicionales

        Returns:
            ID del registro insertado
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO movements (timestamp, cell_id, movement_type, item_code, origen, notas)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (now_str, cell_id, movement_type.upper(), item_code, origen.upper(), notas))
            conn.commit()
            last_id = cursor.lastrowid
            logger.info(f"Movimiento #{last_id} registrado: {movement_type} en {cell_id} ({item_code})")
            return last_id

    def get_movements(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        cell_id: Optional[str] = None,
        movement_type: Optional[str] = None,
        search_text: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        """
        Consulta movimientos con filtros opcionales.

        Returns:
            Lista de diccionarios representando las filas.
        """
        query = "SELECT movement_id, timestamp, cell_id, movement_type, item_code, origen, notas FROM movements WHERE 1=1"
        params = []

        if date_from:
            query += " AND timestamp >= ?"
            params.append(f"{date_from} 00:00:00")
        if date_to:
            query += " AND timestamp <= ?"
            params.append(f"{date_to} 23:59:59")
        if cell_id and cell_id.strip():
            query += " AND cell_id = ?"
            params.append(cell_id.strip().upper())
        if movement_type and movement_type.upper() != "TODOS":
            query += " AND movement_type = ?"
            params.append(movement_type.upper())
        if search_text and search_text.strip():
            query += " AND (item_code LIKE ? OR notas LIKE ?)"
            term = f"%{search_text.strip()}%"
            params.extend([term, term])

        query += " ORDER BY movement_id DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_daily_movement_count(self) -> int:
        """Devuelve el total de movimientos registrados el día de hoy."""
        today_prefix = datetime.now().strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM movements WHERE timestamp LIKE ?
            """, (f"{today_prefix}%",))
            return cursor.fetchone()[0]

    def export_to_csv(self, filepath: str, movements: Optional[List[Dict]] = None) -> int:
        """
        Exporta registros a un archivo CSV.

        Args:
            filepath: Ruta destino .csv
            movements: Lista de movimientos a exportar. Si es None, exporta todo.

        Returns:
            Número de filas exportadas
        """
        if movements is None:
            movements = self.get_movements()

        fieldnames = ["movement_id", "timestamp", "cell_id", "movement_type", "item_code", "origen", "notas"]
        with open(filepath, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(movements)

        logger.info(f"Exportados {len(movements)} registros a {filepath}")
        return len(movements)

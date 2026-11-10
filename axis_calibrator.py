"""
Calibrador de ejes para sistema de almacén automático AS/RS con GRBL 1.1

Permite calibrar los pasos/mm de cada eje utilizando los finales de carrera
físicos conectados a la placa GRBL (Annoy Tools). La calibración se realiza
en dos fases para permitir integración con GUI sin bloquear la interfaz:

  1. buscar_extremos()  → encuentra ambos límites, devuelve distancia reportada
  2. aplicar_calibracion() → recibe distancia real, calcula y aplica corrección

Versión: 1.0
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable, Dict

from warehouse_grbl_system import GRBLController

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
#  EXCEPCIÓN Y RESULTADO
# ═══════════════════════════════════════════════

class CalibrationError(Exception):
    """Error durante el proceso de calibración de un eje"""
    pass


@dataclass
class CalibrationResult:
    """
    Resultado de una calibración de eje completada.

    Attributes:
        axis: Eje calibrado ('X', 'Y' o 'Z')
        pasos_mm_anterior: Valor de $10x antes de calibrar
        pasos_mm_nuevo: Valor de $10x después de calibrar
        distancia_reportada_mm: Distancia entre límites según GRBL (antes de corregir)
        distancia_real_mm: Distancia real medida por el usuario
        timestamp: Marca temporal ISO 8601 de la calibración
        limite_recorrido_sugerido_mm: Valor sugerido para $13x (= distancia_real_mm)
    """
    axis: str
    pasos_mm_anterior: float
    pasos_mm_nuevo: float
    distancia_reportada_mm: float
    distancia_real_mm: float
    timestamp: str
    limite_recorrido_sugerido_mm: float


# ═══════════════════════════════════════════════
#  CLASE PRINCIPAL
# ═══════════════════════════════════════════════

class AxisCalibrator:
    """
    Calibrador de pasos/mm por eje para GRBL 1.1.

    Ejecuta rutinas de calibración usando finales de carrera físicos
    conectados a la placa GRBL. Diseñado para integración con GUI:
    los métodos públicos no usan input() ni print(), y aceptan un
    callback on_progress para reportar estado.

    Args:
        grbl: Instancia de GRBLController ya conectada
        incremento_grueso: Paso de búsqueda gruesa en mm
        incremento_fino: Paso de búsqueda fina en mm
        feed_rate: Velocidad de movimiento durante calibración (mm/min)
        max_incrementos: Máximo de pasos gruesos antes de abortar
        lecturas_debounce: Lecturas consecutivas para confirmar estado de switch
    """

    # Mapeo de eje → parámetro GRBL
    PARAM_PASOS = {'X': '$100', 'Y': '$101', 'Z': '$102'}
    PARAM_LIMITE = {'X': '$130', 'Y': '$131', 'Z': '$132'}

    def __init__(
        self,
        grbl: GRBLController,
        incremento_grueso: float = 5.0,
        incremento_fino: float = 0.2,
        feed_rate: float = 100.0,
        max_incrementos: int = 100,
        lecturas_debounce: int = 3,
    ):
        if not grbl.is_connected:
            raise CalibrationError(
                "El GRBLController proporcionado no está conectado. "
                "Llama a grbl.connect() antes de crear el calibrador."
            )

        self.grbl = grbl
        self.incremento_grueso = incremento_grueso
        self.incremento_fino = incremento_fino
        self.feed_rate = feed_rate
        self.max_incrementos = max_incrementos
        self.lecturas_debounce = lecturas_debounce

        # Estado interno de la sesión de calibración
        self._saved_soft_limits: Optional[float] = None
        self._saved_hard_limits: Optional[float] = None
        self._calibracion_activa: bool = False

    # ═══════════════════════════════════════════════
    #  MÉTODOS PÚBLICOS (API de 2 fases)
    # ═══════════════════════════════════════════════

    def capturar_punto_manual(self, axis: str) -> float:
        """
        Lee y devuelve la posición actual del eje, sin mover nada.

        Usar durante calibración manual (sin finales de carrera): el
        usuario jogea manualmente hasta el extremo deseado con los
        controles de Control Manual, y luego se llama a este método
        para registrar ese punto como uno de los dos extremos.

        Args:
            axis: Eje a leer ('X', 'Y' o 'Z')

        Returns:
            Posición actual del eje en mm, según GRBL

        Raises:
            CalibrationError: Si no se puede leer la posición
        """
        axis = axis.upper()
        if axis not in self.PARAM_PASOS:
            raise CalibrationError(f"Eje '{axis}' no válido. Usar 'X', 'Y' o 'Z'.")

        pos = self.grbl.get_position()
        if not pos:
            raise CalibrationError(
                "No se pudo leer la posición actual. Verifica la conexión serial."
            )

        return pos[axis.lower()]

    def buscar_extremos(
        self,
        axis: str,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> float:
        """
        Busca ambos extremos de un eje usando finales de carrera.

        Ejecuta pasos 0-4 de la calibración: desactiva límites de software,
        localiza ambos finales de carrera con búsqueda gruesa + fina,
        y devuelve la distancia reportada por GRBL entre ellos.

        Args:
            axis: Eje a calibrar ('X', 'Y' o 'Z')
            on_progress: Callback para mensajes de progreso

        Returns:
            Distancia entre ambos extremos según GRBL (mm). Este valor
            puede estar mal calibrado — es el dato de entrada para
            aplicar_calibracion().

        Raises:
            CalibrationError: Switch no detectado, alarma, timeout, etc.
        """
        axis = axis.upper()
        if axis not in self.PARAM_PASOS:
            raise CalibrationError(
                f"Eje '{axis}' no válido. Usar 'X', 'Y' o 'Z'."
            )

        try:
            # ── Paso 0: Preparación ──
            self._preparar_limites(on_progress)
            self._calibracion_activa = True

            # Modo relativo para toda la búsqueda
            self.grbl.send_command("G91")
            logger.info(f"Iniciando búsqueda de extremos del eje {axis}")

            # ── Verificar switch inicial ──
            if self._switch_activado(axis):
                self._notificar(
                    on_progress,
                    f"Switch {axis} ya activado al iniciar, liberando...",
                )
                self._liberar_switch(axis, direccion=+1)

            # ── Paso 1-2: Límite A (dirección negativa) ──
            self._notificar(
                on_progress,
                f"Buscando límite A del eje {axis} (dirección -)...",
            )
            self._buscar_limite_grueso(axis, direccion=-1, on_progress=on_progress)
            self._aproximacion_fina(axis, direccion=-1, on_progress=on_progress)

            # ── Paso 3: Fijar referencia {eje}=0 en límite A ──
            self.grbl.send_command(f"G92 {axis}0")
            self._notificar(
                on_progress,
                f"Referencia {axis}=0 establecida en límite A",
            )

            # Liberar switch A antes de buscar B
            self._liberar_switch(axis, direccion=+1)

            # ── Paso 4: Límite B (dirección positiva) ──
            self._notificar(
                on_progress,
                f"Buscando límite B del eje {axis} (dirección +)...",
            )
            self._buscar_limite_grueso(axis, direccion=+1, on_progress=on_progress)
            self._aproximacion_fina(axis, direccion=+1, on_progress=on_progress)

            # ── Leer distancia reportada por GRBL ──
            self.grbl.serial_conn.reset_input_buffer()
            time.sleep(0.15)
            pos = self.grbl.get_position()

            if not pos:
                raise CalibrationError(
                    "No se pudo leer la posición después de encontrar "
                    "ambos límites. Verifica la conexión serial."
                )

            distancia_reportada = abs(pos[axis.lower()])

            if distancia_reportada <= 0:
                raise CalibrationError(
                    f"Distancia reportada = {distancia_reportada:.3f} mm "
                    f"(inválida). Verifica los finales de carrera."
                )

            logger.info(
                f"Eje {axis}: distancia reportada entre límites = "
                f"{distancia_reportada:.3f} mm"
            )
            self._notificar(
                on_progress,
                f"Distancia reportada por GRBL: {distancia_reportada:.3f} mm. "
                f"Mide la distancia real con un instrumento para continuar.",
            )

            # Liberar switch B y restaurar modo absoluto
            self._liberar_switch(axis, direccion=-1)
            self.grbl.send_command("G90")

            return distancia_reportada

        except CalibrationError:
            self._cleanup_on_error(on_progress)
            raise
        except Exception as e:
            self._cleanup_on_error(on_progress)
            raise CalibrationError(
                f"Error inesperado durante búsqueda de extremos: {e}"
            ) from e

    def aplicar_calibracion(
        self,
        axis: str,
        distancia_reportada: float,
        distancia_real_mm: float,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> CalibrationResult:
        """
        Calcula y aplica el factor de corrección de pasos/mm.

        Ejecuta pasos 5-8: calcula el nuevo valor de $10x, lo escribe
        en la EEPROM de GRBL, y restaura los límites de seguridad.

        Args:
            axis: Eje calibrado ('X', 'Y' o 'Z')
            distancia_reportada: Valor devuelto por buscar_extremos()
            distancia_real_mm: Distancia real medida por el usuario (mm)
            on_progress: Callback para mensajes de progreso

        Returns:
            CalibrationResult con todos los datos de la calibración

        Raises:
            CalibrationError: Valor inválido o error de comunicación
        """
        axis = axis.upper()

        if axis not in self.PARAM_PASOS:
            raise CalibrationError(f"Eje '{axis}' no válido. Usar 'X', 'Y' o 'Z'.")
        if distancia_real_mm <= 0:
            raise CalibrationError(
                f"La distancia real debe ser positiva (recibido: {distancia_real_mm})"
            )
        if distancia_reportada <= 0:
            raise CalibrationError(
                f"La distancia reportada debe ser positiva "
                f"(recibido: {distancia_reportada})"
            )

        try:
            # ── Paso 5-6: Leer parámetro actual y calcular factor ──
            parametros = self._leer_parametros_grbl()
            param_key = self.PARAM_PASOS[axis]

            pasos_mm_actual = parametros.get(param_key)
            if pasos_mm_actual is None:
                raise CalibrationError(
                    f"No se pudo leer el parámetro {param_key} de GRBL"
                )

            factor = distancia_reportada / distancia_real_mm
            pasos_mm_nuevo = pasos_mm_actual * factor

            self._notificar(
                on_progress,
                f"Factor de corrección: {factor:.6f} "
                f"({distancia_reportada:.3f} / {distancia_real_mm:.3f})",
            )
            self._notificar(
                on_progress,
                f"Pasos/mm: {pasos_mm_actual:.3f} → {pasos_mm_nuevo:.3f}",
            )

            # ── Paso 7: Aplicar y guardar en EEPROM ──
            cmd = f"{param_key}={pasos_mm_nuevo:.3f}"
            if not self.grbl.send_command(cmd):
                raise CalibrationError(
                    f"Error al enviar comando {cmd} a GRBL"
                )

            self._notificar(on_progress, f"{cmd} guardado en EEPROM")

            resultado = CalibrationResult(
                axis=axis,
                pasos_mm_anterior=pasos_mm_actual,
                pasos_mm_nuevo=pasos_mm_nuevo,
                distancia_reportada_mm=distancia_reportada,
                distancia_real_mm=distancia_real_mm,
                timestamp=datetime.now().isoformat(),
                limite_recorrido_sugerido_mm=distancia_real_mm,
            )

            logger.info(
                f"Calibración eje {axis} completada: "
                f"{pasos_mm_actual:.3f} → {pasos_mm_nuevo:.3f} pasos/mm "
                f"(reportado={distancia_reportada:.3f}, "
                f"real={distancia_real_mm:.3f})"
            )

            param_limite = self.PARAM_LIMITE[axis]
            self._notificar(
                on_progress,
                f"Valor sugerido para {param_limite} (límite de recorrido): "
                f"{distancia_real_mm:.1f} mm — aplícalo manualmente si lo deseas.",
            )

            return resultado

        finally:
            # ── Paso 8: Restaurar límites siempre ──
            self._restaurar_limites(on_progress)
            self._calibracion_activa = False

    def cancelar(
        self,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Cancela la calibración en curso y restaura la configuración.

        Llamar si se ejecutó buscar_extremos() pero no se desea continuar
        con aplicar_calibracion().
        """
        if self._calibracion_activa:
            self.grbl.send_command("G90")
            self._restaurar_limites(on_progress)
            self._calibracion_activa = False
            logger.info("Calibración cancelada, configuración restaurada")
            self._notificar(on_progress, "Calibración cancelada")

    # ═══════════════════════════════════════════════
    #  COMUNICACIÓN SERIAL DIRECTA CON GRBL
    # ═══════════════════════════════════════════════

    def _leer_parametros_grbl(self) -> Dict[str, float]:
        """
        Lee todos los parámetros de configuración de GRBL (comando $$).

        Returns:
            Diccionario {'$100': 800.0, '$101': 800.0, ...}

        Raises:
            CalibrationError: Si no se puede leer la respuesta
        """
        serial_conn = self.grbl.serial_conn
        serial_conn.reset_input_buffer()
        serial_conn.write(b'$$\n')

        buffer = ""
        timeout = time.time() + 5.0
        while time.time() < timeout:
            if serial_conn.in_waiting:
                buffer += serial_conn.read(
                    serial_conn.in_waiting
                ).decode(errors='replace')
                if 'ok' in buffer:
                    break
            time.sleep(0.05)

        parametros: Dict[str, float] = {}
        for linea in buffer.split('\n'):
            linea = linea.strip()
            if linea.startswith('$') and '=' in linea:
                try:
                    clave, valor = linea.split('=', 1)
                    parametros[clave.strip()] = float(valor.strip())
                except (ValueError, IndexError):
                    pass

        if not parametros:
            raise CalibrationError(
                "No se pudieron leer los parámetros de GRBL ($$). "
                "Verifica la conexión serial."
            )

        logger.debug(f"Parámetros GRBL leídos: {len(parametros)} valores")
        return parametros

    def _leer_estado_completo(self) -> Dict:
        """
        Lee el estado completo de GRBL (comando ? — real-time).

        Parsea el formato GRBL 1.1:
            <State|MPos:x,y,z|FS:f,s|Pn:XYZ|...>

        Returns:
            Dict con claves:
              'state' (str): Estado ('Idle', 'Run', 'Alarm', ...)
              'mpos'  (dict): Posición {'x': float, 'y': float, 'z': float}
              'pn'    (str): Pines activos ('XYZ', 'X', '', etc.)
        """
        serial_conn = self.grbl.serial_conn
        serial_conn.reset_input_buffer()
        serial_conn.write(b'?')

        buffer = ""
        timeout = time.time() + 2.0
        while time.time() < timeout:
            if serial_conn.in_waiting:
                buffer += serial_conn.read(
                    serial_conn.in_waiting
                ).decode(errors='replace')
                if '>' in buffer:
                    break
            time.sleep(0.05)

        resultado = {'state': '', 'mpos': {}, 'pn': ''}

        if '<' not in buffer or '>' not in buffer:
            logger.warning(f"Respuesta de estado incompleta: {buffer!r}")
            return resultado

        contenido = buffer[buffer.index('<') + 1 : buffer.index('>')]
        partes = contenido.split('|')

        resultado['state'] = partes[0]

        for parte in partes[1:]:
            if parte.startswith('MPos:') or parte.startswith('WPos:'):
                try:
                    coords = parte.split(':')[1].split(',')
                    resultado['mpos'] = {
                        'x': float(coords[0]),
                        'y': float(coords[1]),
                        'z': float(coords[2]),
                    }
                except (ValueError, IndexError):
                    pass
            elif parte.startswith('Pn:'):
                resultado['pn'] = parte[3:]

        return resultado

    # ═══════════════════════════════════════════════
    #  LECTURA DE SWITCHES (con debounce)
    # ═══════════════════════════════════════════════

    def _switch_activado(self, axis: str) -> bool:
        """
        Verifica si el switch del eje está activado.

        Realiza múltiples lecturas consecutivas (debounce) para filtrar
        ruido eléctrico. Solo devuelve True si TODAS las lecturas
        confirman la activación.

        Args:
            axis: Eje a verificar ('X', 'Y' o 'Z')

        Returns:
            True si el switch está activado de forma estable
        """
        confirmaciones = 0
        for _ in range(self.lecturas_debounce):
            estado = self._leer_estado_completo()
            if axis.upper() in estado['pn']:
                confirmaciones += 1
            time.sleep(0.05)

        return confirmaciones == self.lecturas_debounce

    # ═══════════════════════════════════════════════
    #  MOVIMIENTO Y ESPERA
    # ═══════════════════════════════════════════════

    def _esperar_idle(self, timeout: float = 30.0) -> None:
        """
        Espera hasta que GRBL reporte estado Idle.

        Raises:
            CalibrationError: Si hay alarma o timeout
        """
        inicio = time.time()
        while time.time() - inicio < timeout:
            estado = self._leer_estado_completo()

            if estado['state'] == 'Idle':
                return

            if 'Alarm' in estado['state']:
                raise CalibrationError(
                    f"GRBL entró en estado de alarma ({estado['state']}). "
                    f"Ejecuta $X para desbloquear e intenta de nuevo."
                )

            time.sleep(0.15)

        raise CalibrationError(
            f"Timeout de {timeout}s esperando estado Idle. "
            f"El motor puede estar atascado o la comunicación falló."
        )

    def _mover_relativo(self, axis: str, distancia: float) -> None:
        """
        Envía un movimiento relativo con feed rate controlado y espera
        a que termine.

        Asume que el modo G91 ya está activo.

        Args:
            axis: Eje a mover
            distancia: Distancia en mm (signo indica dirección)

        Raises:
            CalibrationError: Si el comando falla o hay timeout/alarma
        """
        cmd = f"G1 {axis}{distancia:.3f} F{self.feed_rate:.0f}"
        if not self.grbl.send_command(cmd):
            raise CalibrationError(
                f"Error al enviar comando de movimiento: {cmd}"
            )
        self._esperar_idle()

    # ═══════════════════════════════════════════════
    #  BÚSQUEDA DE LÍMITES
    # ═══════════════════════════════════════════════

    def _buscar_limite_grueso(
        self,
        axis: str,
        direccion: int,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Búsqueda gruesa de un final de carrera.

        Mueve en incrementos de self.incremento_grueso hasta detectar
        el switch o agotar self.max_incrementos.

        Args:
            axis: Eje a buscar
            direccion: +1 o -1
            on_progress: Callback de progreso

        Raises:
            CalibrationError: Si el switch no se detecta
        """
        distancia = self.incremento_grueso * direccion
        signo = '+' if direccion > 0 else '-'

        for i in range(self.max_incrementos):
            self._mover_relativo(axis, distancia)

            if self._switch_activado(axis):
                self._notificar(
                    on_progress,
                    f"Switch {axis} detectado (búsqueda gruesa, "
                    f"paso {i + 1}, "
                    f"~{(i + 1) * self.incremento_grueso:.0f} mm)",
                )
                return

            # Reportar progreso cada 10 pasos
            if (i + 1) % 10 == 0:
                self._notificar(
                    on_progress,
                    f"Buscando límite {axis}{signo}: "
                    f"{(i + 1) * self.incremento_grueso:.0f} mm recorridos...",
                )

        recorrido_total = self.max_incrementos * self.incremento_grueso
        raise CalibrationError(
            f"Switch del eje {axis} no detectado después de "
            f"{recorrido_total:.0f} mm de recorrido "
            f"({self.max_incrementos} incrementos de "
            f"{self.incremento_grueso} mm). "
            f"Verifica la conexión del final de carrera."
        )

    def _aproximacion_fina(
        self,
        axis: str,
        direccion: int,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Retrocede del switch y se reaproxima con pasos finos para
        determinar el punto preciso de activación.

        Args:
            axis: Eje a calibrar
            direccion: +1 o -1 (dirección original de búsqueda)
            on_progress: Callback de progreso

        Raises:
            CalibrationError: Si no se puede liberar o reencontrar el switch
        """
        # ── Retroceder para soltar el switch ──
        retroceso = 2.0 * (-direccion)
        self._mover_relativo(axis, retroceso)

        if self._switch_activado(axis):
            # Segundo intento con más distancia
            self._mover_relativo(axis, retroceso)
            if self._switch_activado(axis):
                raise CalibrationError(
                    f"El switch del eje {axis} no se desactivó después de "
                    f"retroceder 4 mm. Posible switch atascado."
                )

        self._notificar(on_progress, f"Aproximación fina al límite {axis}...")

        # ── Reaproximación fina ──
        distancia_fina = self.incremento_fino * direccion
        # Máximo 5 mm de recorrido fino (margen generoso sobre los 2 mm de retroceso)
        max_pasos_finos = int(5.0 / self.incremento_fino)

        for i in range(max_pasos_finos):
            self._mover_relativo(axis, distancia_fina)

            if self._switch_activado(axis):
                self._notificar(
                    on_progress,
                    f"Límite {axis} preciso encontrado "
                    f"(paso fino {i + 1}, "
                    f"precisión ±{self.incremento_fino} mm)",
                )
                return

        raise CalibrationError(
            f"No se pudo reencontrar el switch del eje {axis} en la "
            f"aproximación fina después de {max_pasos_finos} pasos de "
            f"{self.incremento_fino} mm. El switch puede tener demasiada "
            f"histéresis o estar intermitente."
        )

    def _liberar_switch(self, axis: str, direccion: int) -> None:
        """
        Retrocede hasta liberar un switch activado.

        Args:
            axis: Eje cuyo switch liberar
            direccion: Dirección de retroceso (+1 o -1)

        Raises:
            CalibrationError: Si no se puede liberar el switch
        """
        # Primer intento: 2 mm
        self._mover_relativo(axis, 2.0 * direccion)

        if not self._switch_activado(axis):
            return

        # Segundo intento: 4 mm más
        self._mover_relativo(axis, 4.0 * direccion)

        if self._switch_activado(axis):
            raise CalibrationError(
                f"No se pudo liberar el switch del eje {axis} después de "
                f"retroceder 6 mm. Verifica el montaje del sensor."
            )

    # ═══════════════════════════════════════════════
    #  GESTIÓN DE LÍMITES ($20 / $21)
    # ═══════════════════════════════════════════════

    def _preparar_limites(
        self,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Guarda los valores actuales de $20/$21 y los desactiva."""
        parametros = self._leer_parametros_grbl()

        self._saved_soft_limits = parametros.get('$20', 0)
        self._saved_hard_limits = parametros.get('$21', 0)

        logger.info(
            f"Guardando límites previos: "
            f"$20={self._saved_soft_limits}, $21={self._saved_hard_limits}"
        )

        self.grbl.send_command("$20=0")
        time.sleep(0.1)
        self.grbl.send_command("$21=0")
        time.sleep(0.1)

        self._notificar(
            on_progress,
            "Límites de seguridad ($20/$21) desactivados temporalmente",
        )

    def _restaurar_limites(
        self,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Restaura $20/$21 a los valores guardados previamente."""
        restaurados = []

        if self._saved_soft_limits is not None:
            val = int(self._saved_soft_limits)
            self.grbl.send_command(f"$20={val}")
            restaurados.append(f"$20={val}")
            time.sleep(0.1)

        if self._saved_hard_limits is not None:
            val = int(self._saved_hard_limits)
            self.grbl.send_command(f"$21={val}")
            restaurados.append(f"$21={val}")
            time.sleep(0.1)

        if restaurados:
            logger.info(f"Límites restaurados: {', '.join(restaurados)}")
            self._notificar(
                on_progress,
                f"Límites de seguridad restaurados ({', '.join(restaurados)})",
            )

        self._saved_soft_limits = None
        self._saved_hard_limits = None

    # ═══════════════════════════════════════════════
    #  UTILIDADES
    # ═══════════════════════════════════════════════

    def _cleanup_on_error(
        self,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Limpieza de estado tras un error durante la calibración."""
        try:
            self.grbl.send_command("G90")
        except Exception:
            pass
        self._restaurar_limites(on_progress)
        self._calibracion_activa = False

    @staticmethod
    def _notificar(
        callback: Optional[Callable[[str], None]],
        mensaje: str,
    ) -> None:
        """Invoca el callback de progreso si está definido."""
        if callback:
            callback(mensaje)


# ═══════════════════════════════════════════════
#  PRUEBA MANUAL POR CONSOLA
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )

    print("\n" + "=" * 70)
    print("🔧 CALIBRADOR DE EJES — Almacén Automático AS/RS")
    print("=" * 70)
    print("Este script calibra los pasos/mm de un eje usando los")
    print("finales de carrera conectados a la placa GRBL (Annoy Tools).")
    print("=" * 70)

    puerto = input("\n¿Puerto COM? (Enter para COM6): ").strip() or "COM6"
    eje = input("¿Eje a calibrar? (X/Y/Z): ").strip().upper()

    if eje not in ('X', 'Y', 'Z'):
        print(f"❌ Eje '{eje}' no válido. Usa X, Y o Z.")
        sys.exit(1)

    grbl = GRBLController(port=puerto, baudrate=115200)

    if not grbl.connect():
        print(f"\n❌ No se pudo conectar a {puerto}")
        print("Verifica:")
        print("  - Annoy Tools conectado al USB")
        print("  - Puerto COM correcto")
        print("  - Ningún otro programa usando el puerto")
        sys.exit(1)

    print(f"✅ Conectado a {puerto}")

    # Desbloquear
    grbl.send_command("$X")
    time.sleep(0.3)

    calibrador = AxisCalibrator(grbl)

    def progreso(msg: str) -> None:
        print(f"  ℹ️  {msg}")

    try:
        # ── Fase 1: Buscar extremos ──
        print(f"\n{'─' * 70}")
        print(f"📐 FASE 1: Búsqueda de extremos del eje {eje}")
        print(f"{'─' * 70}")
        print("⚠️  El motor se va a mover. Asegúrate de que el recorrido está libre.\n")

        input("Presiona Enter para comenzar la búsqueda...")

        distancia_reportada = calibrador.buscar_extremos(eje, on_progress=progreso)

        # ── Fase 2: Entrada del usuario ──
        print(f"\n{'─' * 70}")
        print(f"📏 FASE 2: Medición manual")
        print(f"{'─' * 70}")
        print(f"  GRBL reporta: {distancia_reportada:.3f} mm entre ambos límites.")
        print(f"  Ahora mide la distancia REAL con un calibre o regla.")

        try:
            entrada = input(
                "\n📏 Distancia REAL medida (mm), o 'c' para cancelar: "
            ).strip()

            if entrada.lower() == 'c':
                print("\n⚠️ Calibración cancelada.")
                calibrador.cancelar(on_progress=progreso)
                sys.exit(0)

            distancia_real = float(entrada)

        except ValueError:
            print("❌ Valor no válido. Calibración cancelada.")
            calibrador.cancelar(on_progress=progreso)
            sys.exit(1)

        # ── Fase 3: Aplicar calibración ──
        print(f"\n{'─' * 70}")
        print(f"⚙️  FASE 3: Aplicando calibración")
        print(f"{'─' * 70}")

        resultado = calibrador.aplicar_calibracion(
            eje, distancia_reportada, distancia_real, on_progress=progreso
        )

        # ── Resultado ──
        print(f"\n{'═' * 70}")
        print(f"✅ CALIBRACIÓN DEL EJE {resultado.axis} COMPLETADA")
        print(f"{'═' * 70}")
        print(f"  Pasos/mm anterior:  {resultado.pasos_mm_anterior:.3f}")
        print(f"  Pasos/mm nuevo:     {resultado.pasos_mm_nuevo:.3f}")
        print(f"  Distancia GRBL:     {resultado.distancia_reportada_mm:.3f} mm")
        print(f"  Distancia real:     {resultado.distancia_real_mm:.3f} mm")
        print(f"  Timestamp:          {resultado.timestamp}")
        print(f"{'─' * 70}")
        print(
            f"  💡 Valor sugerido para {AxisCalibrator.PARAM_LIMITE[eje]}: "
            f"{resultado.limite_recorrido_sugerido_mm:.1f} mm"
        )
        print(f"{'═' * 70}")

    except CalibrationError as e:
        print(f"\n❌ Error de calibración: {e}")
        sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrumpido por el usuario (Ctrl+C)")
        calibrador.cancelar(on_progress=progreso)

    finally:
        grbl.disconnect()
        print("\n✅ Conexión cerrada. Hasta luego.")

"""
Script de Verificación y Generación de Capturas Headless (Offscreen)
Guarda capturas de las 4 pestañas y diferentes estados en docs/screens/
utilizando QT_QPA_PLATFORM=offscreen y widget.grab().save().
"""

import os
import sys
import time

# Forzar modo offscreen para renderizado sin display físico
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
import theme
from gui_main import MainWindow

def run_tests_and_screenshots():
    app = QApplication.instance() or QApplication(sys.argv)
    
    screens_dir = os.path.join(os.path.dirname(__file__), "docs", "screens")
    os.makedirs(screens_dir, exist_ok=True)
    
    print("Iniciando ventana para capturas offscreen...")
    window = MainWindow()
    window.resize(1280, 800)
    window.show()
    app.processEvents()

    # 1. Verificación de contrastes
    print("\n--- Verificación de Ratios de Contraste (WCAG AA) ---")
    dark_check = theme.verify_theme_contrast("dark")
    light_check = theme.verify_theme_contrast("light")
    print("Tema Oscuro:", dark_check)
    print("Tema Claro:", light_check)

    # 2. Captura: Estado Desconectado (Pestaña Operación)
    window.tabs.setCurrentIndex(0)
    app.processEvents()
    p_op_disc = os.path.join(screens_dir, "01_operacion_desconectado.png")
    window.grab().save(p_op_disc)
    print(f"Captura guardada: {p_op_disc}")

    # 3. Captura: Estado Conectado Sin Origen (Pestaña Operación)
    window.status_bar.update_connection(True, port="COM6")
    window.status_bar.set_origin_confirmed(False)
    window.apply_state("Sin referenciar")
    app.processEvents()
    p_op_sin_orig = os.path.join(screens_dir, "02_operacion_sin_referenciar.png")
    window.grab().save(p_op_sin_orig)
    print(f"Captura guardada: {p_op_sin_orig}")

    # 4. Captura: Estado Listo (Pestaña Operación con Celda Seleccionada)
    window.status_bar.set_origin_confirmed(True)
    window.apply_state("Listo")
    window._on_cell_selected("A1")
    app.processEvents()
    p_op_ready = os.path.join(screens_dir, "03_operacion_listo_seleccion.png")
    window.grab().save(p_op_ready)
    print(f"Captura guardada: {p_op_ready}")

    # 5. Captura: Estado Alarma Activa
    window.alarm_banner.mostrar("alarm", "ALARMA GRBL DETECTADA (Alarm:HardLimit). Desbloquea con $X.", "Desbloquear ($X)")
    window.status_bar.update_machine_state("Alarm:HardLimit")
    window.apply_state("Alarma")
    app.processEvents()
    p_op_alarm = os.path.join(screens_dir, "04_operacion_alarma_activa.png")
    window.grab().save(p_op_alarm)
    print(f"Captura guardada: {p_op_alarm}")
    window.alarm_banner.ocultar("alarm")

    # 6. Captura: Estado Pausa (Hold)
    window.status_bar.emergency_bar.set_paused_state(True)
    window.status_bar.update_machine_state("Hold:0")
    window.apply_state("Pausa")
    app.processEvents()
    p_op_hold = os.path.join(screens_dir, "05_operacion_pausa_hold.png")
    window.grab().save(p_op_hold)
    print(f"Captura guardada: {p_op_hold}")
    window.status_bar.emergency_bar.set_paused_state(False)
    window.apply_state("Listo")

    # 7. Captura: Pestaña Historial
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    p_hist = os.path.join(screens_dir, "06_pestana_historial.png")
    window.grab().save(p_hist)
    print(f"Captura guardada: {p_hist}")

    # 8. Captura: Pestaña Mantenimiento (Bloqueada)
    window.tabs.setCurrentIndex(2)
    window.maint_mode_unlocked = False
    window._update_maint_lock_ui()
    app.processEvents()
    p_maint_locked = os.path.join(screens_dir, "07_mantenimiento_bloqueado.png")
    window.grab().save(p_maint_locked)
    print(f"Captura guardada: {p_maint_locked}")

    # 9. Captura: Pestaña Mantenimiento (Desbloqueada)
    window.maint_mode_unlocked = True
    window._update_maint_lock_ui()
    app.processEvents()
    p_maint_unlocked = os.path.join(screens_dir, "08_mantenimiento_desbloqueado.png")
    window.grab().save(p_maint_unlocked)
    print(f"Captura guardada: {p_maint_unlocked}")

    # 10. Captura: Pestaña Configuración y Zona de Peligro
    window.tabs.setCurrentIndex(3)
    app.processEvents()
    p_cfg = os.path.join(screens_dir, "09_configuracion_zona_peligro.png")
    window.grab().save(p_cfg)
    print(f"Captura guardada: {p_cfg}")

    # 11. Captura: Tema Claro (Presentación / Proyector)
    window._on_theme_changed("light")
    window.tabs.setCurrentIndex(0)
    app.processEvents()
    p_light = os.path.join(screens_dir, "10_operacion_tema_claro.png")
    window.grab().save(p_light)
    print(f"Captura guardada: {p_light}")

    # Restaurar tema oscuro
    window._on_theme_changed("dark")
    app.processEvents()

    # 12. Captura: Sensor Arduino Conectado con Objeto Detectado (LED Virtual Activo) y Modo Automático
    window.sensor_panel.set_connection_state(True, "COM4")
    window.sensor_panel.set_detection_state(True)
    window.sensor_panel.set_auto_mode_state(True, "MODO AUTOMATICO ACTIVO: Objeto detectado en plataforma. Iniciando ciclo...")
    app.processEvents()
    p_sensor = os.path.join(screens_dir, "11_sensor_objeto_detectado.png")
    window.grab().save(p_sensor)
    print(f"Captura guardada: {p_sensor}")

    window.close()
    print("\n¡Todas las capturas y verificaciones completadas con éxito!")

if __name__ == "__main__":
    run_tests_and_screenshots()

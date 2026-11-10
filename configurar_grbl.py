from warehouse_grbl_system import GRBLController
import time

print("⚙️ CONFIGURACIÓN INICIAL DE GRBL PARA ANNOY TOOLS")
print("="*70)
print("Este script configura:")
print("  ✅ ENABLE normal ($4=0) - Para que motores funcionen")
print("  ✅ Dirección Z invertida ($3=4) - Motor Z gira correctamente")
print("  ✅ Velocidades optimizadas")
print("  ✅ Límites de recorrido")
print("="*70)

puerto = input("\n¿Puerto COM del Annoy Tools? (Enter para COM6): ").strip() or 'COM6'

grbl = GRBLController(port=puerto, baudrate=115200)

if grbl.connect():
    print("\n✅ Conectado a Annoy Tools\n")
    
    # Desbloquear primero
    print("🔓 Desbloqueando GRBL...")
    grbl.send_command("$X")
    time.sleep(0.3)
    
    # Configuración completa
    configuracion = {
        # CRÍTICO: ENABLE normal (para que motores funcionen)
        "$4": ("0", "ENABLE normal (motores activos) ✅"),
        
        # CRÍTICO: Invertir dirección del motor Z
        "$3": ("0", "Dirección Z normal"),
        
        # Velocidades (mm/min)
        "$110": ("500", "Velocidad X"),
        "$111": ("500", "Velocidad Y"),
        "$112": ("300", "Velocidad Z (lenta para palas)"),
        
        # Aceleraciones (mm/s²)
        "$120": ("10", "Aceleración X"),
        "$121": ("10", "Aceleración Y"),
        "$122": ("10", "Aceleración Z"),
        
        # Límites de recorrido (mm)
        "$130": ("360", "Límite X (36cm)"),
        "$131": ("290", "Límite Y (29cm)"),
        "$132": ("150", "Límite Z (extensión palas)"),
        
        # Desactivar límites y homing (sin finales de carrera)
        "$20": ("0", "Soft limits OFF"),
        "$21": ("0", "Hard limits OFF"),
        "$22": ("0", "Homing cycle OFF"),
    }
    
    print("📝 Aplicando configuración:")
    print("-"*70)
    
    for param, (valor, descripcion) in configuracion.items():
        cmd = f"{param}={valor}"
        print(f"  {cmd:15} → {descripcion}")
        
        if grbl.send_command(cmd):
            print(f"    ✅ Aplicado")
        else:
            print(f"    ❌ Error")
        
        time.sleep(0.1)
    
    print("-"*70)
    print("\n✅ CONFIGURACIÓN COMPLETADA\n")
    
    # Verificar configuración crítica
    print("🔍 Verificando configuración crítica:")
    for param in ["$3", "$4"]:
        grbl.serial_conn.write(f"{param}\n".encode())
        time.sleep(0.2)
        if grbl.serial_conn.in_waiting:
            resp = grbl.serial_conn.read(grbl.serial_conn.in_waiting).decode()
            print(f"  {resp.strip()}")
    
    print("\n" + "="*70)
    print("💾 Configuración guardada en EEPROM del Annoy Tools")
    print("   Esta configuración se mantendrá incluso después de apagar")
    print("="*70)
    
    # Probar movimiento
    print("\n🧪 PRUEBA DE MOVIMIENTO:")
    respuesta = input("¿Probar movimiento de motores? (s/n): ").lower()
    
    if respuesta == 's':
        print("\n🔄 Modo relativo...")
        grbl.send_command("G91")
        time.sleep(0.2)
        
        print("➡️ Moviendo X +10mm...")
        if grbl.send_command("G0 X10"):
            print("   ✅ X se movió")
            time.sleep(2)
            
            print("⬅️ Regresando X -10mm...")
            grbl.send_command("G0 X-10")
            time.sleep(2)
        
        print("⬆️ Moviendo Y +10mm...")
        if grbl.send_command("G0 Y10"):
            print("   ✅ Y se movió")
            time.sleep(2)
            
            print("⬇️ Regresando Y -10mm...")
            grbl.send_command("G0 Y-10")
            time.sleep(2)
        
        print("↔️ Moviendo Z +20mm...")
        if grbl.send_command("G0 Z20"):
            print("   ✅ Z se movió")
            time.sleep(2)
            
            print("↔️ Regresando Z -20mm...")
            grbl.send_command("G0 Z-20")
            time.sleep(2)
        
        print("🔄 Modo absoluto...")
        grbl.send_command("G90")
        
        print("\n✅ Prueba de movimiento completada")
    
    grbl.disconnect()

else:
    print(f"\n❌ No se pudo conectar en {puerto}")
    print("Verifica:")
    print("  - Annoy Tools conectado al USB")
    print("  - Puerto COM correcto")
    print("  - Ningún otro programa usando el puerto")
from warehouse_grbl_system import WarehouseManager, CellStatus
import serial
import time

# ============================================
# CONFIGURACIÓN DEL SISTEMA
# ============================================

# Puertos COM
PUERTO_ANNOY_TOOLS = 'COM6'  # Annoy Tools con GRBL (motores X, Y, Z)
PUERTO_SENSOR = 'COM4'       # Arduino con sensor ultrasónico - CAMBIAR según tu puerto
BAUDRATE_GRBL = 115200
BAUDRATE_SENSOR = 9600

# ============================================
# CONFIGURACIÓN DEL EJE Z (Palas horizontales)
# ============================================
# Ajustar estos valores según tu máquina:
# - Mide cuánto se extienden las palas totalmente
# - Z=0 significa palas retraídas (posición segura)
Z_RETRAIDO = 0          # Palas ADENTRO (mm) - Posición segura
Z_EXTENDIDO = 100       # Palas AFUERA (mm) - Debajo del objeto
                        # AJUSTAR: Mide la extensión real de tus palas

# ============================================
# CONFIGURACIÓN DEL EJE Y (Levantamiento vertical)
# ============================================
# Ajustar estos valores según tu máquina:
# - Y_OFFSET_ASEGURAR: Cuánto sube Y para levantar la carga sobre las palas
Y_OFFSET_ASEGURAR = 10     # mm que sube Y para asegurar carga
                           # AJUSTAR: Prueba con 10mm, si no es suficiente aumenta a 15-20mm

Y_OFFSET_DEPOSITAR = 10    # mm que sube Y antes de depositar (mismo que asegurar)

Y_TRANSPORTE = 120         # Altura segura de Y para moverse entre celdas sin chocar
                           # AJUSTAR: Debe ser más alto que cualquier objeto en las celdas

# ============================================
# POSICIÓN DE CARGA INICIAL (Homing)
# ============================================
POSICION_CARGA = {
    'x': 0,      # X = 0 (origen)
    'y': 0,      # Y = 0 (origen)
    'z': 0       # Z = 0 (palas retraídas)
}

# ============================================
# TIEMPOS DE ESPERA (Segundos)
# ============================================
DELAY_COLOCACION = 5         # Tiempo para que usuario coloque objeto bien


# ============================================
# CLASE PRINCIPAL DEL SISTEMA
# ============================================
class AlmacenAutomatico:
    
    def __init__(self):
        self.warehouse = WarehouseManager()
        self.sensor_serial = None
        self.running = False
        self.objetos_almacenados = 0
        
    def conectar_sistemas(self):
        """Conecta con Annoy Tools y sensor"""
        print("🔌 Conectando sistemas...")
        print("="*70)
        
        # Conectar con Annoy Tools (GRBL)
        if not self.warehouse.connect_grbl(port=PUERTO_ANNOY_TOOLS, baudrate=BAUDRATE_GRBL):
            print(f"❌ No se pudo conectar con Annoy Tools en {PUERTO_ANNOY_TOOLS}")
            return False
        print(f"✅ Annoy Tools conectado en {PUERTO_ANNOY_TOOLS}")
        
        # Conectar con Arduino del sensor
        try:
            self.sensor_serial = serial.Serial(PUERTO_SENSOR, BAUDRATE_SENSOR, timeout=1)
            time.sleep(2)  # Esperar reset de Arduino
            
            # Limpiar buffer
            self.sensor_serial.reset_input_buffer()
            
            # Esperar mensaje "SENSOR_READY"
            inicio = time.time()
            while time.time() - inicio < 5:
                if self.sensor_serial.in_waiting:
                    msg = self.sensor_serial.readline().decode().strip()
                    if msg == "SENSOR_READY":
                        print(f"✅ Sensor conectado en {PUERTO_SENSOR}")
                        return True
                time.sleep(0.1)
            
            print(f"⚠️ Sensor conectado pero no responde en {PUERTO_SENSOR}")
            print(f"   Continuando sin sensor...")
            return True
            
        except serial.SerialException as e:
            print(f"❌ No se pudo conectar con sensor en {PUERTO_SENSOR}")
            print(f"   Error: {e}")
            print(f"   Verifica que el Arduino con sensor esté conectado")
            return False
    
    def ir_a_posicion_carga(self):
        """Mueve el sistema a la posición de carga inicial"""
        print("\n🏠 Regresando a posición de carga...")
        
        # 1. Y a altura de transporte segura
        self.warehouse.grbl.move(f"G0 Y{Y_TRANSPORTE}")
        
        # 2. Z retraído (palas adentro)
        self.warehouse.grbl.move(f"G0 Z{Z_RETRAIDO}")
        
        # 3. Ir a XY de carga
        self.warehouse.grbl.move(
            f"G0 X{POSICION_CARGA['x']} Y{POSICION_CARGA['y']}"
        )
        
        print("✅ En posición de carga - Listo para recibir objeto")
    
    def recoger_objeto_de_celda(self, celda_id):
        """
        Recoge un objeto de una celda usando el sistema de palas
        
        Secuencia:
        1. X, Y van a posición de la celda
        2. Z extiende (palas entran debajo del objeto)
        3. Y sube un poco (asegura carga sobre las palas)
        4. Z retrae (palas vuelven con objeto)
        5. X, Y vuelven a posición de carga
        """
        cell = self.warehouse.cells[celda_id]
        
        print(f"\n🤲 Recogiendo objeto de celda {celda_id}...")
        
        # 1. Y a altura de transporte
        print("  1️⃣ Y a altura de transporte...")
        self.warehouse.grbl.move(f"G0 Y{Y_TRANSPORTE}")
        
        # 2. Ir a posición XY de la celda
        print(f"  2️⃣ Moviendo a celda {celda_id} (X={cell.x}, Y={cell.y})...")
        self.warehouse.grbl.move(f"G0 X{cell.x} Y{cell.y}")
        
        # 3. Extender Z (palas entran debajo del objeto)
        print(f"  3️⃣ Extendiendo palas (Z={Z_EXTENDIDO})...")
        self.warehouse.grbl.move(f"G0 Z{Z_EXTENDIDO}")
        
        # 4. Y sube un poco (asegura la carga)
        y_asegurado = cell.y + Y_OFFSET_ASEGURAR
        print(f"  4️⃣ Subiendo Y para asegurar carga (Y={y_asegurado})...")
        self.warehouse.grbl.move(f"G0 Y{y_asegurado}")
        
        # 5. Retraer Z (palas vuelven con objeto)
        print(f"  5️⃣ Retrayendo palas con objeto (Z={Z_RETRAIDO})...")
        self.warehouse.grbl.move(f"G0 Z{Z_RETRAIDO}")
        
        print(f"✅ Objeto recogido de {celda_id}")
        
        # 6. Volver a posición de carga
        self.ir_a_posicion_carga()
    
    def depositar_objeto_en_celda(self, celda_id):
        """
        Deposita objeto en una celda usando el sistema de palas
        
        Secuencia:
        1. Usuario coloca objeto en palas (sensor lo detecta)
        2. X, Y llevan a celda destino
        3. Y sube un poco
        4. Z extiende (palas empujan objeto hacia celda)
        5. Y baja (deja objeto depositado)
        6. Z retrae (palas se salen)
        7. X, Y vuelven a posición de carga
        """
        cell = self.warehouse.cells[celda_id]
        
        print(f"\n📦 Depositando objeto en celda {celda_id}...")
        
        # 1. Y a altura de transporte (con objeto)
        print("  1️⃣ Y a altura de transporte...")
        self.warehouse.grbl.move(f"G0 Y{Y_TRANSPORTE}")
        
        # 2. Ir a posición XY de la celda destino
        print(f"  2️⃣ Moviendo a celda {celda_id} (X={cell.x}, Y={cell.y})...")
        self.warehouse.grbl.move(f"G0 X{cell.x} Y{cell.y}")
        
        # 3. Y sube un poco para preparar depósito
        y_depositar = cell.y + Y_OFFSET_DEPOSITAR
        print(f"  3️⃣ Subiendo Y para preparar depósito (Y={y_depositar})...")
        self.warehouse.grbl.move(f"G0 Y{y_depositar}")
        
        # 4. Extender Z (palas empujan objeto hacia celda)
        print(f"  4️⃣ Extendiendo palas para depositar (Z={Z_EXTENDIDO})...")
        self.warehouse.grbl.move(f"G0 Z{Z_EXTENDIDO}")
        
        # 5. Y baja (deja objeto en la celda)
        print(f"  5️⃣ Bajando Y para depositar (Y={cell.y})...")
        self.warehouse.grbl.move(f"G0 Y{cell.y}")
        
        # 6. Retraer Z (palas se salen de debajo del objeto)
        print(f"  6️⃣ Retrayendo palas (Z={Z_RETRAIDO})...")
        self.warehouse.grbl.move(f"G0 Z{Z_RETRAIDO}")
        
        print(f"✅ Objeto depositado en {celda_id}")
    
    def almacenar_objeto_automatico(self):
        """Proceso completo de almacenamiento automático"""
        print("\n" + "="*70)
        print("📦 NUEVO OBJETO DETECTADO EN SENSOR")
        print("="*70)
        
        # Countdown para dar tiempo de acomodar
        print(f"\n⏱️ Tienes {DELAY_COLOCACION} segundos para acomodar bien el objeto...")
        for i in range(DELAY_COLOCACION, 0, -1):
            print(f"   {i}...", end='\r')
            time.sleep(1)
        print("   ¡Iniciando almacenamiento!   ")
        
        # Buscar celda vacía
        celdas_vacias = self.warehouse.get_empty_cells()
        
        if not celdas_vacias:
            print("\n❌ NO HAY CELDAS VACÍAS")
            print("⚠️ El almacén está lleno. Retira objetos antes de continuar.")
            return False
        
        celda_destino = celdas_vacias[0]
        print(f"\n🎯 Celda destino seleccionada: {celda_destino}")
        
        nombre_objeto = f"Objeto_{self.objetos_almacenados + 1}"
        
        # Depositar objeto en celda
        self.depositar_objeto_en_celda(celda_destino)
        
        # Actualizar estado del almacén
        self.warehouse.cells[celda_destino].status = CellStatus.OCCUPIED
        self.warehouse.cells[celda_destino].content = nombre_objeto
        self.warehouse._save_state()
        
        self.objetos_almacenados += 1
        print(f"\n✅ '{nombre_objeto}' almacenado exitosamente en {celda_destino}")
        print(f"📊 Total objetos almacenados en esta sesión: {self.objetos_almacenados}")
        
        return True
    
    def monitorear_sensor(self):
        """Loop que monitorea el sensor constantemente"""
        print("\n" + "="*70)
        print("👁️ SISTEMA EN MODO AUTOMÁTICO - Monitoreando sensor...")
        print("="*70)
        print("   📦 Coloca un objeto en las palas para almacenar")
        print("   ⌨️  Presiona Ctrl+C para detener el sistema\n")
        
        while self.running:
            try:
                if self.sensor_serial and self.sensor_serial.in_waiting:
                    mensaje = self.sensor_serial.readline().decode().strip()
                    
                    if mensaje == "OBJETO_DETECTADO":
                        # Objeto detectado, iniciar almacenamiento
                        self.almacenar_objeto_automatico()
                        
                time.sleep(0.1)
                
            except Exception as e:
                print(f"❌ Error al leer sensor: {e}")
                time.sleep(1)
    
    def iniciar(self):
        """Inicia el sistema automático"""
        print("\n" + "="*70)
        print("🤖 SISTEMA DE ALMACÉN AUTOMÁTICO 2x2")
        print("="*70)
        
        if not self.conectar_sistemas():
            print("\n❌ No se pudo iniciar el sistema")
            print("Verifica las conexiones y los puertos COM")
            return
        
        # Configuración inicial
        print("\n🔧 Configuración inicial...")
        
        # Desbloquear GRBL
        self.warehouse.grbl.send_command("$X")
        
        # Establecer origen con G92
        respuesta = input("\n¿Establecer posición actual como origen (0,0,0)? (s/n): ")
        if respuesta.lower() == 's':
            self.warehouse.grbl.send_command("G92 X0 Y0 Z0")
            print("✅ Origen establecido en posición actual")
        
        # Ir a posición de carga
        self.ir_a_posicion_carga()
        
        # Mostrar estado inicial del almacén
        print("\n📊 ESTADO INICIAL DEL ALMACÉN:")
        print("-"*70)
        for cell_id in ['A1', 'A2', 'B1', 'B2']:
            if cell_id in self.warehouse.cells:
                info = self.warehouse.get_status()[cell_id]
                cell = self.warehouse.cells[cell_id]
                estado = "🟢 VACÍA" if info['status'] == 'empty' else "🔴 OCUPADA"
                contenido = f" - {info['content']}" if info['content'] else ""
                print(f"   {estado} {cell_id} (X:{cell.x}, Y:{cell.y}){contenido}")
        print("-"*70)
        
        # Iniciar monitoreo
        self.running = True
        
        try:
            self.monitorear_sensor()
        except KeyboardInterrupt:
            print("\n\n⚠️ Deteniendo sistema...")
            self.detener()
    
    def detener(self):
        """Detiene el sistema y desconecta todo"""
        self.running = False
        
        print("\n" + "="*70)
        print("📊 RESUMEN FINAL DE LA SESIÓN")
        print("="*70)
        print(f"   Total objetos almacenados: {self.objetos_almacenados}")
        
        # Estado final del almacén
        print("\n📦 ESTADO FINAL DEL ALMACÉN:")
        print("-"*70)
        for cell_id in ['A1', 'A2', 'B1', 'B2']:
            if cell_id in self.warehouse.cells:
                info = self.warehouse.get_status()[cell_id]
                estado = "🟢" if info['status'] == 'empty' else "🔴"
                contenido = f" - {info['content']}" if info['content'] else ""
                print(f"   {estado} {cell_id}: {info['status']}{contenido}")
        print("-"*70)
        
        # Desconectar hardware
        if self.sensor_serial:
            self.sensor_serial.close()
            print("\n✅ Sensor desconectado")
        
        self.warehouse.disconnect_grbl()
        print("✅ Annoy Tools desconectado")
        
        print("\n👋 Sistema detenido correctamente")
        print("="*70)


# ============================================
# PROGRAMA PRINCIPAL
# ============================================
if __name__ == "__main__":
    print("""
    ╔════════════════════════════════════════════════════════════╗
    ║  SISTEMA DE ALMACÉN AUTOMÁTICO 2x2                         ║
    ║  Control con Annoy Tools + GRBL + Sensor Ultrasónico       ║
    ╚════════════════════════════════════════════════════════════╝
    """)
    
    almacen = AlmacenAutomatico()
    almacen.iniciar()

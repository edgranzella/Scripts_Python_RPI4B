import serial
import time

# Configuración constante
RTU_ADDRESS = 230  # 0xE6
# ================= CONFIGURACION DE HARDWARE =================
PORT = '/dev/ttyUSB0'  # Cambiar según corresponda (ej: 'COM3' o '/dev/ttyUSB0')
BAUDRATE = 9600
RTU_ADDRESS = 230      # Dirección fija de la placa PLC

# Configuración 9600,E,8,2 (Importante para RP2040)
ser = serial.Serial(
    port=PORT,
    baudrate=BAUDRATE,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    bytesize=serial.EIGHTBITS,
    timeout=5.0
)

def calculate_checksum(data):
    """
    Calcula el Checksum igual que la Placa PLC.
    Suma de los bytes desde el índice 1 (excluye el LF inicial).
    """
    # Suma todos los bytes excepto el primero (0x0A)
    # El módulo 256 es equivalente al comportamiento de un uint8_t en C
    return sum(data[1:]) % 256

def build_frame(addr, cmd, payload):
    """
    Construye la trama: [LF][ADDR][CMD][DATA...][CSUM][CR]
    """
    frame = bytearray()
    frame.append(0x0A)         # LF (Inicio de trama)
    frame.append(addr)         # Dirección (230)
    frame.append(ord(cmd))     # Comando ASCII
    
    for b in payload:
        frame.append(b)
       
    # Calculamos el checksum antes de cerrar la trama
    # csum = calculate_checksum(frame)
    # frame.append(csum)
    
    frame.append(0x0D)         # CR (Fin de trama)
    return frame

def send_command(comando, payload):
    """
    Construye y envía la trama: [0x0A, ADDR, CMD, DATA1, DATA2, CHK, 0x0D]
    """
    # 1. Iniciar trama con LF (no entra en el checksum según tu protocolo)
    trama = bytearray([0x0A])
    
    # 2. Cuerpo de la trama (estos bytes se suman para el checksum)
    cuerpo = bytearray([RTU_ADDRESS, ord(comando)])
    for p in payload:
        cuerpo.append(p)
        
    # 3. Calcular Checksum (suma del cuerpo % 256)
    checksum = sum(cuerpo) % 256
    # cuerpo.append(checksum)
    
    # 4. Unir todo y cerrar con CR
    trama.extend(cuerpo)
    trama.append(0x0D)
    
    # --- TRANSMISIÓN ---
    # (Asumiendo que 'ser' está definido globalmente o abierto)
    print(f"TX -> {[hex(b) for b in trama]}")
    ser.write(trama)
    time.sleep(1) # Tiempo de espera para respuesta
    
    if ser.in_waiting > 0:
        return ser.read(ser.in_waiting)
    return None

def main():
    print(f"--- Master Central (ID: {RTU_ADDRESS}) ---")
    
    while True:
        print("\n[1] Polling 'P'")
        print("[2] Relés: Activación Permanente (Estado)")
        print("[3] Relés: Generar Pulso")
        print("[4] Salir")
        
        opcion = input("Seleccione: ")

        if opcion == '1':
            # Comando P con payload de relleno (ej. 3 bytes de password o ceros)
            resp = send_command('P', [0x55])
            if resp:
                print(f"RX ({len(resp)} bytes): {resp.hex().upper()}")
            else:
                print("ERROR: Sin respuesta")

        elif opcion == '2':
            rele = int(input("Relé para ESTADO PERMANENTE (1-8): "))
            accion = int(input("Acción (1: ON, 0: OFF): "))
            
            bitmask = 1 << (rele - 1)
            # sndbuffer[3] = Estado, sndbuffer[4] = Pulso (0x00)
            payload = [bitmask if accion == 1 else 0x00, 0x00]
            
            print(f"Enviando Estado {'ON' if accion == 1 else 'OFF'} a Relé {rele}...")
            send_command('R', payload)

        elif opcion == '3':
            rele = int(input("Relé para PULSO (1-8): "))
            
            bitmask = 1 << (rele - 1)
            # sndbuffer[3] = Estado (0x00), sndbuffer[4] = Pulso (bitmask)
            payload = [0x00, bitmask]
            
            print(f"Enviando PULSO a Relé {rele}...")
            send_command('R', payload)

        elif opcion == '4':
            print("Saliendo...")
            break
        else:
            print("Opción no válida.")

# Nota: Asegúrate de inicializar el objeto 'ser' antes de llamar a main()
# ser = serial.Serial('/dev/ttyUSB0', 9600, parity=serial.PARITY_EVEN, stopbits=serial.STOPBITS_TWO, timeout=1)

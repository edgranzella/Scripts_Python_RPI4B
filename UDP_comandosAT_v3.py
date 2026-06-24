import serial
import time

PORT = '/dev/ttyAMA3'
BAUD = 9600
MAX_REINTENTOS = 3

WAIT_CORTO   = 1.5
WAIT_NETOPEN = 4.0
WAIT_CIPOPEN = 5.0

MENSAJES = [
    "Hola que tal como va",
    "Datos de prueba enviados",
    "Ahora cerramos la conexion",
]

def send_at(ser, command, wait=WAIT_CORTO, back="OK"):
    ser.read(ser.in_waiting)
    ser.write((command + '\r\n').encode())
    time.sleep(wait)
    res = ""
    if ser.in_waiting > 0:
        res = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    print(f"  [AT] >>> {command}")
    print(f"  [AT] <<< {res.strip()}")
    return res if back in res else None

def esperar_modem_listo(ser, timeout_seg=60):
    import sys
    import select
    print(f"[*] Esperando MODEM_LISTO (max {timeout_seg}s)...")
    print(f"[*] Presiona 's' + Enter para forzar continuacion.")
    intentos_s = 0
    timeout = time.time() + timeout_seg
    while time.time() < timeout:
        if ser.in_waiting > 0:
            linea = ser.readline().decode('utf-8', errors='ignore').strip()
            if linea:
                print(f"  [STM32] {linea}")
            if "MODEM_LISTO" in linea:
                print("[OK] MODEM_LISTO recibido.")
                return True
        if select.select([sys.stdin], [], [], 0)[0]:
            tecla = sys.stdin.readline().strip().lower()
            if tecla == 's':
                intentos_s += 1
                if intentos_s == 1:
                    print("[!] Modem aun no confirmo la red. Presiona 's' de nuevo para forzar igual.")
                else:
                    print("[OK] Forzando continuacion por comando manual.")
                    return True
        time.sleep(0.1)
    return False

def enviar_y_recibir_udp(ser, msg):
    ser.read(ser.in_waiting)
    msg_bytes = msg.encode()
    largo = len(msg_bytes)

    # Igual que TCP: comando + datos + \r concatenados en un solo bloque
    # El \r final dispara el idle timeout del STM32 para liberar los datos
    paquete = f'AT+CIPSEND=0,{largo}\r\n'.encode() + msg_bytes + b'\r'
    ser.write(paquete)
    print(f"  >> Enviados {largo} bytes: '{msg}'")

    # UDP no tiene ACK de capa de transporte — timeout mayor que TCP
    timeout = time.time() + 15
    buffer_acum = ""
    while time.time() < timeout:
        if ser.in_waiting > 0:
            chunk = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
            buffer_acum += chunk
            print(f"  >> chunk: '{chunk.strip()}'")
            if "+CIPRXGET: 1" in buffer_acum:
                print("  >> Datagrama recibido!")
                time.sleep(0.5)
                ser.read(ser.in_waiting)
                ser.write('AT+CIPRXGET=2,0,100\r\n'.encode())
                time.sleep(2.0)
                respuesta = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                print(f"  >> RESPUESTA: {respuesta.strip()}")
                return True
        time.sleep(0.2)

    print(f"  >> Timeout UDP. Acumulado: '{buffer_acum.strip()}'")
    return False

try:
    ser = serial.Serial(PORT, BAUD, timeout=2)
    print(f"--- UDP v3: UART {PORT} @ {BAUD} -> Black Pill -> A7670SA ---\n")

    if not esperar_modem_listo(ser):
        print("Error: modem no listo en 60s.")
        ser.close()
        exit(1)

    print("\n[OK] Iniciando secuencia AT...\n")
    time.sleep(0.5)
    ser.read(ser.in_waiting)

    print("[0] Desactivando eco del modem...")
    send_at(ser, 'ATE0')

    print("\n[1] Configurando red...")
    send_at(ser, 'AT+CGDCONT=1,"IP","internet.movistar.arg"')
    send_at(ser, 'AT+NETOPEN', wait=WAIT_NETOPEN, back="+NETOPEN")
    send_at(ser, 'AT+CIPRXGET=1')

    print("\n[2] Abriendo socket UDP...")
    # UDP: "UDP" en lugar de "TCP", puerto local 0 = asignado automaticamente
    if send_at(ser, 'AT+CIPOPEN=0,"UDP","190.111.217.188",57777,0',
               wait=WAIT_CIPOPEN, back="+CIPOPEN: 0,0"):

        print("\n[3] Socket UDP abierto. Enviando datagramas...\n")

        for idx, msg in enumerate(MENSAJES, start=1):
            print(f"[Datagrama {idx}/{len(MENSAJES)}] '{msg}'")
            exito = False
            for intento in range(1, MAX_REINTENTOS + 1):
                print(f"  [Intento {intento}] Enviando...")
                if enviar_y_recibir_udp(ser, msg):
                    exito = True
                    break
                print(f"  Reintentando...")
            if not exito:
                print(f"  Error: Agotados los reintentos para datagrama {idx}.")

        print("\n[4] Cerrando socket UDP...")
        send_at(ser, 'AT+CIPCLOSE=0', back="+CIPCLOSE")
    else:
        print("Error: No se pudo abrir el socket UDP.")

    print("\n[5] Cerrando red...")
    send_at(ser, 'AT+NETCLOSE', back="+NETCLOSE")
    ser.close()
    print("\n--- Fin ---")

except Exception as e:
    print(f"Error critico: {e}")
    


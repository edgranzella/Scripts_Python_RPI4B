import serial
import time

# Configuración del puerto

PORT = '/dev/ttyUSB1'
BAUD = 115200
MAX_REINTENTOS = 3

MENSAJES = [
    "Hola que tal como va",
    "Datos de prueba enviados",
    "Ahora cerramos la conexión",
]

def send_at(ser, command, wait=1, back="OK"):
    ser.write((command + '\r\n').encode())
    time.sleep(wait)
    if ser.in_waiting > 0:
        res = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
        return res if back in res else None
    return None

def enviar_y_recibir(ser, msg):
    """Envía un mensaje y espera respuesta del servidor. Devuelve True si hubo respuesta."""
    ser.read(ser.in_waiting)  # Limpiar buffer

    ser.write(f'AT+CIPSEND=0,{len(msg.encode())}\r\n'.encode())
    time.sleep(0.5)
    ser.write(msg.encode())

    timeout = time.time() + 5
    while time.time() < timeout:
        if ser.in_waiting > 0:
            linea = ser.readline().decode('utf-8', errors='ignore')
            if "+CIPRXGET: 1" in linea:
                print(">> ¡Datos recibidos en el módulo!")
                respuesta = send_at(ser, 'AT+CIPRXGET=2,0,50')
                if respuesta:
                    print(f">> RESPUESTA SERVIDOR: {respuesta.strip()}")
                return True
        time.sleep(0.2)

    print(">> Sin respuesta del servidor.")
    return False

try:
    ser = serial.Serial(PORT, BAUD, timeout=1)
    print(f"--- Iniciando TCP con Reintentos en {PORT} ---")

    # 1. Decirle al módulo que mande las respuestas de red a TODAS las interfaces
    # send_at(ser, 'AT+CURCCFG="common",1') 

    # 2. Asegurarnos de que el puerto UART esté en modo "reporte"
    # send_at(ser, 'AT+CSCLK=0') 
    
    # 1. Preparar Red
    send_at(ser, 'AT+CGDCONT=1,"IP","internet.movistar.arg"')
    send_at(ser, 'AT+NETOPEN')
    send_at(ser, 'AT+CIPRXGET=1')  # Modo manual de recepción

    # 2. Intentar conexión al Socket
    if send_at(ser, 'AT+CIPOPEN=0,"TCP","190.111.217.188",57777', wait=3, back="+CIPOPEN: 0,0"):

        # 3. Enviar cada mensaje con reintentos independientes
        for idx, msg in enumerate(MENSAJES, start=1):
            print(f"\n[Mensaje {idx}/{len(MENSAJES)}] '{msg}'")
            exito = False

            for intento in range(1, MAX_REINTENTOS + 1):
                print(f"  [Intento {intento}] Enviando...")
                if enviar_y_recibir(ser, msg):
                    exito = True
                    break
                print(f"  Reintentando...")

            if not exito:
                print(f"  Error: Agotados los reintentos para el mensaje {idx}.")

        send_at(ser, 'AT+CIPCLOSE=0')

    send_at(ser, 'AT+NETCLOSE')
    ser.close()

except Exception as e:
    print(f"Error crítico: {e}")
    

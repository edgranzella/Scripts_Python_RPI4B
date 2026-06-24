import serial
import time

# --- Configuración del sistema ---
PORT = '/dev/ttyAMA3'  # UART hacia la Black Pill
BAUD = 9600
IP_SERVIDOR = "190.111.217.188"
PUERTO_UDP = 57777

# Tiempos de espera
WAIT_NET  = 5.0
WAIT_AT   = 1.0
WAIT_CIPOPEN = 3.0

def enviar_at(ser, comando, espera=WAIT_AT, exito="OK"):
    """Envía un comando y retorna la respuesta."""
    print(f"  [TX] > {comando}")
    ser.write((comando + '\r\n').encode())
    time.sleep(espera)
    if ser.in_waiting > 0:
        res = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
        print(f"  [RX] < {res.strip()}")
        return res
    return ""

def flujo_udp_completo(ser, mensaje):
    """Realiza la secuencia completa de conexión, envío y cierre."""
    try:
        # 1. LIMPIEZA INICIAL
        print("\n--- PASO 1: Reset de Contexto ---")
        enviar_at(ser, 'AT+NETCLOSE', espera=2.0)
        enviar_at(ser, 'AT+CIPCLOSE=0', espera=1.0)
        
        # 2. CONFIGURACIÓN
        print("\n--- PASO 2: Configuración de Red ---")
        enviar_at(ser, 'AT+CGDCONT=1,"IP","internet.movistar.arg"')
        enviar_at(ser, 'AT+CSOCKSETPN=1')
        
        # 3. CONEXIÓN
        print("\n--- PASO 3: Apertura de Red ---")
        res_net = enviar_at(ser, 'AT+NETOPEN', espera=WAIT_NET)
        if "+NETOPEN: 0" not in res_net and "already opened" not in res_net:
            print("  [!] Error al abrir red.")
            return False
            
        enviar_at(ser, 'AT+IPADDR')

        # 4. APERTURA DE SOCKET
        print("\n--- PASO 4: Apertura de Socket UDP ---")
        cmd_open = f'AT+CIPOPEN=0,"UDP","{IP_SERVIDOR}",{PUERTO_UDP},0'
        res_open = enviar_at(ser, cmd_open, espera=WAIT_CIPOPEN)
        if "+CIPOPEN: 0,0" not in res_open and "already connected" not in res_open:
            print("  [!] No se pudo abrir el socket.")
            return False
        
        # 5. ENVÍO
        # Calculamos los bytes reales del mensaje ingresado
        bytes_msg = len(mensaje.encode('utf-8'))
        print(f"\n--- PASO 5: Envío UDP ({bytes_msg} bytes) ---")
        cmd_send = f'AT+CIPSEND=0,{bytes_msg},"{IP_SERVIDOR}",{PUERTO_UDP}'
        ser.write((cmd_send + '\r\n').encode())

        # Esperar el prompt '>'
        timeout = time.time() + 3
        while time.time() < timeout:
            if ser.in_waiting > 0:
                chunk = ser.read(ser.in_waiting)
                if b'>' in chunk:
                    print("  [>] Prompt detectado. Transmitiendo...")
                    ser.write(mensaje.encode())
                    time.sleep(2)
                    confirmacion = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    print(f"  [OK] Respuesta módem:\n{confirmacion.strip()}")
                    break
            time.sleep(0.1)

        # 6. CIERRE DE SEGURIDAD (Limpieza para la próxima vuelta)
        print("\n--- PASO 6: Cierre de Sesión ---")
        enviar_at(ser, 'AT+CIPCLOSE=0')
        enviar_at(ser, 'AT+NETCLOSE')
        return True

    except Exception as e:
        print(f"Error en el flujo: {e}")
        return False

# =============================================================================
if __name__ == "__main__":
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
        print(f"--- Sistema Interactivo UDP A7670SA ---")
        
        while True:
            # Pedir mensaje por teclado
            msg_usuario = input("\nIngrese mensaje a enviar (o escriba 'fin' para salir): ")
            
            # Condición de salida
            if msg_usuario.lower() == "fin":
                print("Finalizando script y cerrando conexiones...")
                # Aseguramos cierre total antes de salir
                enviar_at(ser, 'AT+CIPCLOSE=0')
                enviar_at(ser, 'AT+NETCLOSE')
                break
            
            # Ejecutar el flujo completo para cada mensaje
            if not flujo_udp_completo(ser, msg_usuario):
                print("\n[!] El envío falló. Intentelo de nuevo.")
            else:
                print("\n[✔] Ciclo de envío terminado.")

        ser.close()
        print("Puerto serial cerrado. Adiós.")

    except Exception as e:
        print(f"Error crítico: {e}")
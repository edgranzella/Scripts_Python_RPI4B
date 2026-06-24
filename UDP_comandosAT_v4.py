import serial
import time

# --- Configuración del sistema ---
PORT = '/dev/ttyAMA3' # UART hacia la Black Pill
BAUD = 9600
IP_SERVIDOR = "190.111.217.188"
PUERTO_UDP = 57777

# Tiempos de espera basados en tus pruebas manuales
WAIT_NET  = 5.0
WAIT_AT   = 1.0
WAIT_CIPOPEN = 3.0

def enviar_at(ser, comando, espera=WAIT_AT, exito="+"):
    """Envía un comando y retorna la respuesta si contiene el éxito esperado."""
    print(f"  [TX] > {comando}")
    ser.write((comando + '\r\n').encode())
    time.sleep(espera)
    if ser.in_waiting > 0:
        res = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
        print(f"  [RX] < {res.strip()}")
        return res
    return ""

def flujo_udp_completo(ser, mensaje):
    try:
        # 1. LIMPIEZA INICIAL (Empezar de cero)
        print("\n--- PASO 1: Reset de Contexto ---")
        enviar_at(ser, 'AT+NETCLOSE', espera=2.0) # Cerrar cualquier red abierta
        # enviar_at(ser, 'AT+CFUN=0', espera=3.0)
        # enviar_at(ser, 'AT+CFUN=1', espera=5.0) # Despertar radio
        
        # 2. CONFIGURACIÓN (Vínculo APN-Socket)
        print("\n--- PASO 2: Configuración de Red ---")
        enviar_at(ser, 'AT+CGDCONT=1,"IP","internet.movistar.arg"')
        enviar_at(ser, 'AT+CSOCKSETPN=1') # El "secreto" del éxito
        
        # 3. CONEXIÓN
        print("\n--- PASO 3: Apertura de Red ---")
        res_net = enviar_at(ser, 'AT+NETOPEN', espera=WAIT_NET)
        if "+NETOPEN: 0" not in res_net and "already opened" not in res_net:
            print("  [!] Error al abrir red.")
            return False
            
        # Validación de IP (Tu termómetro de éxito)
        res_ip = enviar_at(ser, 'AT+IPADDR')
        if "+IPADDR:" not in res_ip:
            print("  [!] El módem no obtuvo IP.")
            return False

        # 4. APERTURA DE SOCKET (Comando solicitado)
        print("\n--- PASO 4: Apertura de Socket UDP ---")
        # Sintaxis: AT+CIPOPEN=<link_id>,<type>,<remote_ip>,<remote_port>,<local_port>
        cmd_open = f'AT+CIPOPEN=0,"UDP","{IP_SERVIDOR}",{PUERTO_UDP},0'
        res_open = enviar_at(ser, cmd_open, espera=WAIT_CIPOPEN)
        
        if "+CIPOPEN: 0,0" not in res_open and "already connected" not in res_open:
            print("  [!] No se pudo abrir el socket UDP.")
            return False
        
        # 5. ENVÍO DIRECTO
        print(f"\n--- PASO 5: Envío UDP ({len(mensaje)} bytes) ---")
        cmd_send = f'AT+CIPSEND=0,{len(mensaje)},"{IP_SERVIDOR}",{PUERTO_UDP}'
        ser.write((cmd_send + '\r\n').encode())

        # Esperar el prompt '>'
        timeout = time.time() + 3
        while time.time() < timeout:
            if ser.in_waiting > 0:
                if b'>' in ser.read(ser.in_waiting):
                    print("  [>] Prompt detectado. Transmitiendo...")
                    ser.write(mensaje.encode())
                    time.sleep(2)
                    confirmacion = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    print(f"  [OK] Respuesta: {confirmacion.strip()}")
                    break
            time.sleep(0.1)

        # 5. CIERRE (Para la próxima ejecución)
        print("\n--- PASO 5: Cierre de Seguridad ---")
        enviar_at(ser, 'AT+NETCLOSE')
        # enviar_at(ser, 'AT+CFUN=0', espera=2.0)
        return True

    except Exception as e:
        print(f"Error en el flujo: {e}")
        return False

# =============================================================================
if __name__ == "__main__":
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
        print(f"--- Iniciando Automatización UDP A7670SA ---")
        
        msg_prueba = "Alarma dgranzella OK" # 20 bytes exactos
        if flujo_udp_completo(ser, msg_prueba):
            print("\n¡Ciclo completado con éxito!")
        else:
            print("\nEl ciclo falló en algún punto.")
            
        ser.close()
    except Exception as e:
        print(f"No se pudo abrir el puerto: {e}")

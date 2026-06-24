import socket
import subprocess
import time

# Configuración
TCP_IP = '190.111.217.188'
TCP_PORT = 57777
MESSAGES = [
    "Hola que tal como va",
    "Datos de prueba enviados",
    "Ahora cerramos la conexión",
]

INTERFACE = "ppp0"
TIMEOUT_SOCKET = 10  # segundos


def get_ppp0_ip(interface=INTERFACE):
    """
    Obtiene dinámicamente la IP de la interfaz indicada.
    Devuelve la IP como string, o None si la interfaz no existe o no tiene IP.
    """
    try:
        # 'ip addr show ppp0' es más confiable que ifconfig en sistemas modernos
        result = subprocess.run(
            ["ip", "addr", "show", interface],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None  # La interfaz no existe

        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                # Formato: inet 10.x.x.x/32 ...
                ip = line.split()[1].split("/")[0]
                return ip

        return None  # Interfaz existe pero sin IP asignada aún

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def check_wvdial_active():
    """
    Comprueba si el servicio wvdial está corriendo mediante systemctl.
    Devuelve True si está activo, False en caso contrario.
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "wvdial"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Si systemctl no está disponible, intentamos con pgrep como fallback
        try:
            result = subprocess.run(
                ["pgrep", "-x", "wvdial"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False


def verificar_interfaz():
    """
    Verifica el estado de wvdial y de la interfaz ppp0.
    Devuelve la IP si todo está OK, o lanza una excepción descriptiva.
    """
    print(f"[*] Verificando servicio wvdial...")
    if not check_wvdial_active():
        raise RuntimeError(
            "El servicio wvdial NO está activo. "
            "Inicialo con: sudo systemctl start wvdial"
        )
    print(f"[✓] wvdial está corriendo.")

    print(f"[*] Buscando interfaz '{INTERFACE}'...")
    ip = get_ppp0_ip()
    if ip is None:
        raise RuntimeError(
            f"La interfaz '{INTERFACE}' no está disponible o no tiene IP asignada. "
            f"Verificá la conexión del módem."
        )
    print(f"[✓] Interfaz '{INTERFACE}' activa con IP: {ip}")
    return ip


def enviar_y_recibir(s, msg):
    """Envía un mensaje por el socket y espera el eco del servidor."""
    print(f"  >> Enviando : '{msg}'")
    s.send(msg.encode())
    data = s.recv(1024)
    print(f"  << Recibido : '{data.decode(errors='ignore')}'")


# ── MAIN ──────────────────────────────────────────────────────────────────────

try:
    # 1. Verificar wvdial y obtener IP dinámica de ppp0
    interface_ip = verificar_interfaz()

    # 2. Crear y vincular el socket a ppp0
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT_SOCKET)
    s.bind((interface_ip, 0))  # Puerto 0 = el SO elige uno libre

    # 3. Conectar al servidor remoto
    print(f"\n[*] Conectando a {TCP_IP}:{TCP_PORT} vía {INTERFACE} ({interface_ip})...")
    s.connect((TCP_IP, TCP_PORT))
    print(f"[✓] Conexión establecida.\n")

    # 4. Enviar los tres mensajes
    for idx, msg in enumerate(MESSAGES, start=1):
        print(f"[Mensaje {idx}/{len(MESSAGES)}]")
        enviar_y_recibir(s, msg)
        time.sleep(0.5)  # Pequeña pausa entre mensajes

    s.close()
    print("\n[✓] Conexión cerrada correctamente.")

except RuntimeError as e:
    # Errores de verificación (wvdial / ppp0)
    print(f"\n[✗] Error de red: {e}")

except socket.timeout:
    print(f"\n[✗] Timeout: el servidor no respondió en {TIMEOUT_SOCKET} segundos.")

except ConnectionRefusedError:
    print(f"\n[✗] Conexión rechazada por {TCP_IP}:{TCP_PORT}.")

except Exception as e:
    print(f"\n[✗] Error inesperado: {e}")

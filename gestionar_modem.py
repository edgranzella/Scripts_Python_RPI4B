"""
===============================================================================
README & GUÍA DE INSTALACIÓN: GESTIÓN AUTOMÁTICA SIMCom A7670SA (RNDIS)
===============================================================================
Proyecto: Scripts_Python_RPI4B
Plataforma: Raspberry Pi 4B (Linux / Debian)
Hardware: Módulo LTE Cat 1 SIMCom A7670SA (Conexión USB-C)

DESCRIPCIÓN:
  Este script automatiza de extremo a extremo la inicialización y configuración
  del módem celular SIMCom A7670SA operando en modo RNDIS (Ethernet sobre USB).
  Evita conflictos con NetworkManager al gestionar la interfaz de red de forma
  estática, fija y aislada.

===============================================================================
PASOS PREVIOS OBLIGATORIOS PARA QUE EL PROYECTO FUNCIONE
===============================================================================

PASO 1: ASEGURAR LA ALIMENTACIÓN (HARDWARE)
  El módulo SIMCom A7670SA consume picos de hasta 2A cuando transmite en 4G.
  - Conéctalo SIEMPRE a los puertos USB 3.0 de la Raspberry Pi 4B (los azules).
  - Si notas desconexiones constantes (el dispositivo cambia de número en dmesg),
    deberás usar un HUB USB con alimentación externa independiente.

PASO 2: INSTALAR DEPENDENCIAS DE PYTHON
  Ejecuta en la terminal para instalar el módulo serie de Python:
  $ pip3 install pyserial

PASO 3: CEGAR A NETWORKMANAGER (EVITAR SECUESTRO DE LA INTERFAZ)
  Para evitar que NetworkManager intente asignarle IPs dinámicas por DHCP y te
  rompa las métricas de internet de la interfaz física (eth0):
  
  1. Abre el archivo de configuración:
     $ sudo nano /etc/NetworkManager/NetworkManager.conf
  2. Asegúrate de agregar la regla 'usb*' en la sección [keyfile] para que quede así:
     [main]
     plugins=ifupdown,keyfile
     
     [keyfile]
     unmanaged-devices=interface-name:usb*
  3. Guarda los cambios (Ctrl+O, Enter, Ctrl+X) y reinicia el servicio:
     $ sudo systemctl restart NetworkManager

PASO 4: LIMPIAR RESIDUOS DE RED ANTES DE INICIAR (TRUCO TÉCNICO)
  Si la interfaz usb0 se quedó colgada con IPs viejas de NetworkManager, puedes
  limpiarla completamente en frío ejecutando:
  $ sudo ip addr flush dev usb0

===============================================================================
COMPORTAMIENTO DEL TRÁFICO Y MÉTRICAS (¿CÓMO FUNCIONA?)
===============================================================================
  El script asigna la IP fija 192.168.0.100 a la Raspberry Pi y levanta la ruta 
  hacia el módem (192.168.0.1) usando una Métrica Alta (200). 
  
  - Tu Raspberry Pi seguirá navegando por cable (eth0) de forma predeterminada.
  - El canal 4G del módem queda libre y aislado para peticiones selectivas.

  Para probar o forzar el internet del módem 4G desde la consola sin interferir
  con tu conexión ethernet actual, ejecuta:
  $ curl --interface usb0 https://ifconfig.me

===============================================================================
ANEXO: EL SCRIPT DE BASH EQUIVALENTE (MANUAL)
===============================================================================
Si alguna vez necesitas replicar la configuración de red en Linux de forma manual
sin ejecutar Python, los comandos exactos secuenciales son:

  #!/bin/bash
  sudo ip link set usb0 down                           # Apaga la interfaz
  sudo ip addr flush dev usb0                          # Limpia cualquier IP residual
  sudo ip link set usb0 up                             # Enciende la interfaz físicamente
  sudo ip addr add 192.168.0.100/24 dev usb0           # Asigna la IP estática fija
  sudo ip route add default via 192.168.0.1 dev usb0 metric 200 # Inyecta la métrica

===============================================================================
MODO DE USO DEL SCRIPT DE PYTHON:
===============================================================================
  Como el script interactúa con los subsistemas de red del núcleo de Linux, es
  estrictamente obligatorio ejecutarlo con privilegios de superusuario (sudo):
  
  $ sudo python3 gestionar_modem.py
===============================================================================
"""

import serial
import time
import subprocess
import sys

# --- CONFIGURACIÓN ---
PUERTO_SERIE = "/dev/ttyACM0"
INTERFAZ_RED = "usb0"
IP_ESTATICA = "192.168.0.100/24"
IP_MODEM = "192.168.0.1"
APN = "tu_apn_aqui"  # <-- REEMPLAZA CON EL APN DE TU OPERADOR (ej: ://personal.com)

def enviar_comando_at(ser, comando, respuesta_esperada="OK", timeout=2):
    """Envía un comando AT al módem y valida la respuesta."""
    print(f"[AT] Enviando: {comando}")
    ser.write((comando + "\r\n").encode())
    time.sleep(0.5)
    
    fin = time.time() + timeout
    salida = ""
    while time.time() < fin:
        if ser.in_waiting:
            salida += ser.read(ser.in_waiting).decode(errors="ignore")
            if respuesta_esperada in salida:
                print(f"[MODEM]: {salida.strip()}")
                return True, salida
    print(f"[WARN] No se obtuvo la respuesta esperada para: {comando}. Salida: {salida.strip()}")
    return False, salida

def configurar_modem():
    """Se conecta por puerto serie para preparar el SIMCom A7670SA."""
    print("\n=== 1. CONFIGURANDO FIRMWARE DEL MÓDEM VIA SERIE ===")
    try:
        # Abrimos el puerto serie a 115200 (baudrate por defecto de la serie A7670)
        with serial.Serial(PUERTO_SERIE, 115200, timeout=1) as ser:
            # Comando de prueba básico
            enviar_comando_at(ser, "AT")
            
            # Verificar si la SIM está lista
            enviar_comando_at(ser, "AT+CPIN?")
            
            # Configurar el APN del operador
            enviar_comando_at(ser, f'AT+CGDCONT=1,"IP","{APN}"')
            
            # Activar el auto-marcado y el modo RNDIS nativo
            enviar_comando_at(ser, "AT+DIALMODE=0")
            enviar_comando_at(ser, "AT+CUSBPIDSWITCH=2")
            
            # Consultar estado de registro en la antena celular
            enviar_comando_at(ser, "AT+CPSI?")
            print("[OK] Configuración interna del módem completada.")
            return True
    except serial.SerialException:
        print(f"[ERROR] No se pudo abrir el puerto {PUERTO_SERIE}. ¿Está conectado el cable?")
        return False

def configurar_red_linux():
    """Configura la interfaz de red usb0 de forma estática en Linux."""
    print("\n=== 2. CONFIGURANDO INTERFAZ DE RED FIJA (LINUX) ===")
    try:
        # 1. Limpiar IPs previas y encender la interfaz
        subprocess.run(["sudo", "ip", "addr", "flush", "dev", INTERFAZ_RED], check=True)
        subprocess.run(["sudo", "ip", "link", "set", INTERFAZ_RED, "up"], check=True)
        time.sleep(1)
        
        # 2. Asignar la IP fija a la Raspberry Pi
        subprocess.run(["sudo", "ip", "addr", "add", IP_ESTATICA, "dev", INTERFAZ_RED], check=True)
        print(f"[OK] IP estática {IP_ESTATICA} asignada a {INTERFAZ_RED}")
        
        # 3. Configurar métrica de ruteo alta (200) para no tumbar eth0
        # Intentamos borrar una ruta previa por si existía y agregamos la nueva
        subprocess.run(["sudo", "ip", "route", "del", "default", "via", IP_MODEM, "dev", INTERFAZ_RED], stderr=subprocess.DEVNULL)
        subprocess.run(["sudo", "ip", "route", "add", "default", "via", IP_MODEM, "dev", INTERFAZ_RED, "metric", "200"], check=True)
        print(f"[OK] Ruta configurada con métrica 200 (Tráfico secundario).")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Falló el comando del sistema: {e}")
        return False

def verificar_ping():
    """Verifica si la Raspberry Pi ve al módem a nivel de red."""
    print("\n=== 3. VERIFICANDO CONECTIVIDAD ===")
    res = subprocess.run(["ping", "-c", "3", IP_MODEM], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode == 0:
        print(f"[ÉXITO] ¡Conexión establecida! El módem ({IP_MODEM}) responde correctamente.")
        print(f"-> Para probar el internet del módem ejecuta: curl --interface {INTERFAZ_RED} https://ifconfig.me")
    else:
        print("[ERROR] No hay respuesta de ping del módem. Revisa si el dispositivo sigue conectado.")

if __name__ == "__main__":
    # Asegurar que requiera privilegios para los comandos de red
    if subprocess.run(["id", "-u"], stdout=subprocess.PIPE).stdout.decode().strip() != "0":
        print("[!] Tip: Recuerda que para configurar interfaces de red necesitarás ejecutar con sudo.")
        
    configurar_modem()
    if configurar_red_linux():
        verificar_ping()

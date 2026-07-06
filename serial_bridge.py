#!/usr/bin/env python3
"""
Bridge serie: /dev/ttyUSB0 (panel de alarma, protocolo R3K/Contact ID)
          <-> /dev/ttyAMA3 (controlador STM32F411 / modem)

Además, expone un puerto virtual (pty) que espeja todo el tráfico real de
AMA3 en ambos sentidos, para poder abrir minicom sobre ese puerto virtual
y así interactuar manualmente con el STM32F411 mientras el script sigue
inyectando en simultáneo las tramas que llegan de la central de alarmas.

IMPORTANTE: no abrir minicom sobre /dev/ttyAMA3 directamente. Este script
necesita tenerlo abierto en exclusiva. Abrir minicom sobre el puerto
virtual que el script imprime al arrancar (por defecto /tmp/ttyAMA3_mirror):

    minicom -b 9600 -o -D /tmp/ttyAMA3_mirror

Config real de ambos puertos físicos: 9600 baudios, 8 bits de datos,
paridad PAR (Even), 2 bits de stop (8E2). Ver ProtR3KCID.md para el
detalle del protocolo. El puerto virtual (pty) no tiene parámetros de
línea reales; minicom puede abrirlo igual (usar -o para que no intente
inicializar un módem).

Ambos archivos abren en modo append con buffering=1 (line-buffered), 
así que podés hacer tail -f logs/bridge_*.log en otra terminal mientras el bridge corre, 
y ver el log en vivo.

Comportamiento:
  USB0 -> AMA3 (real):
    - Todo byte recibido de la central se reenvía de inmediato al AMA3
      real (sin esperar el encuadre, para no introducir latencia).
    - Esos mismos bytes se espejan también al puerto virtual, para que
      minicom vea en pantalla las tramas que se están tunelando.
    - En paralelo se reconoce el encuadre real del protocolo para
      mostrarlo prolijo en la consola del script:
        * Evento CID: termina en 0x14 0x0A 0x0D (23 bytes totales).
        * Heartbeat:  arranca con "@@HB", longitud fija de 25 bytes,
          con los campos no imprimibles mostrados en hex (0xXX).
      Si aparece algo que no matchea ninguno de los dos formatos, se
      vuelca como texto "sin encuadre" para no perder datos.

  AMA3 (real) -> USB0 / minicom:
    - Todo lo que el STM32F411 escribe en AMA3 se espeja íntegro hacia
      el puerto virtual (minicom lo ve tal cual).
    - De ese mismo tráfico, el script identifica el byte 0x06 (ACK) y
      lo reenvía a la central de alarmas por USB0 (FORWARD_ACK_TO_CENTRAL).
    - El resto de los datos (debug, prompts, respuestas a comandos
      tipeados en minicom, etc.) se muestra en la consola del script
      pero no se reenvía a USB0.

  minicom -> AMA3 (real):
    - Lo que se tipea en minicom llega al puerto virtual y el script lo
      reenvía tal cual al AMA3 real (interacción manual con el STM32).

Requisitos:
    pip install pyserial

Logging a archivo:
    Todo lo que se muestra en consola (CID, HB, ACK, STM32, TX, RAW, ERROR)
    se guarda también en un archivo de log con fecha/hora completa, en la
    carpeta LOG_DIR (por defecto "logs/"), con un nombre por sesión, ej.:
        logs/bridge_20260703_105530.log
    Además, se guarda un volcado crudo en hexadecimal de absolutamente todo
    lo que llega por AMA3 (antes de cualquier interpretación), en:
        logs/ama3_raw_20260703_105530.log
    Este segundo archivo es el más útil para depurar fallas de comunicación
    a nivel de bytes (basura, tramas cortadas, timing entre lecturas), ya
    que no depende de que el parser haya reconocido bien la trama.

Uso:
    python3 serial_bridge.py
    (en otra terminal: minicom -b 9600 -o -D /tmp/ttyAMA3_mirror)
    Ctrl+C en el script para salir.
"""

import os
import pty
import select
import sys
import time
import tty
import threading
from datetime import datetime

try:
    import serial
except ImportError:
    print("Falta pyserial. Instalar con: pip install pyserial")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuración general
# ---------------------------------------------------------------------------
PORT_USB = '/dev/ttyUSB0'    # Panel de alarma (via conversor USB-RS232)
PORT_AMA3 = '/dev/ttyAMA3'   # Controlador STM32F411 / modem (puerto REAL)
MIRROR_PATH = '/tmp/ttyAMA3_mirror'  # Puerto virtual para minicom

BAUDRATE = 9600
BYTESIZE = serial.EIGHTBITS
PARITY = serial.PARITY_EVEN
STOPBITS = serial.STOPBITS_TWO

SILENCE_TIMEOUT = 0.3   # seg. de silencio para "cerrar" datos sin encuadre reconocido
MAX_UNFRAMED = 200      # bytes máx. acumulados sin lograr reconocer una trama

# --- Formato del protocolo (ProtR3KCID.md) ---
HB_PREFIX = b'@@HB'
HB_FRAME_LEN = 25                           # outputstr[0..24]
CID_TERMINATOR = bytes([0x14, 0x0A, 0x0D])  # outputstr[20..22]
ACK_BYTE = 0x06

# El ACK real que emite el STM32 se reenvía a la central por USB0.
FORWARD_ACK_TO_CENTRAL = True

# Espejar hacia minicom también las tramas que llegan de la central
# (además de mostrarlas en la consola del script).
MIRROR_USB0_TRAFFIC_TO_MINICOM = True

# --- Logging a archivo ---
LOG_TO_FILE = True
LOG_DIR = 'logs'
LOG_RAW_AMA3_BYTES = True  # volcado hex de TODO lo que llega por AMA3, sin interpretar

print_lock = threading.Lock()
file_log_lock = threading.Lock()
_log_fh = None       # archivo de líneas interpretadas (igual que consola)
_raw_ama3_fh = None  # archivo de volcado crudo hex de AMA3


def open_port(port_name):
    return serial.Serial(
        port=port_name,
        baudrate=BAUDRATE,
        bytesize=BYTESIZE,
        parity=PARITY,
        stopbits=STOPBITS,
        timeout=0.1,
    )


def now_ts():
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]


def now_full_ts():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def open_log_files():
    """Abre (o crea) los archivos de log de la sesión. Devuelve (log_fh, raw_fh),
    cualquiera de los dos puede ser None si su logging está deshabilitado."""
    global _log_fh, _raw_ama3_fh

    if not LOG_TO_FILE:
        return None, None

    os.makedirs(LOG_DIR, exist_ok=True)
    session_id = datetime.now().strftime('%Y%m%d_%H%M%S')

    log_path = os.path.join(LOG_DIR, f'bridge_{session_id}.log')
    _log_fh = open(log_path, 'a', encoding='utf-8', buffering=1)  # line-buffered

    raw_path = None
    if LOG_RAW_AMA3_BYTES:
        raw_path = os.path.join(LOG_DIR, f'ama3_raw_{session_id}.log')
        _raw_ama3_fh = open(raw_path, 'a', encoding='utf-8', buffering=1)

    return log_path, raw_path


def close_log_files():
    global _log_fh, _raw_ama3_fh
    with file_log_lock:
        if _log_fh:
            try:
                _log_fh.close()
            except OSError:
                pass
            _log_fh = None
        if _raw_ama3_fh:
            try:
                _raw_ama3_fh.close()
            except OSError:
                pass
            _raw_ama3_fh = None


def log_raw_ama3(data: bytes):
    """Vuelca en hex, sin interpretar, cada chunk leído de AMA3. Independiente
    de si el parser lo reconoce bien o no; es la referencia cruda para
    depurar problemas de comunicación."""
    if not (LOG_RAW_AMA3_BYTES and _raw_ama3_fh):
        return
    with file_log_lock:
        try:
            _raw_ama3_fh.write(f'{now_full_ts()} [{len(data):3d}B] {hexdump(data)}\n')
        except OSError:
            pass


def hexdump(data: bytes) -> str:
    return ' '.join(f'{b:02X}' for b in data)


def fmt_field_byte(b: int) -> str:
    """Un byte de contenido variable del Heartbeat: si es imprimible se
    muestra como caracter, si no, como hex de dos dígitos (0xXX)."""
    if 32 <= b < 127:
        return chr(b)
    return f'0x{b:02X}'


def format_heartbeat(frame: bytes) -> str:
    """Arma el texto del Heartbeat respetando el offset fijo de ProtR3KCID.md,
    mostrando en hex de dos dígitos los campos que no son imprimibles:
    NUMABO(11), Alarma(14), EstadoDispositivos(17), MemoriaDispositivos(20)
    y checksum(23). El resto (@@HB, corchetes, dígitos de cuenta) es ASCII fijo."""
    account = frame[5:9].decode('ascii', errors='replace')
    numabo = fmt_field_byte(frame[11])
    alarma = fmt_field_byte(frame[14])
    estado = fmt_field_byte(frame[17])
    memoria = fmt_field_byte(frame[20])
    checksum = fmt_field_byte(frame[23])
    return f'@@HB[{account}][{numabo}][{alarma}][{estado}][{memoria}][{checksum}]'


def safe_text(data: bytes) -> str:
    """Decodifica byte a byte tal cual lo haría una terminal simple (latin-1),
    sin reemplazar nada. Se usa para las tramas CID y los volcados 'sin encuadre'."""
    return data.decode('latin-1')


def log(tag: str, msg: str):
    line_console = f"[{now_ts()}] {tag:6s}| {msg}"
    with print_lock:
        print(line_console)
    if LOG_TO_FILE and _log_fh:
        line_file = f"[{now_full_ts()}] {tag:6s}| {msg}\n"
        with file_log_lock:
            try:
                _log_fh.write(line_file)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Puerto virtual (pty) para minicom
# ---------------------------------------------------------------------------
def create_virtual_port(mirror_path: str):
    """Crea un par pty y deja un symlink estable en mirror_path apuntando
    al lado esclavo, para que minicom siempre lo abra con el mismo nombre."""
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)

    # Modo raw desde el arranque: sin eco ni buffer de línea del kernel.
    # Minicom reconfigura el puerto al abrirlo, pero esto evita duplicados
    # o datos "canónicos" mientras todavía no se conectó.
    tty.setraw(slave_fd)

    try:
        if os.path.islink(mirror_path) or os.path.exists(mirror_path):
            os.remove(mirror_path)
    except OSError:
        pass
    os.symlink(slave_name, mirror_path)

    # No cerramos slave_fd: si lo cerráramos y minicom todavía no lo abrió,
    # el master puede quedar en un estado inestable (EIO). Lo mantenemos
    # abierto nosotros (sin usarlo) solo para sostener el par vivo.
    return master_fd, slave_fd, slave_name


# ---------------------------------------------------------------------------
# USB0 (central de alarmas) -> AMA3 real (+ espejo a minicom)
# ---------------------------------------------------------------------------
def usb0_to_ama3(usb: serial.Serial, ama3: serial.Serial, master_fd: int,
                  stop_event: threading.Event):
    buf = bytearray()
    last_rx = time.time()

    while not stop_event.is_set():
        try:
            n = usb.in_waiting
            data = usb.read(n if n else 1)
        except serial.SerialException as e:
            log('ERROR', f'USB0: {e}')
            stop_event.set()
            break

        if data:
            # Reenvío inmediato al AMA3 real, sin esperar el encuadre
            ama3.write(data)

            # Espejo hacia minicom, para verlo tunelado en esa terminal también
            if MIRROR_USB0_TRAFFIC_TO_MINICOM:
                try:
                    os.write(master_fd, data)
                except OSError:
                    pass

            buf.extend(data)
            last_rx = time.time()

            while _extract_one_frame(buf):
                pass
        else:
            if buf and (time.time() - last_rx) > SILENCE_TIMEOUT:
                _flush_unframed(buf)
            time.sleep(0.01)

    if buf:
        _flush_unframed(buf)


def _extract_one_frame(buf: bytearray) -> bool:
    """Intenta cortar una trama completa (Heartbeat o CID) del inicio de buf."""
    if not buf:
        return False

    if len(buf) < len(HB_PREFIX):
        return False

    if buf.startswith(HB_PREFIX):
        if len(buf) < HB_FRAME_LEN:
            return False
        frame = bytes(buf[:HB_FRAME_LEN])
        del buf[:HB_FRAME_LEN]
        log('HB', format_heartbeat(frame))
        return True

    idx = buf.find(CID_TERMINATOR)
    if idx != -1:
        frame = bytes(buf[:idx + len(CID_TERMINATOR)])
        del buf[:idx + len(CID_TERMINATOR)]
        log('CID', safe_text(frame[:-len(CID_TERMINATOR)]))
        return True

    if len(buf) > MAX_UNFRAMED:
        noise = bytes(buf[:1])
        del buf[:1]
        log('RAW', f'(sin encuadre, descartado 1 byte) HEX[{hexdump(noise)}]')
        return True

    return False


def _flush_unframed(buf: bytearray):
    log('RAW', f'(sin encuadre) HEX[{hexdump(bytes(buf))}]  ASCII[{safe_text(bytes(buf))}]')
    buf.clear()


# ---------------------------------------------------------------------------
# AMA3 real -> espejo a minicom + extracción de ACK hacia USB0
# ---------------------------------------------------------------------------
def ama3_to_usb0_and_minicom(ama3: serial.Serial, usb: serial.Serial, master_fd: int,
                              stop_event: threading.Event):
    dbg_buf = bytearray()
    last_rx = time.time()

    while not stop_event.is_set():
        try:
            n = ama3.in_waiting
            data = ama3.read(n if n else 1)
        except serial.SerialException as e:
            log('ERROR', f'AMA3: {e}')
            stop_event.set()
            break

        if data:
            last_rx = time.time()

            # Volcado crudo a archivo, sin interpretar (para diagnóstico de fallas)
            log_raw_ama3(data)

            # Espejo íntegro hacia minicom: ve exactamente lo que manda el STM32
            try:
                os.write(master_fd, data)
            except OSError:
                pass

            for b in data:
                if b == ACK_BYTE:
                    if dbg_buf:
                        log('STM32', safe_text(bytes(dbg_buf)))
                        dbg_buf.clear()
                    if FORWARD_ACK_TO_CENTRAL:
                        usb.write(bytes([ACK_BYTE]))
                        log('ACK', '0x06 (STM32) -> reenviado a central de alarmas (USB0)')
                    else:
                        log('ACK', '0x06 recibido del STM32 (no reenviado)')
                else:
                    dbg_buf.append(b)

            while True:
                idx_cr = dbg_buf.find(b'\r')
                idx_lf = dbg_buf.find(b'\n')
                candidates = [i for i in (idx_cr, idx_lf) if i != -1]
                if not candidates:
                    break
                idx = min(candidates)
                line = bytes(dbg_buf[:idx + 1])
                del dbg_buf[:idx + 1]
                text = line.decode('ascii', errors='replace').rstrip('\r\n')
                if text:
                    log('STM32', text)
        else:
            if dbg_buf and (time.time() - last_rx) > SILENCE_TIMEOUT:
                log('STM32', dbg_buf.decode('ascii', errors='replace'))
                dbg_buf.clear()
            time.sleep(0.01)

    if dbg_buf:
        log('STM32', dbg_buf.decode('ascii', errors='replace'))


# ---------------------------------------------------------------------------
# minicom (puerto virtual) -> AMA3 real
# ---------------------------------------------------------------------------
def pty_master_to_ama3(master_fd: int, ama3: serial.Serial, stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            r, _, _ = select.select([master_fd], [], [], 0.1)
        except (OSError, ValueError):
            break
        if master_fd in r:
            try:
                data = os.read(master_fd, 1024)
            except OSError:
                time.sleep(0.1)
                continue
            if data:
                ama3.write(data)
                log('TX', f'minicom->AMA3: {data!r}')


def main():
    try:
        usb = open_port(PORT_USB)
        ama3 = open_port(PORT_AMA3)
    except serial.SerialException as e:
        print(f"Error abriendo puertos serie: {e}")
        print("Verifique permisos (grupo dialout) y que los dispositivos existan.")
        sys.exit(1)

    master_fd, slave_fd, slave_name = create_virtual_port(MIRROR_PATH)
    log_path, raw_path = open_log_files()

    print(f"USB0 (central):        {PORT_USB}")
    print(f"AMA3 (STM32, real):    {PORT_AMA3}  ({BAUDRATE} baud, 8 datos, paridad PAR, 2 stop)")
    print(f"Puerto virtual minicom: {MIRROR_PATH}  -> {slave_name}")
    if log_path:
        print(f"Log de eventos:         {log_path}")
    if raw_path:
        print(f"Log crudo AMA3 (hex):   {raw_path}")
    print()
    print("En OTRA terminal, ejecutar:")
    print(f"    minicom -b {BAUDRATE} -o -D {MIRROR_PATH}")
    print()
    print("No abrir minicom sobre /dev/ttyAMA3 directamente: este script")
    print("necesita tenerlo abierto en exclusiva.")
    print()
    print("Ctrl+C en esta terminal para salir.\n")

    stop_event = threading.Event()

    t1 = threading.Thread(target=usb0_to_ama3, args=(usb, ama3, master_fd, stop_event), daemon=True)
    t2 = threading.Thread(target=ama3_to_usb0_and_minicom, args=(ama3, usb, master_fd, stop_event), daemon=True)
    t3 = threading.Thread(target=pty_master_to_ama3, args=(master_fd, ama3, stop_event), daemon=True)
    t1.start()
    t2.start()
    t3.start()

    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nCerrando...")
        stop_event.set()
        time.sleep(0.2)
        usb.close()
        ama3.close()
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.close(slave_fd)
        except OSError:
            pass
        try:
            if os.path.islink(MIRROR_PATH):
                os.remove(MIRROR_PATH)
        except OSError:
            pass
        close_log_files()


if __name__ == '__main__':
    main()

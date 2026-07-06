#!/bin/bash
# start_bridge.sh
#
# Levanta serial_bridge.py y minicom en una sola terminal, dividida en dos
# paneles con tmux: arriba el log del bridge, abajo minicom conectado al
# puerto virtual que crea el bridge.
#
# Requisito: tmux (si no está instalado: sudo apt install tmux)
#
# Uso:
#   ./start_bridge.sh
#
# Para salir: Ctrl+B luego & (mata la sesión completa, bridge y minicom),
# o simplemente cerrar minicom (Ctrl+A X) y después Ctrl+C en el otro panel.

set -e

SESSION="alarma_bridge"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_SCRIPT="$SCRIPT_DIR/serial_bridge.py"
MIRROR_PATH="/tmp/ttyAMA3_mirror"
BAUDRATE=9600

if ! command -v tmux >/dev/null 2>&1; then
    echo "Falta tmux. Instalar con: sudo apt install tmux"
    exit 1
fi

if [ ! -f "$BRIDGE_SCRIPT" ]; then
    echo "No encuentro $BRIDGE_SCRIPT"
    echo "Ajustá la variable BRIDGE_SCRIPT en este .sh si lo moviste de lugar."
    exit 1
fi

# Si quedó una sesión previa colgada (por un cierre abrupto), la mato antes de arrancar
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Panel superior: el bridge
tmux new-session -d -s "$SESSION" -n bridge "python3 '$BRIDGE_SCRIPT'"

# Panel inferior: espera a que el bridge cree el puerto virtual y recién ahí abre minicom
tmux split-window -v -t "$SESSION" "
echo 'Esperando a que el bridge cree el puerto virtual...'
while [ ! -e '$MIRROR_PATH' ]; do
    sleep 0.2
done
minicom -b $BAUDRATE -o -D '$MIRROR_PATH'
"

tmux select-layout -t "$SESSION" even-vertical
tmux attach -t "$SESSION"

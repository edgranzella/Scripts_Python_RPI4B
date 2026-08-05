
#!/bin/bash

# Nombre de la interfaz de red asignada al A7670SA
INTERFACE="usb0"
# IP fija que le daremos a la Raspberry Pi para hablar con el módem
IP_RPI="192.168.0.100/24"
# IP interna de la puerta de enlace del módem SIMCom
GATEWAY_MODEM="192.168.0.1"

echo "=== Iniciando configuración fija para SIMCom A7670SA ==="

# 1. Asegurar que la interfaz esté limpia y encendida
sudo ip addr flush dev $INTERFACE 2>/dev/null
sudo ip link set $INTERFACE up
sleep 1

# 2. Asignar la IP estática a la Raspberry Pi
sudo ip addr add $IP_RPI dev $INTERFACE
echo "[OK] IP $IP_RPI asignada a $INTERFACE"

# 3. Configurar la métrica de ruteo para NO perder el internet de eth0
# Agregamos una ruta específica para el módem con una métrica alta (ej. 200)
# Esto permite que eth0 (métrica baja, ej. 100) siga siendo el internet principal
sudo ip route add default via $GATEWAY_MODEM dev $INTERFACE metric 200 2>/dev/null

# 4. Verificar conectividad local con el chip SIMCom
echo "Verificando comunicación con el módem..."
if ping -c 3 $GATEWAY_MODEM > /dev/null 2>&1; then
    echo "[ÉXITO] Comunicación establecida con el A7670SA ($GATEWAY_MODEM)"
else
    echo "[ERROR] No hay respuesta del módem. Revisa el hardware o los comandos AT."
fi

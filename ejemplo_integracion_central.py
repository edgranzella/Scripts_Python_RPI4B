#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ejemplo_integracion_central.py

Muestra cómo el programa de la central de alarmas (que corre en la Raspberry
Pi 4B y puede operar como ABONADO o como CENTRAL POLICIAL) usa el driver
rs485_host para supervisar la placa controladora de fuentes y batería EN
PARALELO con su lógica principal.

Arquitectura:
    +--------------------------------------------------------------+
    | Proceso de la central (Raspberry Pi 4B)                      |
    |                                                              |
    |  [hilo principal]  lógica de alarmas (Abonado/Policial)      |
    |         ▲                                                    |
    |         │ callbacks / cola de eventos                        |
    |         ▼                                                    |
    |  [hilo RS-485]  RS485Controller  ── polling cada ~2 s ──►    |
    +---------------------------────────────────────────────┬─────+
                                                            │ RS-485
                                                    (placa fuentes/batería)

El driver ya trae su propio hilo lector; aquí agregamos un hilo de polling
para no bloquear la lógica de la central.
"""

import time
import signal
import threading
import datetime as _dt
from enum import Enum

from rs485_host import RS485Controller, Event, Status


# --------------------------------------------------------------------------- #
#  Rol del equipo
# --------------------------------------------------------------------------- #
class Rol(Enum):
    ABONADO = "abonado"
    CENTRAL_POLICIAL = "central_policial"


# --------------------------------------------------------------------------- #
#  Supervisor de energía: envuelve al driver y expone el estado a la central
# --------------------------------------------------------------------------- #
class SupervisorEnergia:
    def __init__(self, port: str, rol: Rol, poll_period: float = 2.0):
        self.rol = rol
        self.poll_period = poll_period
        self._stop = threading.Event()
        self._poll_thread = None

        # Estado consolidado que la lógica de alarmas puede leer en cualquier momento.
        self.lock = threading.Lock()
        self.sobre_bateria = False       # True cuando se cae 220 V
        self.falla_fuente = False
        self.ultimo_estado: Status | None = None

        self.ctrl = RS485Controller(
            port,
            on_event=self._on_event,     # se ejecuta en el hilo lector del driver
            logger=self._log,
        )

    # ---- logging simple (reemplazar por el logger real de la central) ---- #
    def _log(self, msg: str):
        print(f"[{_dt.datetime.now():%H:%M:%S}] {msg}")

    # ---- ciclo de vida ---- #
    def iniciar(self):
        self.ctrl.start()
        # Sincroniza el reloj de la placa con la hora de la Raspberry.
        try:
            self.ctrl.set_datetime()
            self._log("Reloj de la placa sincronizado (TD).")
        except Exception as e:
            self._log(f"No se pudo sincronizar la hora: {e}")

        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="energia-poll", daemon=True
        )
        self._poll_thread.start()

    def detener(self):
        self._stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=3)
        self.ctrl.stop()

    # ---- polling periódico (Doc 1 §12.5) ---- #
    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                st = self.ctrl.poll_status()
                with self.lock:
                    self.ultimo_estado = st
                    self.falla_fuente = st.f1_fail or st.f2_fail
                    # AC=0 -> equipo funcionando a batería.
                    self.sobre_bateria = (st.ac == 0)
            except Exception as e:
                self._log(f"Fallo de polling RS-485: {e}")
            self._stop.wait(self.poll_period)

    # ---- manejo de eventos confiables ---- #
    def _on_event(self, ev: Event):
        """
        El driver ya ACKeó el evento; aquí sólo se decide la reacción.
        La reacción depende del ROL del equipo.
        """
        if ev.kind == "AC" and ev.detail == "OFF":
            self._log(f"⚡ Caída de 220 V a las {ev.ts}")
            self._reaccion_corte_energia(ev)
        elif ev.kind == "AC" and ev.detail == "ON":
            self._log(f"✅ Retorno de 220 V a las {ev.ts}")
            self._reaccion_retorno_energia(ev)
        elif ev.kind in ("F1", "F2") and ev.detail == "FAIL":
            self._log(f"🔴 Falla de fuente {ev.kind} a las {ev.ts}")
            self._reaccion_falla_fuente(ev)
        elif ev.kind == "BT" and ev.detail == "END":
            self._log(f"🔋 Fin de test de batería: "
                      f"{ev.dur_mm:02d}:{ev.dur_ss:02d} (modo {ev.mode}) a las {ev.ts}")
            self._reaccion_fin_test(ev)

    # ---- reacciones específicas por rol ---- #
    def _reaccion_corte_energia(self, ev: Event):
        if self.rol == Rol.CENTRAL_POLICIAL:
            # La central policial debe registrar y priorizar continuidad:
            # p.ej. avisar a operadores, encender indicadores, escalar.
            self._notificar_operadores("CORTE DE ENERGÍA en central policial", ev)
        else:  # ABONADO
            # El abonado reporta el evento a la central de monitoreo por su canal.
            self._reportar_a_central_de_monitoreo("AC_OFF", ev)

    def _reaccion_retorno_energia(self, ev: Event):
        if self.rol == Rol.CENTRAL_POLICIAL:
            self._notificar_operadores("Energía restablecida", ev)
        else:
            self._reportar_a_central_de_monitoreo("AC_ON", ev)

    def _reaccion_falla_fuente(self, ev: Event):
        # Falla de hardware: siempre es crítico en ambos roles.
        msg = f"FALLA DE FUENTE {ev.kind}"
        if self.rol == Rol.CENTRAL_POLICIAL:
            self._notificar_operadores(msg, ev)
        else:
            self._reportar_a_central_de_monitoreo(f"{ev.kind}_FAIL", ev)

    def _reaccion_fin_test(self, ev: Event):
        # Se podría persistir la duración para tendencias de salud de batería.
        self._registrar_log_bateria(ev)

    # ---- puntos de integración con la lógica real de la central ---- #
    def _notificar_operadores(self, texto: str, ev: Event):
        # TODO: integrar con la UI/consola de la central policial.
        self._log(f"[POLICIAL] {texto} ({ev.raw})")

    def _reportar_a_central_de_monitoreo(self, codigo: str, ev: Event):
        # TODO: integrar con el canal de comunicación del abonado (IP/GPRS/etc.).
        self._log(f"[ABONADO] Reporte '{codigo}' hacia la central ({ev.ts})")

    def _registrar_log_bateria(self, ev: Event):
        # TODO: escribir en el log/histórico de la central.
        self._log(f"[LOG] Test batería {ev.dur_mm:02d}:{ev.dur_ss:02d} "
                  f"modo={ev.mode} ts={ev.ts}")

    # ---- ayudas que la central puede llamar bajo demanda ---- #
    def hay_energia(self) -> bool:
        with self.lock:
            return not self.sobre_bateria

    def lanzar_test_bateria(self):
        self.ctrl.start_battery_test()
        self._log("Test de batería solicitado por la central.")


# --------------------------------------------------------------------------- #
#  Programa principal de ejemplo
# --------------------------------------------------------------------------- #
def main():
    import sys
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    rol = Rol.CENTRAL_POLICIAL if "policial" in sys.argv else Rol.ABONADO

    sup = SupervisorEnergia(port, rol=rol)
    sup.iniciar()
    print(f"Supervisor de energía iniciado. Rol: {rol.value}. Ctrl-C para salir.")

    # Salida limpia con Ctrl-C.
    detener = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: detener.set())

    # === Aquí correría el bucle principal de la central de alarmas ===
    # A modo de ejemplo, sólo imprime el estado consolidado cada 5 s y hace
    # un test de batería diario a las 03:00.
    ultimo_test_dia = None
    try:
        while not detener.is_set():
            ahora = _dt.datetime.now()
            if ahora.hour == 3 and ahora.minute == 0 and ultimo_test_dia != ahora.date():
                sup.lanzar_test_bateria()
                ultimo_test_dia = ahora.date()

            with sup.lock:
                st = sup.ultimo_estado
            if st:
                modo = "BATERÍA" if st.ac == 0 else "RED 220V"
                print(f"[{ahora:%H:%M:%S}] {modo} | VB={st.vb_ad} IB={st.ib_ad} "
                      f"IC={st.ic_ad} PWM={st.pwm} FL={st.fl}")
            detener.wait(5)
    finally:
        sup.detener()
        print("Supervisor detenido.")


if __name__ == "__main__":
    main()

"""Simulation engine - the runtime that ties the modules together.

Responsibilities:
* Run the closed control loop between M1 (PlantSimulator) and M2 (PLCController)
  once per update period. The README states the closed loop is ONLY between
  M1 and M2 - the engine honours that.
* Publish the combined tag snapshot through the M3 message bus.
* Persist the snapshot in the M3 historian.
* Apply operator commands (start/stop/fault inject/reset) coming from M4.
* Detect a stale data link (the MQTT_STALE infrastructure fault).

It can run its loop in a background thread (used by the Streamlit dashboard and
the FastAPI backend) or be stepped manually (used by tests/CLI).
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional

import config
from historian import Historian
from messaging import MessageBus, create_bus
from plc import PLCController
from simulator import PlantSimulator


class SimulationEngine:
    def __init__(self, use_mqtt: bool = False, historian: Optional[Historian] = None,
                 bus: Optional[MessageBus] = None) -> None:
        self.plant = PlantSimulator()
        self.plc = PLCController()
        self.bus = bus or create_bus(use_mqtt=use_mqtt)
        self.historian = historian or Historian()

        # Operator command state (driven by the dashboard, M4).
        self._operator_start = 0
        self._operator_stop = 0
        self._fault_inject_code = config.FAULT_NONE
        self._reset_fault = 0
        # Simulated MQTT stale: when set, the engine stops refreshing the bus.
        self._simulate_stale = False

        self._latest: Dict = {}
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._tick = 0

    # ------------------------------------------------------------------
    # Operator command API (called from M4 dashboard / M3 command topic)
    # ------------------------------------------------------------------
    def start_line(self) -> None:
        with self._lock:
            self._operator_start = 1
            self._operator_stop = 0

    def stop_line(self) -> None:
        with self._lock:
            self._operator_stop = 1
            self._operator_start = 0

    def inject_fault(self, code: int) -> None:
        with self._lock:
            self._fault_inject_code = int(code)
            self._reset_fault = 0
            self._simulate_stale = (int(code) == config.FAULT_MQTT_STALE)

    def reset_fault(self) -> None:
        with self._lock:
            self._fault_inject_code = config.FAULT_NONE
            self._reset_fault = 1
            self._simulate_stale = False
        self.plc.acknowledge()

    # ------------------------------------------------------------------
    # One control-loop iteration
    # ------------------------------------------------------------------
    def step(self) -> Dict:
        with self._lock:
            operator_start = self._operator_start
            operator_stop = self._operator_stop
            fault_code = self._fault_inject_code
            reset_fault = self._reset_fault
            simulate_stale = self._simulate_stale
            # start/stop/reset are edge-triggered: consume them after one tick.
            self._operator_start = 0
            self._operator_stop = 0
            self._reset_fault = 0

        # Data-stale detection: how long since the bus last saw a tag update.
        data_stale_flag = 0
        if simulate_stale:
            data_stale_flag = 1
        elif self.bus.seconds_since_last(config.MQTT_TOPIC_TAGS) > config.DATA_STALE_TIMEOUT_S:
            # only meaningful once we have published at least once
            if self._tick > 0:
                data_stale_flag = 1

        # --- M2 reads the previous sensor snapshot + operator buttons ---
        sensors_for_plc = dict(self.plant.outputs())
        sensors_for_plc["operator_start"] = operator_start
        sensors_for_plc["operator_stop"] = operator_stop
        sensors_for_plc["data_stale_flag"] = data_stale_flag
        control = self.plc.step(sensors_for_plc)

        # --- M1 applies the actuator commands + fault injection ---
        plant_cmd = dict(control)
        plant_cmd["fault_inject_code"] = fault_code
        plant_cmd["reset_fault"] = reset_fault
        sensors = self.plant.step(plant_cmd)

        # --- Build the combined tag snapshot (plant + control + alarm) ---
        snapshot: Dict = {}
        snapshot.update(sensors)
        snapshot.update(control)
        snapshot["data_stale_flag"] = data_stale_flag
        snapshot["ts"] = time.time()
        snapshot["tick"] = self._tick

        # --- M3: publish + store (skip publish when simulating a dead link) ---
        if not simulate_stale:
            self.bus.publish(config.MQTT_TOPIC_TAGS, snapshot)
        self.historian.record(snapshot)

        with self._lock:
            self._latest = snapshot
            self._tick += 1
        return snapshot

    # ------------------------------------------------------------------
    # Background loop control
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            start = time.time()
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                print(f"[engine] step error: {exc}")
            elapsed = time.time() - start
            time.sleep(max(0.0, config.TICK_INTERVAL_S - elapsed))

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    def latest(self) -> Dict:
        with self._lock:
            return dict(self._latest)

    @property
    def is_running(self) -> bool:
        return self._running

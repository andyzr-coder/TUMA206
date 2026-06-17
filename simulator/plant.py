"""M1 - Plant Simulator.

Simulates the physical beverage pasteurization & bottling line. It receives
actuator commands (from the PLC, M2) plus a fault-injection code (from the
dashboard, M4) and produces sensor + feedback values every update period.

This module contains NO control logic - it only models physics and faults.
The control decisions live in M2 (plc/controller.py).

Port specification (see README section 4):
    inputs : pump_cmd, inlet_valve_cmd, heater_power_cmd, cooling_valve_cmd,
             conveyor_cmd, fill_valve_cmd, capper_cmd, fault_inject_code,
             reset_fault
    outputs: tank_level, pasteur_temp, cooler_temp, flow_rate, bottle_present,
             bottle_count, pump_feedback, valve_feedback, stage_state,
             fault_status
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict

import config


@dataclass
class PlantSimulator:
    """Physical plant model. Call :meth:`step` once per update period."""

    # --- internal physical state (sensor values) ---
    tank_level: float = 50.0       # %
    pasteur_temp: float = config.AMBIENT_TEMP  # degC
    cooler_temp: float = config.AMBIENT_TEMP   # degC
    flow_rate: float = 0.0         # L/min
    bottle_present: int = 0        # 0/1
    bottle_count: int = 0          # bottles capped so far
    pump_feedback: int = 0         # 0/1 - real pump running confirmation
    valve_feedback: int = 0        # 0/1 - inlet valve open confirmation

    # --- fault handling ---
    fault_status: int = config.FAULT_NONE
    _frozen_temp: float = field(default=0.0, repr=False)

    # --- helpers for discrete events ---
    _fill_timer: int = field(default=0, repr=False)
    _bottle_phase: int = field(default=0, repr=False)
    _bottle_filled: int = field(default=0, repr=False)

    def reset(self) -> None:
        """Reset the plant to a clean starting state."""
        self.tank_level = 50.0
        self.pasteur_temp = config.AMBIENT_TEMP
        self.cooler_temp = config.AMBIENT_TEMP
        self.flow_rate = 0.0
        self.bottle_present = 0
        self.bottle_count = 0
        self.pump_feedback = 0
        self.valve_feedback = 0
        self.fault_status = config.FAULT_NONE
        self._fill_timer = 0
        self._bottle_phase = 0
        self._bottle_filled = 0

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------
    def step(self, cmd: Dict) -> Dict:
        """Advance the simulation one tick.

        Args:
            cmd: dictionary of actuator commands and fault controls.

        Returns:
            dictionary of sensor + feedback outputs (the M1 output pins).
        """
        # 0. Fault injection / reset --------------------------------------
        if cmd.get("reset_fault"):
            self.fault_status = config.FAULT_NONE
        else:
            code = int(cmd.get("fault_inject_code", config.FAULT_NONE))
            if code != self.fault_status:
                # Latch the new fault and remember the current temp for "stuck".
                self.fault_status = code
                self._frozen_temp = self.pasteur_temp

        pump_cmd = int(cmd.get("pump_cmd", 0))
        inlet_valve_cmd = int(cmd.get("inlet_valve_cmd", 0))
        heater_power_cmd = float(cmd.get("heater_power_cmd", 0.0))
        cooling_valve_cmd = int(cmd.get("cooling_valve_cmd", 0))
        conveyor_cmd = int(cmd.get("conveyor_cmd", 0))
        fill_valve_cmd = int(cmd.get("fill_valve_cmd", 0))
        capper_cmd = int(cmd.get("capper_cmd", 0))

        pump_failed = self.fault_status == config.FAULT_PUMP_FAIL

        # 1. Feed pump & flow --------------------------------------------
        if pump_cmd and not pump_failed and self.tank_level > 0.5:
            self.pump_feedback = 1
            self.flow_rate = 40.0 + random.uniform(-1.5, 1.5)
        else:
            self.pump_feedback = 0
            self.flow_rate = 0.0

        # 2. Raw / balance tank (S1) -------------------------------------
        self.valve_feedback = inlet_valve_cmd
        inflow = 6.0 if inlet_valve_cmd else 0.0
        outflow = 4.0 if self.pump_feedback else 0.0
        self.tank_level = _clamp(self.tank_level + inflow - outflow, 0.0, 100.0)

        # 3. Pasteurizer temperature (S2) --------------------------------
        self._update_pasteur_temp(heater_power_cmd)

        # 4. Cooler (S3) -------------------------------------------------
        # Hot product enters the cooler; cooling valve pulls it toward set-point.
        cool_target = config.COOLER_SETPOINT if cooling_valve_cmd else self.pasteur_temp
        self.cooler_temp += 0.25 * (cool_target - self.cooler_temp)
        self.cooler_temp += random.uniform(-0.05, 0.05)

        # 5. Filler & 6. Capper / Conveyor (S4 / S5) ---------------------
        self._update_bottling(conveyor_cmd, fill_valve_cmd, capper_cmd)

        return self.outputs()

    # ------------------------------------------------------------------
    def _update_pasteur_temp(self, heater_power_cmd: float) -> None:
        """First-order thermal model with fault behaviour."""
        if self.fault_status == config.FAULT_TEMP_STUCK:
            # Sensor frozen: reported temperature never moves.
            self.pasteur_temp = self._frozen_temp
            return

        if self.fault_status == config.FAULT_TEMP_EXCURSION:
            # Heater "runs away": temperature drifts above the safe band
            # regardless of the command, simulating a stuck heating element.
            target = config.PASTEUR_SAFE_MAX + 8.0
        else:
            # Normal: heater_power_cmd (0-100 %) drives the achievable temp.
            target = config.AMBIENT_TEMP + (heater_power_cmd / 100.0) * 60.0

        # Move toward the target with a simple time constant.
        self.pasteur_temp += 0.20 * (target - self.pasteur_temp)
        self.pasteur_temp += random.uniform(-0.08, 0.08)

    def _update_bottling(self, conveyor_cmd: int, fill_valve_cmd: int,
                         capper_cmd: int) -> None:
        """Discrete-event model for bottle presence, filling and capping."""
        if not conveyor_cmd:
            # Line stopped: hold the current bottle in place.
            return

        # The conveyor advances a bottle through a small phase cycle:
        #   phase 0 -> empty slot (no bottle)
        #   phase 1 -> bottle arrives and is detected at the filler
        #   phase 2 -> bottle being filled / waiting to be capped
        self._bottle_phase = (self._bottle_phase + 1) % 4

        if self._bottle_phase in (1, 2):
            self.bottle_present = 1
        else:
            self.bottle_present = 0

        # Filling
        if self.bottle_present and fill_valve_cmd:
            self._fill_timer += 1
            if self._fill_timer >= config.FILL_DURATION_TICKS:
                self._bottle_filled = 1
        else:
            self._fill_timer = 0

        # Capping & counting: a filled bottle that gets capped is counted once.
        if self.bottle_present and self._bottle_filled and capper_cmd:
            self.bottle_count += 1
            self._bottle_filled = 0

    # ------------------------------------------------------------------
    def stage_state(self) -> str:
        """A coarse description of where product currently is."""
        if self.flow_rate > 0 and self.pasteur_temp >= config.PASTEUR_SAFE_MIN:
            return "PROCESSING"
        if self.flow_rate > 0:
            return "HEATING"
        if self.tank_level > config.TANK_LEVEL_MIN_PUMP:
            return "READY"
        return "EMPTY"

    def outputs(self) -> Dict:
        """Return the current M1 output pins as a dictionary."""
        return {
            "tank_level": round(self.tank_level, 2),
            "pasteur_temp": round(self.pasteur_temp, 2),
            "cooler_temp": round(self.cooler_temp, 2),
            "flow_rate": round(self.flow_rate, 2),
            "bottle_present": int(self.bottle_present),
            "bottle_count": int(self.bottle_count),
            "pump_feedback": int(self.pump_feedback),
            "valve_feedback": int(self.valve_feedback),
            "stage_state": self.stage_state(),
            "fault_status": int(self.fault_status),
        }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

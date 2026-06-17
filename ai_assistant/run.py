"""Headless runner / smoke test for the digital twin.

This does NOT need Streamlit. It runs the M1+M2+M3+M5 pipeline in the terminal
so you can verify the whole system works and watch a fault scenario play out.

Usage:
    python run.py                 # run a scripted demo (normal -> faults)
    python run.py --ticks 30      # run N ticks of normal operation
"""

from __future__ import annotations

import argparse
import time

import config
from ai_assistant import AIAssistant
from engine import SimulationEngine


def _print_row(snap: dict) -> None:
    print(
        f"t={snap.get('tick'):>3} "
        f"state={snap.get('plc_state'):<8} "
        f"level={snap.get('tank_level'):>5.1f}% "
        f"temp={snap.get('pasteur_temp'):>5.1f}C "
        f"flow={snap.get('flow_rate'):>5.1f} "
        f"bottles={snap.get('bottle_count'):>3} "
        f"alarm={config.ALARM_LABELS.get(int(snap.get('alarm_code', 0)))}"
    )


def scripted_demo() -> None:
    engine = SimulationEngine(use_mqtt=False)
    assistant = AIAssistant()
    print(f"AI engine: {'Claude' if assistant.using_claude else 'rule-based'}\n")

    def run(n: int) -> None:
        for _ in range(n):
            _print_row(engine.step())

    print("== Start line, normal operation ==")
    engine.start_line()
    run(8)

    print("\n== Inject PUMP_FAIL ==")
    engine.inject_fault(config.FAULT_PUMP_FAIL)
    run(6)
    snap = engine.latest()
    advice = assistant.diagnose(snap, int(snap["alarm_code"]),
                                engine.historian.recent())
    print(f"\nAI: [{advice['diagnosis_label']}] {advice['recommendation_text']}\n")

    print("== Reset fault, resume ==")
    engine.reset_fault()
    engine.start_line()
    run(5)

    print("\n== Inject TEMP_EXCURSION ==")
    engine.inject_fault(config.FAULT_TEMP_EXCURSION)
    run(8)
    snap = engine.latest()
    advice = assistant.diagnose(snap, int(snap["alarm_code"]),
                                engine.historian.recent())
    print(f"\nAI: [{advice['diagnosis_label']}] {advice['recommendation_text']}")

    engine.historian.export_csv()
    print(f"\nHistory exported to {config.CSV_EXPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Beverage line digital twin runner")
    parser.add_argument("--ticks", type=int, default=0,
                        help="run N ticks of normal operation instead of the demo")
    args = parser.parse_args()

    if args.ticks > 0:
        engine = SimulationEngine(use_mqtt=False)
        engine.start_line()
        for _ in range(args.ticks):
            _print_row(engine.step())
    else:
        scripted_demo()


if __name__ == "__main__":
    main()

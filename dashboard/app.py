"""M4 - Dashboard (Streamlit + Plotly).

The operator-facing screen. It:
* starts the simulation engine (M1+M2+M3) in a background thread,
* shows live KPIs, trend charts and the alarm banner,
* provides Start / Stop and fault-injection buttons (the M4 output pins),
* shows the AI assistant's recommendation (M5).

Only the right-hand "live view" auto-refreshes (via st.fragment), so the
left-hand operator controls do not flicker or reset on every update.

Run from the project root with:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
import sys

# Make the project root importable when Streamlit runs this file directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

import config  # noqa: E402
from ai_assistant import AIAssistant  # noqa: E402
from engine import SimulationEngine  # noqa: E402

st.set_page_config(page_title="Beverage Line Digital Twin", layout="wide",
                   page_icon="🥤")


# ---------------------------------------------------------------------------
# Engine + AI assistant are created once and shared across reruns.
# ---------------------------------------------------------------------------
@st.cache_resource
def get_engine() -> SimulationEngine:
    use_mqtt = os.environ.get("USE_MQTT", "0") == "1"
    engine = SimulationEngine(use_mqtt=use_mqtt)
    engine.start()
    return engine


@st.cache_resource
def get_assistant() -> AIAssistant:
    return AIAssistant()


engine = get_engine()
assistant = get_assistant()

# ---------------------------------------------------------------------------
# Small HTML helpers for colored status cards
# ---------------------------------------------------------------------------
_CARD_COLORS = {
    "ok": ("#e8f5e9", "#2e7d32"),
    "warn": ("#fff8e1", "#f57f17"),
    "bad": ("#ffebee", "#c62828"),
    "idle": ("#eceff1", "#607d8b"),
    "info": ("#e3f2fd", "#1565c0"),
}


def status_card(label: str, value: str, status: str = "idle", sub: str = "") -> str:
    bg, fg = _CARD_COLORS.get(status, _CARD_COLORS["idle"])
    return (
        f'<div style="background:{bg};border-left:6px solid {fg};border-radius:8px;'
        f'padding:8px 12px;margin:2px 0;min-height:78px;">'
        f'<div style="font-size:0.70rem;color:{fg};font-weight:700;'
        f'text-transform:uppercase;letter-spacing:.03em;">{label}</div>'
        f'<div style="font-size:1.45rem;color:#1a1a1a;font-weight:700;'
        f'line-height:1.25;">{value}</div>'
        f'<div style="font-size:0.68rem;color:#6b6b6b;">{sub}</div></div>'
    )


def arrow() -> str:
    return ('<div style="text-align:center;font-size:1.6rem;color:#90a4ae;'
            'padding-top:24px;">&#10142;</div>')


# ---------------------------------------------------------------------------
# Header (static)
# ---------------------------------------------------------------------------
st.title("🥤 Smart Beverage Pasteurization & Bottling Line")
st.caption("Digital Twin demo - M1 Plant · M2 PLC · M3 Data Layer · "
           "M4 Dashboard · M5 AI Assistant")

# ---------------------------------------------------------------------------
# Sidebar: operator controls (M4 output pins) - this block does NOT auto-refresh
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Operator Controls")
    col_a, col_b = st.columns(2)
    if col_a.button("▶ Start line", use_container_width=True):
        engine.start_line()
    if col_b.button("■ Stop line", use_container_width=True):
        engine.stop_line()

    st.divider()
    st.subheader("Fault injection")
    fault_choice = st.selectbox(
        "Fault to inject",
        options=list(config.FAULT_LABELS.keys()),
        format_func=lambda code: f"{code} - {config.FAULT_LABELS[code]}",
    )
    col_c, col_d = st.columns(2)
    if col_c.button("Inject", use_container_width=True):
        engine.inject_fault(fault_choice)
    if col_d.button("Reset fault", use_container_width=True):
        engine.reset_fault()

    st.divider()
    st.subheader("Display settings")
    refresh_s = st.slider("Refresh interval (s)", 1, 10, 3)
    window = st.slider("Trend window (s)", 30, 600, config.HISTORY_WINDOW_S, 30)
    if st.button("Export CSV", use_container_width=True):
        path = engine.historian.export_csv()
        st.success(f"Exported to {path}")

    st.divider()
    ai_mode = "Claude (Anthropic)" if assistant.using_claude else "Rule-based (no API key)"
    st.caption(f"AI engine: **{ai_mode}**")
    st.caption(f"Data bus: **{type(engine.bus).__name__}**")


# ---------------------------------------------------------------------------
# Render helpers for the live view
# ---------------------------------------------------------------------------
# Which production stage each alarm code belongs to (for highlighting).
_ALARM_STAGE = {
    config.ALARM_PUMP_NO_FLOW: "S1",
    config.ALARM_SENSOR_TEMP_STUCK: "S2",
    config.ALARM_TEMP_OUT_OF_RANGE: "S2",
}


def _render_flow_diagram(latest: dict, alarm_code: int) -> None:
    """Five stage cards S1..S5 with arrows; active stages green, faulted red."""
    alarm_stage = _ALARM_STAGE.get(alarm_code)
    stages = [
        ("S1", "Raw Tank", latest.get("pump_cmd") or latest.get("inlet_valve_cmd"),
         f"level {latest.get('tank_level', 0):.0f}%"),
        ("S2", "Pasteurizer", float(latest.get("heater_power_cmd", 0)) > 0,
         f"{latest.get('pasteur_temp', 0):.1f} °C"),
        ("S3", "Cooler", latest.get("cooling_valve_cmd"),
         f"{latest.get('cooler_temp', 0):.1f} °C"),
        ("S4", "Filler", latest.get("fill_valve_cmd") or latest.get("bottle_present"),
         "bottle present" if latest.get("bottle_present") else "—"),
        ("S5", "Capper", latest.get("conveyor_cmd"),
         f"{int(latest.get('bottle_count', 0))} capped"),
    ]
    cols = st.columns([4, 1, 4, 1, 4, 1, 4, 1, 4])
    for i, (sid, name, active, sub) in enumerate(stages):
        if sid == alarm_stage:
            status = "bad"
        elif active:
            status = "ok"
        else:
            status = "idle"
        cols[i * 2].markdown(
            status_card(f"{sid} {name}", name, status, sub),
            unsafe_allow_html=True,
        )
        if i < len(stages) - 1:
            cols[i * 2 + 1].markdown(arrow(), unsafe_allow_html=True)


def _render_kpis(latest: dict) -> None:
    temp = float(latest.get("pasteur_temp", 0))
    if temp > config.PASTEUR_SAFE_MAX:
        temp_status = "bad"
    elif temp < config.PASTEUR_SAFE_MIN:
        temp_status = "warn"
    else:
        temp_status = "ok"

    level = float(latest.get("tank_level", 0))
    level_status = "warn" if (level < config.TANK_LEVEL_LOW
                              or level > config.TANK_LEVEL_HIGH) else "ok"
    flow = float(latest.get("flow_rate", 0))
    flow_status = "ok" if flow > 0.1 else "idle"

    cards = [
        ("Tank level", f"{level:.1f} %", level_status, ""),
        ("Pasteur temp", f"{temp:.1f} °C", temp_status,
         f"safe {config.PASTEUR_SAFE_MIN:.0f}-{config.PASTEUR_SAFE_MAX:.0f}"),
        ("Cooler temp", f"{latest.get('cooler_temp', 0):.1f} °C", "info", ""),
        ("Flow rate", f"{flow:.1f} L/min", flow_status, ""),
        ("Bottles capped", f"{int(latest.get('bottle_count', 0))}", "info", ""),
        ("Heater power", f"{latest.get('heater_power_cmd', 0):.0f} %", "info", ""),
    ]
    cols = st.columns(len(cards))
    for col, (label, value, status, sub) in zip(cols, cards):
        col.markdown(status_card(label, value, status, sub), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Live view (right-hand side) - only THIS part reruns on the refresh interval.
# ---------------------------------------------------------------------------
@st.fragment(run_every=f"{refresh_s}s")
def live_view() -> None:
    latest = engine.latest()
    alarm_code = int(latest.get("alarm_code", config.ALARM_NONE))
    plc_state = latest.get("plc_state", config.PLC_IDLE)

    # Alarm banner
    if alarm_code != config.ALARM_NONE:
        st.error(
            f"🚨 ALARM [{config.ALARM_LABELS.get(alarm_code)}] - "
            f"{config.ALARM_DESCRIPTIONS.get(alarm_code)}  ·  PLC state: {plc_state}"
        )
    else:
        st.success(f"✅ Normal operation  ·  PLC state: {plc_state}")

    # --- Process flow diagram: S1 -> S2 -> S3 -> S4 -> S5 -----------------
    _render_flow_diagram(latest, alarm_code)

    # --- KPI cards (colored by threshold) --------------------------------
    _render_kpis(latest)

    # --- Trend charts -----------------------------------------------------
    history = engine.historian.recent(window_s=window)
    if history:
        df = pd.DataFrame(history)
        df["time"] = pd.to_datetime(df["ts"], unit="s")

        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=("Pasteurization temperature (°C)", "Tank level (%)",
                            "Flow rate (L/min)", "Bottles capped"),
        )
        fig.add_trace(go.Scatter(x=df["time"], y=df["pasteur_temp"],
                                 name="pasteur_temp", line=dict(color="#e4572e")),
                      row=1, col=1)
        fig.add_hline(y=config.PASTEUR_SAFE_MAX, line_dash="dot", line_color="red",
                      row=1, col=1)
        fig.add_hline(y=config.PASTEUR_SAFE_MIN, line_dash="dot", line_color="red",
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=df["tank_level"],
                                 name="tank_level", line=dict(color="#17bebb")),
                      row=1, col=2)
        fig.add_trace(go.Scatter(x=df["time"], y=df["flow_rate"],
                                 name="flow_rate", line=dict(color="#4a6fa5")),
                      row=2, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=df["bottle_count"],
                                 name="bottle_count", line=dict(color="#ffc914")),
                      row=2, col=2)

        # Mark the moments an alarm was raised with a dashed vertical line.
        for ev in engine.historian.recent_alarms(limit=50):
            if int(ev.get("alarm_code", 0)) != config.ALARM_NONE \
                    and ev["ts"] >= df["ts"].iloc[0]:
                xpos = pd.to_datetime(ev["ts"], unit="s")
                fig.add_vline(x=xpos, line_color="#c62828", line_dash="dash",
                              line_width=1.5, row=1, col=1)

        fig.update_layout(height=520, showlegend=False, margin=dict(t=40, b=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Waiting for data… press **Start line** in the sidebar.")
        history = []

    # AI assistant (M5). Cache per alarm code so we do not call the model every
    # refresh; only re-query when the alarm changes or the operator asks.
    st.subheader("🤖 AI Operator Assistant (M5)")
    asked = st.button("Ask the assistant now")
    cache = st.session_state.setdefault("ai_cache", {})
    if asked or (alarm_code != config.ALARM_NONE and alarm_code not in cache):
        cache[alarm_code] = assistant.diagnose(latest, alarm_code, history)

    result = cache.get(alarm_code)
    if result is not None:
        box = st.error if alarm_code != config.ALARM_NONE else st.info
        box(
            f"**Diagnosis:** {result['diagnosis_label']}  "
            f"_(confidence: {result['confidence_level']}, via {result['engine']})_\n\n"
            f"{result['recommendation_text']}"
        )
    else:
        st.caption("No active alarm. Click the button to ask for a status check.")

    # Alarm log
    with st.expander("Alarm log"):
        alarms = engine.historian.recent_alarms()
        if alarms:
            adf = pd.DataFrame(alarms)
            adf["time"] = pd.to_datetime(adf["ts"], unit="s")
            st.dataframe(adf[["time", "label", "description"]],
                         use_container_width=True, hide_index=True)
        else:
            st.caption("No alarms logged yet.")


live_view()

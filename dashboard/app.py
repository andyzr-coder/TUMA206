"""M4 - Dashboard (Streamlit + Plotly).

The operator-facing screen. It:
* starts the simulation engine (M1+M2+M3) in a background thread,
* shows live KPIs, trend charts and the alarm banner,
* provides Start / Stop and fault-injection buttons (the M4 output pins),
* shows the AI assistant's recommendation (M5).

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

# Auto-refresh the page so KPIs and charts update live.
try:
    from streamlit_autorefresh import st_autorefresh

    st_autorefresh(interval=1000, key="auto_refresh")
except Exception:  # noqa: BLE001 - optional dependency
    # Fallback: lightweight meta refresh if the helper package is missing.
    st.markdown(
        "<meta http-equiv='refresh' content='2'>", unsafe_allow_html=True
    )

latest = engine.latest()
alarm_code = int(latest.get("alarm_code", config.ALARM_NONE))

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🥤 Smart Beverage Pasteurization & Bottling Line")
st.caption("Digital Twin demo - M1 Plant · M2 PLC · M3 Data Layer · "
           "M4 Dashboard · M5 AI Assistant")

# ---------------------------------------------------------------------------
# Sidebar: operator controls (M4 output pins)
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
    st.subheader("History")
    window = st.slider("Trend window (s)", 30, 600, config.HISTORY_WINDOW_S, 30)
    if st.button("Export CSV", use_container_width=True):
        path = engine.historian.export_csv()
        st.success(f"Exported to {path}")

    st.divider()
    ai_mode = "Claude (Anthropic)" if assistant.using_claude else "Rule-based (no API key)"
    st.caption(f"AI engine: **{ai_mode}**")
    bus_mode = type(engine.bus).__name__
    st.caption(f"Data bus: **{bus_mode}**")

# ---------------------------------------------------------------------------
# Alarm banner
# ---------------------------------------------------------------------------
plc_state = latest.get("plc_state", config.PLC_IDLE)
if alarm_code != config.ALARM_NONE:
    st.error(
        f"🚨 ALARM [{config.ALARM_LABELS.get(alarm_code)}] - "
        f"{config.ALARM_DESCRIPTIONS.get(alarm_code)}  ·  PLC state: {plc_state}"
    )
else:
    st.success(f"✅ Normal operation  ·  PLC state: {plc_state}")

# ---------------------------------------------------------------------------
# KPIs (live tags)
# ---------------------------------------------------------------------------
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Tank level (%)", f"{latest.get('tank_level', 0):.1f}")
k2.metric("Pasteur temp (°C)", f"{latest.get('pasteur_temp', 0):.1f}")
k3.metric("Cooler temp (°C)", f"{latest.get('cooler_temp', 0):.1f}")
k4.metric("Flow (L/min)", f"{latest.get('flow_rate', 0):.1f}")
k5.metric("Bottles capped", int(latest.get("bottle_count", 0)))
k6.metric("Heater power (%)", f"{latest.get('heater_power_cmd', 0):.0f}")

# ---------------------------------------------------------------------------
# Trend charts (Plotly) from the historian
# ---------------------------------------------------------------------------
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
    fig.update_layout(height=520, showlegend=False, margin=dict(t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Waiting for data… press **Start line** in the sidebar.")

# ---------------------------------------------------------------------------
# AI assistant recommendation (M5)
# ---------------------------------------------------------------------------
st.subheader("🤖 AI Operator Assistant (M5)")

# Cache the recommendation per alarm code so we do not call the model on every
# 1 s auto-refresh. We only re-query when the alarm changes or the user asks.
asked = st.button("Ask the assistant now")
cache = st.session_state.setdefault("ai_cache", {})
need_new = asked or (alarm_code != config.ALARM_NONE and alarm_code not in cache)

if need_new:
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

# ---------------------------------------------------------------------------
# Alarm log
# ---------------------------------------------------------------------------
with st.expander("Alarm log"):
    alarms = engine.historian.recent_alarms()
    if alarms:
        adf = pd.DataFrame(alarms)
        adf["time"] = pd.to_datetime(adf["ts"], unit="s")
        st.dataframe(adf[["time", "label", "description"]],
                     use_container_width=True, hide_index=True)
    else:
        st.caption("No alarms logged yet.")

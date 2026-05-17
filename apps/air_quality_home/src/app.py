"""Streamlit dashboard for Awair Element air-quality readings.

Reads the SQLite database written by collect_data.py and renders the latest
reading plus historical trend charts. The time window is user-selectable; the
filter is pushed into SQL so the query stays fast as history accumulates.
"""

import os
import sqlite3

import altair as alt
import pandas as pd
import streamlit as st

# --- Database — same DATA_DIR convention as the collector ---
DB_PATH = os.path.join(os.getenv("DATA_DIR", "/data"), "awair_data.db")

# --- Thresholds for Awair Element metrics (optimal ranges) ---
# Temperature: 20-25°C, Humidity: 40-50%, CO2: <=600 ppm, TVOC: <=300 ppb, PM2.5: <=12 µg/m³
THRESHOLDS = {
    "temp": (20.0, 25.0),
    "humid": (40.0, 50.0),
    "co2": (None, 600.0),
    "voc": (None, 300.0),
    "pm25": (None, 12.0),
}

# Friendly room names — collector stores generic device names for now.
NAME_MAP = {"Awair Element 1": "Bedroom", "Awair Element 2": "Living Room"}

# Selectable history windows. None == no time filter (all history).
RANGE_OPTIONS = {
    "Last 6 hours": pd.Timedelta(hours=6),
    "Last 24 hours": pd.Timedelta(hours=24),
    "Last 7 days": pd.Timedelta(days=7),
    "Last 30 days": pd.Timedelta(days=30),
    "All time": None,
}

OK_ICON = "✅"
ALERT_ICON = "⚠️"


@st.cache_data(ttl=300)
def fetch_data(range_key):
    """Load readings for the selected time window.

    The window filter runs in SQL (against the idx_air_quality_ts index) rather
    than loading the whole table and filtering in pandas.
    """
    columns = "device_name, timestamp, temp, humid, co2, voc, pm25"
    delta = RANGE_OPTIONS[range_key]

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        if delta is None:
            query = f"SELECT {columns} FROM air_quality ORDER BY timestamp"
            params = ()
        else:
            cutoff = (pd.Timestamp.now(tz="Europe/London") - delta).isoformat()
            query = (
                f"SELECT {columns} FROM air_quality "
                "WHERE timestamp > ? ORDER BY timestamp"
            )
            params = (cutoff,)
        df = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()

    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(
            "Europe/London"
        )
        df["device_name"] = df["device_name"].replace(NAME_MAP)
    return df


def classify_metric(metric, value):
    """True if `value` is within the optimal range for `metric`."""
    lower, upper = THRESHOLDS[metric]
    if lower is not None and value < lower:
        return False
    if upper is not None and value > upper:
        return False
    return True


def render():
    """Render the Streamlit dashboard. Kept in a function so importing this
    module for tests has no side effects (Streamlit runs it as __main__)."""
    st.set_page_config(page_title="Awair Air Quality", layout="wide")
    st.title("🏡 London Home Air Quality")
    st.markdown("Data collected from Awair Element devices")

    range_key = st.selectbox("Time range:", list(RANGE_OPTIONS.keys()), index=1)
    df = fetch_data(range_key)

    if df.empty:
        st.warning(f"No data found for the selected range ({range_key.lower()}).")
        return

    rooms = df["device_name"].unique()
    selected = st.selectbox("Room:", rooms)
    sub = df[df["device_name"] == selected]

    # Hidden section: classification rules
    with st.expander("How metrics are classified (thresholds and icons)", expanded=False):
        st.markdown(
            """
**Thresholds:**

- **Temperature:** 20°C – 25°C
- **Humidity:** 40% – 50%
- **CO₂:** ≤ 600 ppm
- **TVOC:** ≤ 300 ppb
- **PM2.5:** ≤ 12 µg/m³

**Status Icons:**

- ✅ = Within optimal range
- ⚠️ = Outside optimal range
"""
        )

    if not sub.empty:
        latest = sub.sort_values(by="timestamp", ascending=False).iloc[0]
        st.subheader(f"Latest Reading for {selected}")

        time_str = latest["timestamp"].strftime("%Y-%m-%d<br>%H:%M:%S %Z")
        vals = {
            "temp": f"{latest['temp']:.1f}°C",
            "humid": f"{latest['humid']:.1f}%",
            "co2": f"{latest['co2']:.0f} ppm",
            "voc": f"{latest['voc']:.0f} ppb",
            "pm25": f"{latest['pm25']:.1f} µg/m³",
        }
        status = {m: classify_metric(m, latest[m]) for m in vals}
        icons = {m: OK_ICON if status[m] else ALERT_ICON for m in vals}
        total_ok = sum(status.values())

        col1, col2, col3, col4, col5, col6 = st.columns([1.2, 1, 1, 1, 1, 1.2])
        style = "font-size: 1.1em; font-weight: bold;"
        with col1:
            st.markdown(f"**Time**<br><span style='{style}'>{time_str}</span>", unsafe_allow_html=True)
        with col2:
            st.markdown(f"**Temp**<br><span style='{style}'>{vals['temp']} {icons['temp']}</span>", unsafe_allow_html=True)
        with col3:
            st.markdown(f"**Humidity**<br><span style='{style}'>{vals['humid']} {icons['humid']}</span>", unsafe_allow_html=True)
        with col4:
            st.markdown(f"**CO₂**<br><span style='{style}'>{vals['co2']} {icons['co2']}</span>", unsafe_allow_html=True)
        with col5:
            st.markdown(f"**VOC**<br><span style='{style}'>{vals['voc']} {icons['voc']}</span>", unsafe_allow_html=True)
        with col6:
            st.markdown(f"**PM2.5**<br><span style='{style}'>{vals['pm25']} {icons['pm25']}</span>", unsafe_allow_html=True)

        st.markdown(f"**Overall:** {total_ok}/5 metrics within optimal range")
        st.markdown("---")

    # Historical trend charts. Hours-scale ranges show time-of-day on the axis;
    # multi-day ranges show the date instead.
    axis_format = "%H:%M" if RANGE_OPTIONS[range_key] in (
        RANGE_OPTIONS["Last 6 hours"],
        RANGE_OPTIONS["Last 24 hours"],
    ) else "%m-%d %H:%M"

    base = alt.Chart(sub).mark_line(interpolate="monotone").encode(
        x=alt.X("timestamp:T", title="Time", axis=alt.Axis(format=axis_format, labelAngle=45))
    ).properties(title=f"{range_key} — {selected}")
    for title, field in {
        "Temperature (°C)": "temp:Q",
        "Humidity (%)": "humid:Q",
        "CO₂ (ppm)": "co2:Q",
        "VOC (ppb)": "voc:Q",
        "PM2.5 (µg/m³)": "pm25:Q",
    }.items():
        st.altair_chart(base.encode(y=alt.Y(field, title=title)), use_container_width=True)

    if st.checkbox("Show raw data"):
        st.dataframe(sub)


if __name__ == "__main__":
    render()

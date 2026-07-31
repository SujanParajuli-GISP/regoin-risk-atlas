import streamlit as st
import geopandas as gpd
import folium
from folium import MacroElement
from branca.element import Template
from streamlit_folium import st_folium
from shapely.geometry import Point
import os
import math
import pandas as pd
import plotly.express as px

MAP_HEIGHT_PX = 560
MAP_WIDTH_PX = 820  # assumed rendered width of the map column, used only for the zoom-fit calculation

st.set_page_config(layout="wide", page_title="Nepal Risk Atlas", page_icon="🌊")

RISK_COLORS = {
    "Critical": "#e5484d",
    "High": "#f5a340",
    "Moderate": "#f0c33c",
    "Low": "#4cb782",
}
RISK_ORDER = ["Critical", "High", "Moderate", "Low"]
TREND_COLORS = {
    "Rapid Degradation": "#e5484d",
    "Worsening": "#f5a340",
    "Stable": "#8ea0b3",
    "Improving": "#4cb782",
}
TREND_ORDER = ["Rapid Degradation", "Worsening", "Stable", "Improving"]
NEUTRAL = "#8ea0b3"
ACCENT = "#74c000"


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def rgba(hex_color, alpha):
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha})"


def badge_html(label, color):
    if not label or pd.isna(label):
        return ""
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;'
        f'font-size:12px;font-weight:600;color:{color};'
        f'background:{rgba(color, 0.14)};border:1px solid {rgba(color, 0.35)};">{label}</span>'
    )


def kpi_card(label, value, subtext, color):
    return (
        f'<div class="kpi-card" style="background:{rgba(color, 0.08)};border:1px solid {rgba(color, 0.25)};">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value" style="color:{color};">{value}</div>'
        f'<div class="kpi-subtext">{subtext}</div>'
        f'</div>'
    )


def inject_css():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

        .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

        .app-header { display:flex; align-items:baseline; gap:12px; margin-bottom: 4px; }
        .app-header h1 { font-size: 1.7rem; font-weight: 700; margin: 0; }
        .app-subtitle { color: #8ea0b3; font-size: 0.92rem; margin-bottom: 1.2rem; }

        .kpi-row { display:flex; gap:12px; margin-bottom: 1.4rem; flex-wrap: wrap; }
        .kpi-card {
            flex: 1 1 150px; border-radius: 10px; padding: 14px 16px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.15);
        }
        .kpi-label { font-size: 11px; font-weight: 600; letter-spacing: 0.04em;
            text-transform: uppercase; color: #8ea0b3; margin-bottom: 6px; }
        .kpi-value { font-size: 1.6rem; font-weight: 700; line-height: 1.1; }
        .kpi-subtext { font-size: 12px; color: #8ea0b3; margin-top: 4px; }

        .panel-title { font-size: 1.05rem; font-weight: 600; margin-bottom: 2px; }
        .panel-caption { font-size: 12px; color: #8ea0b3; margin-bottom: 10px; }

        .block-card {
            border-radius: 10px; padding: 16px; margin-bottom: 12px;
            background: rgba(255,255,255,0.02); border: 1px solid #272C34;
        }
        .score-row { display:flex; justify-content: space-between; align-items:center; margin: 10px 0; }
        .score-label { font-size: 13px; color: #8ea0b3; }
        .score-value { font-size: 15px; font-weight: 700; }

        [data-testid="stSidebar"] { border-right: 1px solid #272C34; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# --- 1. LOAD DATA ---
@st.cache_data
def load_data():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Prefer the nationwide atlas (all districts); fall back to the original
    # Saptari-only file if the nationwide pipeline hasn't been run yet.
    nepal_path = os.path.join(base_dir, "data", "final_risk_atlas_nepal.geojson")
    saptari_path = os.path.join(base_dir, "data", "final_risk_atlas_saptari.geojson")
    file_path = nepal_path if os.path.exists(nepal_path) else saptari_path

    if not os.path.exists(file_path):
        st.error(f"❌ File not found: {file_path}")
        st.stop()

    gdf = gpd.read_file(file_path)
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    # Standardize Column Names
    rename_map = {
        "Flood_Risk_Score": "Flood_Risk_Score",
        "GW_Stress_Score": "GW_Stress_Score",
        "Risk_Category": "Risk_Category",
        "Compound_Score": "Compound_Score",
        "Trend_Status": "Trend_Status",
        "Degradation_Rate": "Degradation_Rate",
        "GaPa_NaPa": "block_name"
    }
    gdf = gdf.rename(columns=rename_map)

    # Ensure Block Name is string
    if "block_name" in gdf.columns:
        gdf["block_name"] = gdf["block_name"].astype(str)
    else:
        gdf["block_name"] = gdf.index.astype(str)

    return gdf


def _mercator_y(lat_deg):
    lat_rad = math.radians(max(min(lat_deg, 89.9), -89.9))
    return math.log(math.tan(math.pi / 4 + lat_rad / 2))


def bounds_to_zoom(minx, miny, maxx, maxy, map_width_px=MAP_WIDTH_PX, map_height_px=MAP_HEIGHT_PX, padding_frac=0.12):
    """Compute the Leaflet zoom level that fits a lon/lat bounding box into a
    target pixel size, using the actual Web Mercator projection math (accounts
    for latitude compression and the map's aspect ratio) - the same approach
    Leaflet's own fitBounds() uses internally.

    fit_bounds() itself is unreliable inside the streamlit-folium iframe (the
    map container isn't sized yet when Leaflet computes it), so we compute a
    static zoom_start instead, assuming the map renders at roughly
    map_width_px x map_height_px.
    """
    lon_span = max(maxx - minx, 1e-6)
    lat_span_merc = max(_mercator_y(maxy) - _mercator_y(miny), 1e-9)

    eff_w = map_width_px * (1 - padding_frac)
    eff_h = map_height_px * (1 - padding_frac)

    zoom_x = math.log2(eff_w * 360 / (256 * lon_span))
    zoom_y = math.log2(eff_h * 2 * math.pi / (256 * lat_span_merc))

    return max(5, min(14, math.floor(min(zoom_x, zoom_y))))


def build_map(gdf):
    minx, miny, maxx, maxy = gdf.total_bounds
    m = folium.Map(
        location=[(miny + maxy) / 2, (minx + maxx) / 2],
        zoom_start=bounds_to_zoom(minx, miny, maxx, maxy),
        tiles="CartoDB dark_matter",
    )

    def style_function(feature):
        risk = feature["properties"].get("Risk_Category", "Low")
        return {
            "fillColor": RISK_COLORS.get(risk, "gray"),
            "color": "#0E1115",
            "weight": 0.6,
            "fillOpacity": 0.75,
        }

    def highlight_function(feature):
        return {"weight": 2.5, "color": "#ffffff", "fillOpacity": 0.9}

    folium.GeoJson(
        gdf,
        style_function=style_function,
        highlight_function=highlight_function,
        tooltip=folium.GeoJsonTooltip(fields=["block_name", "Risk_Category"], aliases=["Unit:", "Risk:"]),
        popup=folium.GeoJsonPopup(fields=["block_name"], aliases=["Municipal Unit:"]),
    ).add_to(m)

    legend_rows = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin:3px 0;">'
        f'<span style="width:10px;height:10px;border-radius:2px;background:{RISK_COLORS[cat]};display:inline-block;"></span>'
        f'<span>{cat}</span></div>'
        for cat in RISK_ORDER
    )
    legend_html = f"""
    {{% macro html(this, kwargs) %}}
    <div style="position: fixed; bottom: 24px; left: 24px; z-index:9999;
        background: rgba(22,26,33,0.92); border:1px solid #272C34; border-radius:8px;
        padding:10px 14px; font-family: Inter, sans-serif; font-size:12px; color:#E7EBEF;">
      <div style="font-weight:600; margin-bottom:6px;">Risk Level</div>
      {legend_rows}
    </div>
    {{% endmacro %}}
    """
    legend = MacroElement()
    legend._template = Template(legend_html)
    m.get_root().add_child(legend)

    return m


def risk_breakdown_chart(gdf):
    counts = gdf["Risk_Category"].value_counts()
    counts = counts.reindex([c for c in RISK_ORDER if c in counts.index] + [c for c in counts.index if c not in RISK_ORDER])
    fig = px.pie(
        names=counts.index,
        values=counts.values,
        hole=0.62,
        color=counts.index,
        color_discrete_map=RISK_COLORS,
    )
    fig.update_traces(textinfo="value", hovertemplate="%{label}: %{value} units<extra></extra>")
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=10, b=10, l=10, r=10),
        height=260,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15),
    )
    return fig


def trend_breakdown_chart(gdf):
    if "Trend_Status" not in gdf.columns:
        return None
    counts = gdf["Trend_Status"].value_counts()
    order = [c for c in TREND_ORDER if c in counts.index] + [c for c in counts.index if c not in TREND_ORDER]
    counts = counts.reindex(order)
    fig = px.bar(
        x=counts.values,
        y=counts.index,
        orientation="h",
        color=counts.index,
        color_discrete_map=TREND_COLORS,
    )
    fig.update_traces(hovertemplate="%{y}: %{x} units<extra></extra>")
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=10, b=10, l=10, r=10),
        height=260,
        showlegend=False,
        xaxis_title=None,
        yaxis_title=None,
    )
    return fig


inject_css()

try:
    gdf_all = load_data()
except Exception as e:
    st.error(f"Error loading data: {e}")
    st.stop()

has_districts = "District" in gdf_all.columns

if "selected_unit" not in st.session_state:
    st.session_state.selected_unit = "None"
if "last_map_click" not in st.session_state:
    st.session_state.last_map_click = None
if "pending_unit_click" not in st.session_state:
    st.session_state.pending_unit_click = None

# A map click updates `pending_unit_click` + reruns (see below). The selectbox
# below owns `selected_unit` via its `key`, so Streamlit forbids writing to it
# after the widget is instantiated - apply any pending click here, before the
# widget is created.
if st.session_state.pending_unit_click is not None:
    st.session_state.selected_unit = st.session_state.pending_unit_click
    st.session_state.pending_unit_click = None

# --- 2. SIDEBAR ---
with st.sidebar:
    st.markdown("### 🔍 Controls")

    if has_districts:
        all_districts = sorted(gdf_all["District"].dropna().unique().tolist())
        default_idx = all_districts.index("Saptari") if "Saptari" in all_districts else 0
        selected_district = st.selectbox("District", options=all_districts, index=default_idx)
        district_gdf = gdf_all[gdf_all["District"] == selected_district].reset_index(drop=True)
    else:
        # Single-district file (no District column) - nothing to filter.
        selected_district = "Saptari"
        district_gdf = gdf_all

    if "Risk_Category" in district_gdf.columns:
        risk_options = [c for c in RISK_ORDER if c in district_gdf["Risk_Category"].unique()] + \
            [c for c in district_gdf["Risk_Category"].unique() if c not in RISK_ORDER]
        selected_risks = st.multiselect("Risk Level", options=risk_options, default=risk_options)
        gdf = district_gdf[district_gdf["Risk_Category"].isin(selected_risks)].reset_index(drop=True)
    else:
        gdf = district_gdf

    st.divider()
    all_units = ["None"] + sorted(gdf["block_name"].unique().tolist())
    if st.session_state.selected_unit not in all_units:
        st.session_state.selected_unit = "None"
    selected_block = st.selectbox("Search Municipal Unit", options=all_units, key="selected_unit")

    st.divider()
    st.caption(f"Showing **{len(gdf)}** of **{len(district_gdf)}** municipal units in {selected_district}")

# --- 3. MAIN DASHBOARD ---
districts_covered = gdf_all["District"].nunique() if has_districts else 1
st.markdown(
    f'<div class="app-header"><h1>🌊 {selected_district} Hydro-Climatic Risk Atlas</h1></div>'
    f'<div class="app-subtitle">Nationwide flood & groundwater compound risk monitoring · {districts_covered} district(s) covered</div>',
    unsafe_allow_html=True,
)

if gdf.empty:
    st.warning("No municipal units match the current filters. Adjust the Risk Level filter in the sidebar.")
    st.stop()

# --- KPI ROW ---
critical_n = int((gdf["Risk_Category"] == "Critical").sum()) if "Risk_Category" in gdf.columns else 0
high_n = int((gdf["Risk_Category"] == "High").sum()) if "Risk_Category" in gdf.columns else 0
avg_score = gdf["Compound_Score"].mean() if "Compound_Score" in gdf.columns else None
worsening_n = int(gdf["Trend_Status"].isin(["Worsening", "Rapid Degradation"]).sum()) if "Trend_Status" in gdf.columns else 0

kpis = [
    kpi_card("Total Municipal Units", f"{len(gdf):,}", f"in {selected_district}", ACCENT),
    kpi_card("Critical Hotspots", f"{critical_n:,}", "top-tier compound risk", RISK_COLORS["Critical"]),
    kpi_card("High Risk", f"{high_n:,}", "elevated compound risk", RISK_COLORS["High"]),
    kpi_card("Districts Covered", f"{districts_covered}", "nationwide dataset", NEUTRAL),
    kpi_card("Avg Compound Score", f"{avg_score:.2f}" if avg_score is not None else "—", "0 (low) – 1 (critical)", ACCENT),
    kpi_card("Worsening Trend", f"{worsening_n:,}", "degrading 5-yr trajectory", RISK_COLORS["High"]),
]
st.markdown(f'<div class="kpi-row">{"".join(kpis)}</div>', unsafe_allow_html=True)

col1, col2 = st.columns([3, 1.2])

with col1:
    st.markdown('<div class="panel-title">Interactive Map</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-caption">Color-coded by compound risk category · click a unit to see details</div>', unsafe_allow_html=True)
    m = build_map(gdf)
    map_data = st_folium(m, width="100%", height=MAP_HEIGHT_PX, returned_objects=["last_object_clicked"])

    clicked = map_data.get("last_object_clicked") if map_data else None
    if clicked:
        click_coords = (round(clicked["lat"], 6), round(clicked["lng"], 6))
        if click_coords != st.session_state.last_map_click:
            st.session_state.last_map_click = click_coords
            point = Point(click_coords[1], click_coords[0])
            matches = gdf[gdf.geometry.contains(point)]
            if matches.empty:
                # Fall back to the nearest unit in case the click landed just
                # outside a simplified polygon boundary.
                matches = gdf.iloc[[gdf.geometry.distance(point).idxmin()]]
            st.session_state.pending_unit_click = matches.iloc[0]["block_name"]
            st.rerun()

with col2:
    st.markdown('<div class="panel-title">Municipal Unit Details</div>', unsafe_allow_html=True)

    if selected_block != "None" and selected_block in gdf["block_name"].values:
        row = gdf[gdf["block_name"] == selected_block].iloc[0]

        tab1, tab2, tab3 = st.tabs(["⚠️ Overview", "🌊 Flood", "💧 Groundwater"])

        with tab1:
            st.markdown(f"#### {row['block_name']}")
            risk = row.get("Risk_Category", "Unknown")
            trend = row.get("Trend_Status", None)
            badges = badge_html(risk, RISK_COLORS.get(risk, NEUTRAL))
            if trend is not None and pd.notnull(trend):
                badges += " " + badge_html(trend, TREND_COLORS.get(trend, NEUTRAL))
            st.markdown(badges, unsafe_allow_html=True)
            if "Compound_Score" in row and pd.notnull(row["Compound_Score"]):
                st.write("")
                st.progress(float(row["Compound_Score"]), text=f"Composite Risk: {row['Compound_Score']:.2f}")

        with tab2:
            st.markdown("##### Surface Water")
            if "Flood_Risk_Score" in row and pd.notnull(row["Flood_Risk_Score"]):
                val = float(row["Flood_Risk_Score"])
                st.metric("Flood Pressure Index", f"{val:.2f}", help="Higher is worse")
                if val > 0.6:
                    st.error("⚠️ High Saturation")
                else:
                    st.success("✅ Normal Drainage")
            else:
                st.warning("Data Missing")

        with tab3:
            st.markdown("##### Aquifer Status")

            if "GW_Stress_Score" in row and pd.notnull(row["GW_Stress_Score"]):
                val = float(row["GW_Stress_Score"])
                st.metric("Current Stress Index", f"{val:.2f}")

            st.divider()

            if "Degradation_Rate" in row and pd.notnull(row["Degradation_Rate"]):
                rate = float(row["Degradation_Rate"])

                if rate > 0:
                    st.metric("Aquifer Depletion Rate", f"{rate:.3f}", delta="- Worsening", delta_color="inverse")
                elif rate < 0:
                    st.metric("Aquifer Recovery Rate", f"{abs(rate):.3f}", delta="+ Improving", delta_color="normal")
                else:
                    st.metric("Aquifer Rate", "Stable", delta="No Change", delta_color="off")

                st.caption("*(Based on multi-year trend slope)*")
            else:
                st.info("Trend data unavailable for this unit.")

    else:
        st.info("👈 Click a unit on the map, or search one in the sidebar, to see details.")

# --- 4. ANALYTICS ROW ---
st.write("")
acol1, acol2, acol3 = st.columns([1, 1, 1.4])

with acol1:
    st.markdown('<div class="panel-title">Risk Breakdown</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-caption">Municipal units by compound risk category</div>', unsafe_allow_html=True)
    st.plotly_chart(risk_breakdown_chart(gdf), use_container_width=True, config={"displayModeBar": False})

with acol2:
    trend_fig = trend_breakdown_chart(gdf)
    st.markdown('<div class="panel-title">Trend Outlook</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-caption">5-year groundwater trajectory</div>', unsafe_allow_html=True)
    if trend_fig is not None:
        st.plotly_chart(trend_fig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("Trend data unavailable.")

with acol3:
    st.markdown('<div class="panel-title">🚨 Priority List</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-caption">Highest compound-risk municipal units</div>', unsafe_allow_html=True)
    if "Risk_Category" in gdf.columns and "Compound_Score" in gdf.columns:
        top = gdf.sort_values("Compound_Score", ascending=False).head(10)
        st.dataframe(
            top[["block_name", "Risk_Category", "Compound_Score"]],
            hide_index=True,
            use_container_width=True,
            column_config={
                "block_name": st.column_config.TextColumn("Municipal Unit"),
                "Risk_Category": st.column_config.TextColumn("Risk"),
                "Compound_Score": st.column_config.ProgressColumn(
                    "Compound Score", min_value=0, max_value=1, format="%.2f"
                ),
            },
        )
    else:
        st.info("Risk data unavailable.")

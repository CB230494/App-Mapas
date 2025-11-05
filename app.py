# -*- coding: utf-8 -*-
# App 100% en Streamlit (sin Excel): crear puntos, colores, etiquetas,
# ver puntos o heatmap, leyenda y exportar a GeoJSON/CSV/Shapefile.
# Ejecuta: streamlit run app.py

import io, json, zipfile, tempfile, re
from pathlib import Path
from typing import Dict, List

import pandas as pd
import streamlit as st
import pydeck as pdk

# Geospatial para exportar Shapefile
import geopandas as gpd
from shapely.geometry import Point

st.set_page_config(page_title="Mapas CR – App sin Excel", layout="wide")
st.title("🗺️ Mapas (sin Excel): puntos, etiquetas, colores y export a ArcGIS")

# ====================== ESTADO ======================
if "points" not in st.session_state:
    # points: lista de dicts {"lat": float, "lon": float, "categoria": str, "etiqueta": str}
    st.session_state.points: List[Dict] = []

if "color_cfg" not in st.session_state:
    st.session_state.color_cfg: Dict[str, str] = {}  # {categoria: "#RRGGBB"}

if "project_name" not in st.session_state:
    st.session_state.project_name = "proyecto_mapa"

# ====================== BARRA LATERAL ======================
st.sidebar.header("Proyecto")
st.session_state.project_name = st.sidebar.text_input("Nombre del proyecto", st.session_state.project_name)

st.sidebar.header("Visualización")
modo = st.sidebar.selectbox("Modo de mapa", ["Puntos", "Heatmap"], index=0)
mostrar_etiquetas = st.sidebar.checkbox("Mostrar etiquetas (TextLayer)", True)
zoom_inicial = st.sidebar.slider("Zoom inicial", 4, 15, 7)
radio_punto = st.sidebar.slider("Radio de punto (px)", 1, 40, 12)
heat_radius = st.sidebar.slider("Radio Heatmap (px)", 20, 120, max(20, radio_punto*2))

st.sidebar.header("Guardar / Cargar")
st.sidebar.caption("Exporta/importa el proyecto (puntos + paleta) 100% desde la app.")
c1, c2 = st.sidebar.columns(2)
with c1:
    export_json_btn = st.button("💾 Exportar proyecto (JSON)")
with c2:
    import_file = st.file_uploader("Cargar JSON", type=["json"], label_visibility="collapsed")

# ====================== CREAR / EDITAR PUNTOS ======================
st.subheader("➕ Agregar puntos (sin archivos)")
with st.form("form_add_point", clear_on_submit=True):
    cols = st.columns([1,1,1,2])
    with cols[0]:
        lat = st.number_input("Latitud", value=9.93, format="%.8f")
    with cols[1]:
        lon = st.number_input("Longitud", value=-84.08, format="%.8f")
    with cols[2]:
        categoria = st.text_input("Categoría", value="Riesgo Social")
    with cols[3]:
        etiqueta = st.text_input("Etiqueta (máx 100)", value="San José – punto 1")[:100]
    submitted = st.form_submit_button("Agregar punto")
    if submitted:
        st.session_state.points.append({
            "lat": float(lat), "lon": float(lon),
            "categoria": categoria.strip() or "Sin categoría",
            "etiqueta": etiqueta.strip()
        })
        # Color por defecto para categorías nuevas
        cat = categoria.strip() or "Sin categoría"
        if cat not in st.session_state.color_cfg:
            default = "#1f77b4"
            if "riesgo" in cat.lower(): default = "#d62728"
            if "delito" in cat.lower(): default = "#ff7f0e"
            if "otro"  in cat.lower(): default = "#2ca02c"
            st.session_state.color_cfg[cat] = default

# ====================== TABLA Y ACCIONES SOBRE PUNTOS ======================
st.subheader("🧭 Puntos cargados")
df = pd.DataFrame(st.session_state.points)
if df.empty:
    st.info("Todavía no hay puntos. Agrega algunos en el formulario de arriba.")
else:
    # Mostrar tabla amigable
    st.dataframe(df, use_container_width=True)

    # Borrar / Limpiar
    colA, colB, colC = st.columns(3)
    with colA:
        idx_to_delete = st.number_input("Eliminar por índice (fila)", min_value=0, max_value=len(df)-1, value=0, step=1)
        if st.button("🗑️ Eliminar fila"):
            st.session_state.points.pop(int(idx_to_delete))
            st.rerun()
    with colB:
        if st.button("🧹 Eliminar TODO"):
            st.session_state.points.clear()
            st.rerun()
    with colC:
        st.write(" ")  # espacio

# ====================== COLORES POR CATEGORÍA ======================
st.subheader("🎨 Colores por categoría")
cats = sorted(set([p["categoria"] for p in st.session_state.points])) if st.session_state.points else []
if cats:
    cols = st.columns(min(5, max(3, len(cats))))
    for i, c in enumerate(cats):
        with cols[i % len(cols)]:
            current = st.session_state.color_cfg.get(c, "#1f77b4")
            st.session_state.color_cfg[c] = st.color_picker(c, current)

# ====================== MAPA ======================
def hex_to_rgba(hx: str, alpha: int = 180) -> List[int]:
    hx = hx.strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", hx):
        hx = hx.replace("#","")
        r = int(hx[0:2], 16)
        g = int(hx[2:4], 16)
        b = int(hx[4:6], 16)
        return [r,g,b,alpha]
    return [31,119,180,alpha]

def current_df_with_colors() -> pd.DataFrame:
    df = pd.DataFrame(st.session_state.points)
    if df.empty:
        return df
    df["_rgba"] = df["categoria"].map(lambda c: hex_to_rgba(st.session_state.color_cfg.get(c, "#1f77b4")))
    df["text60"] = df["etiqueta"].astype(str).str[:60]
    return df

df_map = current_df_with_colors()
center_lat = df_map["lat"].mean() if not df_map.empty else 9.93
center_lon = df_map["lon"].mean() if not df_map.empty else -84.08

layers = []
if modo == "Puntos":
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=df_map,
            get_position='[lon, lat]',
            get_radius=radio_punto,
            get_fill_color="_rgba",
            pickable=True,
            auto_highlight=True
        )
    )
else:
    layers.append(
        pdk.Layer(
            "HeatmapLayer",
            data=df_map,
            get_position='[lon, lat]',
            aggregation="MEAN",
            radius_pixels=heat_radius,
        )
    )

if mostrar_etiquetas and not df_map.empty:
    layers.append(
        pdk.Layer(
            "TextLayer",
            data=df_map,
            get_position='[lon, lat]',
            get_text="text60",
            get_size=14,
            get_color=[0, 0, 0, 220],
            get_angle=0,
            get_alignment_baseline='"bottom"',
            billboard=True
        )
    )

tooltip = {
    "html":"<b>{etiqueta}</b><br/>Cat: {categoria}<br/>[{lat}, {lon}]",
    "style":{"backgroundColor":"white","color":"black"}
}

deck = pdk.Deck(
    map_style="mapbox://styles/mapbox/light-v11",
    initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=zoom_inicial, pitch=0),
    layers=layers,
    tooltip=tooltip
)

st.pydeck_chart(deck, use_container_width=True)

# ===== Leyenda =====
if cats:
    with st.expander("🔎 Leyenda de categorías", expanded=True):
        lg_cols = st.columns(min(6, max(2, len(cats))))
        for i, c in enumerate(cats):
            rgba = hex_to_rgba(st.session_state.color_cfg.get(c, "#1f77b4"))
            swatch = f"background: rgba({rgba[0]}, {rgba[1]}, {rgba[2]}, {rgba[3]/255:.2f}); width:18px; height:18px; display:inline-block; border:1px solid #888; margin-right:8px;"
            lg_cols[i % len(lg_cols)].markdown(f'<div style="{swatch}"></div> {c}', unsafe_allow_html=True)

st.divider()

# ====================== EXPORTS ======================
def df_to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame(
        df.drop(columns=["_rgba","text60"], errors="ignore").copy(),
        geometry=[Point(xy) for xy in zip(df["lon"], df["lat"])],
        crs="EPSG:4326"
    )
    return gdf

def export_geojson_bytes(df: pd.DataFrame) -> bytes:
    gdf = df_to_gdf(df)
    return gdf.to_json().encode("utf-8")

def export_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.drop(columns=["_rgba","text60"], errors="ignore").to_csv(index=False).encode("utf-8")

def export_shapefile_zip_bytes(df: pd.DataFrame) -> bytes:
    gdf = df_to_gdf(df)
    with tempfile.TemporaryDirectory() as tmpd:
        shp_path = Path(tmpd) / "puntos.shp"
        gdf.to_file(shp_path, driver="ESRI Shapefile", encoding="utf-8")
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in Path(tmpd).glob("puntos.*"):
                zf.write(p, arcname=p.name)
        zbuf.seek(0)
        return zbuf.getvalue()

col1, col2, col3 = st.columns(3)
with col1:
    st.subheader("📤 Exportar")
    geojson_data = export_geojson_bytes(df_map) if not df_map.empty else None
    csv_data     = export_csv_bytes(df_map) if not df_map.empty else None
    shpzip_data  = export_shapefile_zip_bytes(df_map) if not df_map.empty else None

    st.download_button("⬇️ GeoJSON", data=geojson_data or b"", file_name=f"{st.session_state.project_name}_puntos.geojson",
                       mime="application/geo+json", disabled=df_map.empty)
with col2:
    st.write(" ")
    st.download_button("⬇️ Shapefile (ZIP)", data=shpzip_data or b"", file_name=f"{st.session_state.project_name}_puntos_shp.zip",
                       mime="application/zip", disabled=df_map.empty)
with col3:
    st.write(" ")
    st.download_button("⬇️ CSV", data=csv_data or b"", file_name=f"{st.session_state.project_name}_puntos.csv",
                       mime="text/csv", disabled=df_map.empty)

# ====================== EXPORT / IMPORT DE PROYECTO (JSON) ======================
def export_project_json() -> bytes:
    payload = {
        "name": st.session_state.project_name,
        "points": st.session_state.points,
        "color_cfg": st.session_state.color_cfg
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

def import_project_json(file_bytes: bytes):
    try:
        data = json.loads(file_bytes.decode("utf-8"))
        st.session_state.project_name = data.get("name", st.session_state.project_name)
        st.session_state.points = data.get("points", [])
        st.session_state.color_cfg = data.get("color_cfg", {})
        st.success("Proyecto cargado correctamente.")
        st.rerun()
    except Exception as e:
        st.error(f"No se pudo importar el JSON: {e}")

if export_json_btn:
    st.download_button("Guardar JSON del proyecto", data=export_project_json(),
                       file_name=f"{st.session_state.project_name}.json", mime="application/json")

if import_file is not None:
    import_project_json(import_file.read())

# ====================== NOTAS ======================
st.info(
    "👉 Flujo recomendado: agrega puntos aquí (lat/lon), define categorías y colores, "
    "visualiza en Puntos o Heatmap y exporta a **GeoJSON/CSV/Shapefile**. "
    "Sube cualquiera de esos archivos a **ArcGIS Online** o **ArcGIS Pro** y aplica tu simbología allá. "
    "Puedes guardar el proyecto como **JSON** y cargarlo después, todo desde esta app."
)



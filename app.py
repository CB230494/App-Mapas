# -*- coding: utf-8 -*-
# Constructor de "Casos de Éxito" por capas (clic en mapa) + export a ArcGIS
# Ejecuta: streamlit run app.py

import io, json, zipfile, tempfile, base64, re
from pathlib import Path
from typing import Dict, List, Any

import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

import folium
from streamlit_folium import st_folium

# ==================== CONFIG INICIAL ====================
st.set_page_config(page_title="Casos de Éxito – Constructor de Mapas", layout="wide")
st.title("🌟 Casos de Éxito – Mapa interactivo por capas")
st.caption("Haz clic en el mapa para agregar casos, organiza por capas y exporta a ArcGIS (GeoJSON/Shapefile/CSV).")

# Estado
if "layers" not in st.session_state:
    # capas predefinidas (puedes agregar más en la UI)
    st.session_state.layers: Dict[str, Dict[str, Any]] = {
        "Infraestructura recuperada": {"color": "#2ca02c", "visible": True, "features": []},
        "Prevención comunitaria":     {"color": "#1f77b4", "visible": True, "features": []},
        "Operativos y control":       {"color": "#ff7f0e", "visible": True, "features": []},
        "Gestión interinstitucional": {"color": "#9467bd", "visible": True, "features": []},
    }

if "project_name" not in st.session_state:
    st.session_state.project_name = "casos_exito"

def _hex_ok(h):
    return bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", (h or "").strip()))

def _clean_hex(h):
    h = (h or "#1f77b4").strip()
    if not h.startswith("#"): h = "#" + h
    return h if _hex_ok(h) else "#1f77b4"

# ==================== SIDEBAR – PROYECTO / CAPAS ====================
st.sidebar.header("Proyecto")
st.session_state.project_name = st.sidebar.text_input("Nombre del proyecto", st.session_state.project_name)

st.sidebar.header("Capas (tipo de caso)")
# Editar visibilidad/color y agregar capas nuevas
for lname, meta in list(st.session_state.layers.items()):
    with st.sidebar.expander(lname, expanded=False):
        meta["visible"] = st.checkbox("Visible", value=meta.get("visible", True), key=f"vis_{lname}")
        meta["color"] = _clean_hex(st.color_picker("Color", value=meta.get("color", "#1f77b4"), key=f"col_{lname}"))
        if st.button("Eliminar capa", key=f"del_{lname}"):
            del st.session_state.layers[lname]
            st.rerun()

with st.sidebar.expander("➕ Agregar capa"):
    new_layer = st.text_input("Nombre de la nueva capa", "")
    new_color = st.color_picker("Color", "#17becf", key="newcol")
    if st.button("Crear capa"):
        name = new_layer.strip()
        if name and name not in st.session_state.layers:
            st.session_state.layers[name] = {"color": _clean_hex(new_color), "visible": True, "features": []}
            st.success(f"Capa '{name}' creada.")
            st.rerun()

# ==================== FORMULARIO DE CASO ====================
st.subheader("📝 Ficha del caso (aplica al próximo clic en el mapa)")
cols = st.columns([1, 1, 1, 1])
with cols[0]:
    layer_active = st.selectbox("Capa activa", list(st.session_state.layers.keys()))
with cols[1]:
    titulo = st.text_input("Título", "Parque recuperado y seguro")
with cols[2]:
    fecha = st.date_input("Fecha")
with cols[3]:
    responsable = st.selectbox("Responsable", ["GL", "FP", "Mixta"])

desc = st.text_area("Descripción corta (máx. 240)", "Rehabilitación de iluminación y mobiliario; patrullajes coordinados con comunidad.")[:240]
colz = st.columns([1,1,1])
with colz[0]:
    provincia = st.text_input("Provincia", "San José")
with colz[1]:
    canton = st.text_input("Cantón", "Montes de Oca")
with colz[2]:
    impacto = st.text_input("Indicador de impacto (opcional)", "↓ 35% incidentes en 3 meses")

enlace = st.text_input("Enlace a evidencia (video/fotos/noticia)", "")

st.caption("Consejo: deja prellenada esta ficha, luego haz clic en el mapa sobre la ubicación del caso. Cada clic crea un punto en la **capa activa** con esta ficha.")

# ==================== MAPA Y CAPTURA DE CLIC ====================
st.subheader("🗺️ Mapa (clic para agregar caso)")
# Centro de CR aprox.
m = folium.Map(location=[9.94, -84.10], zoom_start=7, tiles="cartodbpositron")

# Dibujar capas existentes
for lname, meta in st.session_state.layers.items():
    if not meta.get("visible", True): 
        continue
    fg = folium.FeatureGroup(name=lname, show=True)
    for feat in meta.get("features", []):
        lat, lon = feat["geometry"]["coordinates"][1], feat["geometry"]["coordinates"][0]
        props = feat["properties"]
        # Popup HTML corto y elegante
        html = f"""
        <b>{props.get('titulo','(sin título)')}</b><br>
        <i>{props.get('fecha','')}</i><br>
        <b>Resp:</b> {props.get('responsable','')} · <b>Capa:</b> {props.get('layer','')}<br>
        <b>Prov/Cantón:</b> {props.get('provincia','')}/{props.get('canton','')}<br>
        <b>Impacto:</b> {props.get('impacto','')}<br>
        <b>Evidencia:</b> <a href="{props.get('enlace','')}" target="_blank">ver</a><br>
        <hr style="margin:4px 0;">
        {props.get('desc','')}
        """
        folium.CircleMarker(
            location=[lat, lon],
            radius=8,
            color=meta["color"],
            fill=True,
            fill_color=meta["color"],
            fill_opacity=0.85,
            popup=folium.Popup(html, max_width=320, show=False),
            tooltip=props.get('titulo', '(Caso)')
        ).add_to(fg)
    fg.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

# Render interactivo y captura del último clic
map_state = st_folium(m, height=650, width=None, returned_objects=[])

# ¿Hubo clic?
if map_state and map_state.get("last_clicked"):
    lat = map_state["last_clicked"]["lat"]
    lon = map_state["last_clicked"]["lng"]
    # Construir feature (GeoJSON-like)
    props = {
        "layer": layer_active,
        "color": st.session_state.layers[layer_active]["color"],
        "titulo": titulo.strip(),
        "desc": desc.strip(),
        "fecha": str(fecha),
        "provincia": provincia.strip(),
        "canton": canton.strip(),
        "responsable": responsable,
        "impacto": impacto.strip(),
        "enlace": enlace.strip()
    }
    feat = {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]}
    }
    st.session_state.layers[layer_active]["features"].append(feat)
    st.success(f"Caso agregado en '{layer_active}' ({lat:.5f}, {lon:.5f}).")
    st.rerun()

# ==================== TABLAS Y GESTIÓN ====================
st.subheader("📋 Resumen por capas")
tabs = st.tabs(list(st.session_state.layers.keys()))
for i, lname in enumerate(st.session_state.layers.keys()):
    with tabs[i]:
        feats = st.session_state.layers[lname]["features"]
        if not feats:
            st.info("Sin casos aún.")
        else:
            df = pd.DataFrame([{
                "Título": f["properties"]["titulo"],
                "Fecha": f["properties"]["fecha"],
                "Resp": f["properties"]["responsable"],
                "Provincia": f["properties"]["provincia"],
                "Cantón": f["properties"]["canton"],
                "Impacto": f["properties"]["impacto"],
                "Evidencia": f["properties"]["enlace"],
                "Lat": f["geometry"]["coordinates"][1],
                "Lon": f["geometry"]["coordinates"][0],
            } for f in feats])
            st.dataframe(df, use_container_width=True)

            colx, coly = st.columns([1,1])
            with colx:
                idx_del = st.number_input("Eliminar fila (índice)", min_value=0, max_value=len(df)-1, value=0, step=1, key=f"idx_{lname}")
                if st.button("🗑️ Eliminar", key=f"btn_del_{lname}"):
                    st.session_state.layers[lname]["features"].pop(int(idx_del))
                    st.rerun()
            with coly:
                if st.button("🧹 Vaciar capa", key=f"btn_clear_{lname}"):
                    st.session_state.layers[lname]["features"].clear()
                    st.rerun()

st.divider()

# ==================== EXPORTADORES ====================
def all_features_fc() -> Dict[str, Any]:
    """FeatureCollection de todas las capas con propiedad 'layer'."""
    feats = []
    for lname, meta in st.session_state.layers.items():
        for f in meta.get("features", []):
            feats.append(f)
    return {"type":"FeatureCollection","features":feats}

def gdf_from_fc(fc: Dict[str, Any]) -> gpd.GeoDataFrame:
    feats = fc["features"]
    if not feats:
        return gpd.GeoDataFrame(columns=["layer","color","titulo","desc","fecha","provincia","canton","responsable","impacto","enlace","geometry"], geometry="geometry", crs="EPSG:4326")
    recs = []
    for f in feats:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        rec = {**p, "geometry": Point(lon, lat)}
        recs.append(rec)
    gdf = gpd.GeoDataFrame(recs, crs="EPSG:4326")
    return gdf

def export_geojson_bytes(fc: Dict[str, Any]) -> bytes:
    return json.dumps(fc, ensure_ascii=False).encode("utf-8")

def export_csv_bytes(gdf: gpd.GeoDataFrame) -> bytes:
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    df["lat"] = gdf.geometry.y
    df["lon"] = gdf.geometry.x
    return df.to_csv(index=False).encode("utf-8")

def export_shapefile_zip_bytes(gdf: gpd.GeoDataFrame) -> bytes:
    with tempfile.TemporaryDirectory() as tmpd:
        shp = Path(tmpd) / "casos_exito.shp"
        gdf.to_file(shp, driver="ESRI Shapefile", encoding="utf-8")
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in Path(tmpd).glob("casos_exito.*"):
                zf.write(p, arcname=p.name)
        zbuf.seek(0)
        return zbuf.getvalue()

st.subheader("📤 Exportar a ArcGIS")
fc = all_features_fc()
gdf = gdf_from_fc(fc)

c1, c2, c3 = st.columns(3)
with c1:
    st.download_button(
        "⬇️ GeoJSON (todas las capas)",
        data=export_geojson_bytes(fc),
        file_name=f"{st.session_state.project_name}.geojson",
        mime="application/geo+json",
        disabled=(len(fc["features"]) == 0)
    )
with c2:
    st.download_button(
        "⬇️ Shapefile (ZIP)",
        data=export_shapefile_zip_bytes(gdf) if len(fc["features"]) else b"",
        file_name=f"{st.session_state.project_name}.zip",
        mime="application/zip",
        disabled=(len(fc["features"]) == 0)
    )
with c3:
    st.download_button(
        "⬇️ CSV (atributos + lat/lon)",
        data=export_csv_bytes(gdf) if len(fc["features"]) else b"",
        file_name=f"{st.session_state.project_name}.csv",
        mime="text/csv",
        disabled=(len(fc["features"]) == 0)
    )

st.info(
    "➡️ **Uso en ArcGIS**: Sube el **GeoJSON** o el **ZIP (Shapefile)** como *Feature Layer* a ArcGIS Online/Pro. "
    "La propiedad **layer** conserva qué tipo de caso es; puedes simbolizar por capa o por responsable (GL/FP/Mixta). "
    "El CSV sirve para cargas rápidas; ArcGIS infiere la geolocalización con lat/lon."
)


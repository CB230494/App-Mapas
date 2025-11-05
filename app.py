# -*- coding: utf-8 -*-
# Casos de Éxito – Capas, edición/mover, Heatmap, Dashboard, Filtros Prov→Cantón (catálogo CR) y Google Sheets.
# Ejecuta: streamlit run app.py

import io, json, zipfile, tempfile, re, datetime as dt, uuid
from pathlib import Path
from typing import Dict, List, Any, Optional

import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

import folium
from streamlit_folium import st_folium
from folium.plugins import Draw, MeasureControl, MiniMap, HeatMap, BeautifyIcon

# ====== Opcional: Google Sheets ======
HAS_SHEETS = False
try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_SHEETS = True
except Exception:
    HAS_SHEETS = False

# ==================== Catálogo Costa Rica ====================
CR_CATALOG: Dict[str, List[str]] = {
    "San José": [
        "San José","Escazú","Desamparados","Puriscal","Tarrazú","Aserrí","Mora",
        "Goicoechea","Santa Ana","Alajuelita","Vázquez de Coronado","Acosta",
        "Tibás","Moravia","Montes de Oca","Turrubares","Dota","Curridabat",
        "Pérez Zeledón","León Cortés"
    ],
    "Alajuela": [
        "Alajuela","San Ramón","Grecia","San Mateo","Atenas","Naranjo","Palmares",
        "Poás","Orotina","San Carlos","Zarcero","Sarchí","Upala","Los Chiles","Guatuso"
    ],
    "Cartago": [
        "Cartago","Paraíso","La Unión","Jiménez","Turrialba","Alvarado","Oreamuno","El Guarco"
    ],
    "Heredia": [
        "Heredia","Barva","Santo Domingo","Santa Bárbara","San Rafael","San Isidro",
        "Belén","Flores","San Pablo","Sarapiquí"
    ],
    "Guanacaste": [
        "Liberia","Nicoya","Santa Cruz","Bagaces","Carrillo","Cañas","Abangares",
        "Tilarán","Nandayure","La Cruz","Hojancha"
    ],
    "Puntarenas": [
        "Puntarenas","Esparza","Buenos Aires","Montes de Oro","Osa","Quepos","Golfito",
        "Coto Brus","Parrita","Corredores","Garabito"
    ],
    "Limón": [
        "Limón","Pococí","Siquirres","Talamanca","Matina","Guácimo"
    ],
}

# ==================== Config general ====================
st.set_page_config(page_title="Casos de Éxito – Mapas CR", layout="wide")
st.title("🌟 Casos de Éxito – Mapas por capas (CR)")
st.caption("Marca puntos (clic o ✏️), edita/mueve, Heatmap, Dashboard con filtros por Capa y Provincia→Cantón. Exporta GeoJSON/ZIP/CSV. Conecta a Google Sheets (opcional).")

# ---------- Estado ----------
if "layers" not in st.session_state:
    st.session_state.layers: Dict[str, Dict[str, Any]] = {
        "Infraestructura recuperada": {"color": "#2ca02c", "visible": True, "features": []},
        "Prevención comunitaria":     {"color": "#1f77b4", "visible": True, "features": []},
        "Operativos y control":       {"color": "#ff7f0e", "visible": True, "features": []},
        "Gestión interinstitucional": {"color": "#9467bd", "visible": True, "features": []},
    }
if "project_name" not in st.session_state:
    st.session_state.project_name = "casos_exito"
if "move_target" not in st.session_state:
    st.session_state.move_target: Optional[tuple] = None  # (layer, index)

def _hex_ok(h): return bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", (h or "").strip()))
def _clean_hex(h):
    h = (h or "#1f77b4").strip()
    if not h.startswith("#"): h = "#" + h
    return h if _hex_ok(h) else "#1f77b4"

# ==================== Sidebar ====================
st.sidebar.header("Proyecto")
st.session_state.project_name = st.sidebar.text_input("Nombre del proyecto", st.session_state.project_name, key="proj_name")

st.sidebar.header("Mapa base")
BASEMAPS = {
    "OSM Estándar": {
        "tiles": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
    "Carto Claro": {
        "tiles": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attr": '&copy; <a href="https://carto.com/">CARTO</a>, &copy; OSM',
    },
    "Carto Dark": {
        "tiles": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attr": '&copy; <a href="https://carto.com/">CARTO</a>, &copy; OSM',
    },
    "Stamen Terreno": {
        "tiles": "https://stamen-tiles-{s}.a.ssl.fastly.net/terrain/{z}/{x}/{y}.jpg",
        "attr": 'Map tiles by <a href="http://stamen.com">Stamen</a>, Data &copy; OSM',
    },
    "Stamen Toner": {
        "tiles": "https://stamen-tiles-{s}.a.ssl.fastly.net/toner/{z}/{x}/{y}.png",
        "attr": 'Map tiles by <a href="http://stamen.com">Stamen</a>, Data &copy; OSM',
    },
    "Esri Satélite": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": 'Tiles &copy; <a href="https://www.esri.com/">Esri</a>',
    },
    "Esri Calles": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        "attr": 'Tiles &copy; Esri',
    },
    "Esri Topográfico": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "attr": 'Tiles &copy; Esri',
    },
    "Esri Gray (Light)": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "attr": 'Tiles &copy; Esri',
    },
}
basemap_name = st.sidebar.selectbox("Elegir mapa base", list(BASEMAPS.keys()), index=0, key="basemap")

st.sidebar.header("Capas (tipo de caso)")
for lname, meta in list(st.session_state.layers.items()):
    with st.sidebar.expander(lname, expanded=False):
        meta["visible"] = st.checkbox("Visible", value=meta.get("visible", True), key=f"vis_{lname}")
        meta["color"] = _clean_hex(st.color_picker("Color", value=meta.get("color", "#1f77b4"), key=f"col_{lname}"))
        if st.button("Eliminar capa", key=f"del_{lname}"):
            del st.session_state.layers[lname]; st.rerun()

with st.sidebar.expander("➕ Agregar capa"):
    new_layer = st.text_input("Nombre de la nueva capa", "", key="new_layer")
    new_color = st.color_picker("Color", "#17becf", key="new_layer_color")
    if st.button("Crear capa", key="btn_new_layer"):
        name = new_layer.strip()
        if name and name not in st.session_state.layers:
            st.session_state.layers[name] = {"color": _clean_hex(new_color), "visible": True, "features": []}
            st.success(f"Capa '{name}' creada."); st.rerun()

# ==================== Tabs ====================
tab_mapa, tab_dashboard, tab_export, tab_sheets = st.tabs(["🗺️ Mapa", "📊 Dashboard", "📤 Exportar", "📡 Google Sheets"])

# ==================== Utilidades ====================
def feature_to_row(f: Dict[str, Any]) -> Dict[str, Any]:
    p = f["properties"]; lon, lat = f["geometry"]["coordinates"]
    return {
        "id": p.get("id",""),
        "Capa": p.get("layer",""),
        "Título": p.get("titulo",""),
        "Fecha": p.get("fecha",""),
        "Resp": p.get("responsable",""),
        "Provincia": p.get("provincia",""),
        "Cantón": p.get("canton",""),
        "Impacto": p.get("impacto",""),
        "Evidencia": p.get("enlace",""),
        "Lat": lat, "Lon": lon
    }

def all_features_fc() -> Dict[str, Any]:
    feats = []
    for lname, meta in st.session_state.layers.items():
        feats.extend(meta.get("features", []))
    return {"type":"FeatureCollection","features":feats}

def gdf_from_fc(fc: Dict[str, Any]) -> gpd.GeoDataFrame:
    feats = fc["features"]
    if not feats:
        return gpd.GeoDataFrame(columns=["id","layer","color","titulo","desc","fecha","provincia","canton","responsable","impacto","enlace","geometry"], geometry="geometry", crs="EPSG:4326")
    recs = []
    for f in feats:
        p = f["properties"]; lon, lat = f["geometry"]["coordinates"]
        recs.append({**p, "geometry": Point(lon, lat)})
    return gpd.GeoDataFrame(recs, crs="EPSG:4326")

def _new_id() -> str:
    return uuid.uuid4().hex[:12]

def _build_feature(lon: float, lat: float, props: Dict[str, Any]) -> Dict[str, Any]:
    if not props.get("id"):
        props["id"] = _new_id()
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]}}

def current_df() -> pd.DataFrame:
    fc = all_features_fc()
    feats = fc["features"]
    if not feats: return pd.DataFrame(columns=["id","Capa","Título","Fecha","Resp","Provincia","Cantón","Impacto","Evidencia","Lat","Lon"])
    return pd.DataFrame([feature_to_row(f) for f in feats])

# ==================== 🗺️ MAPA ====================
with tab_mapa:
    st.subheader("Filtros de visualización")

    # Selectores usando catálogo CR (independientes de los datos cargados)
    provs_catalog = ["(todas)"] + list(CR_CATALOG.keys())
    provincia_f = st.selectbox("Provincia", provs_catalog, index=0, key="prov_map")
    cantones_catalog = ["(todos)"] + (CR_CATALOG.get(provincia_f, []) if provincia_f != "(todas)" else sorted({c for cs in CR_CATALOG.values() for c in cs}))
    canton_f = st.selectbox("Cantón", cantones_catalog, index=0, key="canton_map")

    st.subheader("📝 Ficha del caso (se aplica al próximo punto que marques)")
    c1, c2, c3, c4 = st.columns([1,1,1,1])
    with c1: layer_active = st.selectbox("Capa activa", list(st.session_state.layers.keys()), key="layer_active_map")
    with c2: titulo = st.text_input("Título", "Parque recuperado y seguro", key="titulo_map")
    with c3: fecha = st.date_input("Fecha", key="fecha_map")
    with c4: responsable = st.selectbox("Responsable", ["GL", "FP", "Mixta"], key="resp_map")
    desc = st.text_area("Descripción (máx. 240)", "Rehabilitación de iluminación y mobiliario; patrullajes comunitarios.", key="desc_map")[:240]

    d2a, d2b, d2c = st.columns([1,1,1])
    # Prefill según selector
    prov_prefill = provincia_f if provincia_f != "(todas)" else "San José"
    cant_prefill = (canton_f if canton_f != "(todos)" else (CR_CATALOG[prov_prefill][0] if prov_prefill in CR_CATALOG else "Montes de Oca"))
    with d2a: provincia = st.text_input("Provincia", prov_prefill, key="prov_in_form_map")
    with d2b: canton = st.text_input("Cantón",   cant_prefill, key="canton_in_form_map")
    with d2c: impacto = st.text_input("Impacto (opcional)", "↓ 35% incidentes en 3 meses", key="impacto_map")
    enlace = st.text_input("Enlace a evidencia (opcional)", "", key="enlace_map")

    st.divider()
    colx, coly = st.columns([1,1])
    with colx:
        use_heat = st.checkbox("🔥 Mostrar Heatmap (filtrado)", value=False, key="heat_toggle")
        heat_radius = st.slider("Radio Heatmap", 10, 60, 25, 1, key="heat_radius")
    with coly:
        st.caption("🔀 Mover por clic: " + (f"{st.session_state.move_target}" if st.session_state.move_target else "inactivo"))

    # Mapa base
    center_lat, center_lon = 9.94, -84.10
    m = folium.Map(location=[center_lat, center_lon], zoom_start=7, control_scale=True)
    bm = BASEMAPS[basemap_name]
    folium.TileLayer(tiles=bm["tiles"], name=basemap_name, attr=bm["attr"], control=False).add_to(m)
    for nm, cfg in BASEMAPS.items():
        if nm == basemap_name: continue
        folium.TileLayer(tiles=cfg["tiles"], name=nm, attr=cfg["attr"], control=True).add_to(m)

    folium.plugins.Fullscreen(position="topleft").add_to(m)
    m.add_child(MiniMap(toggle_display=True))
    m.add_child(MeasureControl(primary_length_unit="meters", secondary_length_unit="kilometers",
                               primary_area_unit="sqmeters", secondary_area_unit="hectares"))
    folium.LatLngPopup().add_to(m)

    # Filtro función
    def pass_filter(props: Dict[str, Any]) -> bool:
        if provincia_f != "(todas)" and props.get("provincia","") != provincia_f: return False
        if canton_f != "(todos)" and props.get("canton","") != canton_f: return False
        return True

    # Dibujar puntos (viñeta por color de capa)
    all_points_for_heat = []
    for lname, meta in st.session_state.layers.items():
        if not meta.get("visible", True): 
            continue
        fg = folium.FeatureGroup(name=lname, show=True)
        color = _clean_hex(meta["color"])
        for idx, feat in enumerate(meta.get("features", [])):
            props = feat["properties"]
            if not pass_filter(props): 
                continue
            lat, lon = feat["geometry"]["coordinates"][1], feat["geometry"]["coordinates"][0]
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
            icon = BeautifyIcon(icon="circle", icon_shape="marker", text_color="white",
                                background_color=color, border_color=color, spin=False)
            folium.Marker(location=[lat, lon], icon=icon, tooltip=props.get('titulo','(Caso)'),
                          popup=folium.Popup(html, max_width=320)).add_to(fg)
            all_points_for_heat.append([lat, lon, 1])
        fg.add_to(m)

    if use_heat and all_points_for_heat:
        HeatMap(all_points_for_heat, radius=heat_radius, blur=25, min_opacity=0.3, name="Heatmap").add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    Draw(draw_options={"polyline": False, "polygon": False, "rectangle": False, "circle": False,
                       "marker": True, "circlemarker": False},
         edit_options={"edit": False, "remove": False}).add_to(m)

    map_state = st_folium(m, height=650, width=None, key="mapa_casos", feature_group_to_add=None)

    # Propiedades para nuevo punto
    new_props = {
        "id": _new_id(),
        "layer": layer_active,
        "color": _clean_hex(st.session_state.layers[layer_active]["color"]),
        "titulo": titulo.strip(),
        "desc": desc.strip(),
        "fecha": str(fecha),
        "provincia": provincia.strip(),
        "canton": canton.strip(),
        "responsable": responsable,
        "impacto": impacto.strip(),
        "enlace": enlace.strip()
    }

    # Alta por clic (si no estamos moviendo)
    if st.session_state.move_target is None and map_state and map_state.get("last_clicked"):
        lat = map_state["last_clicked"]["lat"]; lon = map_state["last_clicked"]["lng"]
        st.session_state.layers[layer_active]["features"].append(_build_feature(lon, lat, new_props))
        st.success(f"Punto agregado: '{layer_active}' ({lat:.5f}, {lon:.5f})."); st.rerun()

    # Alta por herramienta de dibujo
    drawn = None
    for key in ["last_active_drawing", "last_drawn_feature", "last_drawing"]:
        if map_state and map_state.get(key): drawn = map_state[key]; break
    if st.session_state.move_target is None and drawn:
        try:
            geom = drawn.get("geometry", {})
            if geom.get("type") == "Point":
                lon, lat = geom["coordinates"]
                st.session_state.layers[layer_active]["features"].append(_build_feature(lon, lat, new_props))
                st.success(f"Punto agregado (✏️): '{layer_active}' ({lat:.5f}, {lon:.5f})."); st.rerun()
        except Exception:
            pass

    st.divider()
    st.subheader("📋 Gestión por capas (eliminar / **editar / mover**)")
    tabs = st.tabs(list(st.session_state.layers.keys()))
    for i, lname in enumerate(list(st.session_state.layers.keys())):
        with tabs[i]:
            feats = st.session_state.layers[lname]["features"]
            if not feats:
                st.info("Sin casos aún.")
            else:
                df_layer = pd.DataFrame([feature_to_row(f) for f in feats])
                # Mostrar con filtro aplicado (contexto)
                if provincia_f != "(todas)": df_layer = df_layer[df_layer["Provincia"] == provincia_f]
                if canton_f != "(todos)":   df_layer = df_layer[df_layer["Cantón"] == canton_f]
                st.dataframe(df_layer, use_container_width=True)

                colx, coly, colz = st.columns([1,1,1])
                # Eliminar
                with colx:
                    idx_del = st.number_input("Eliminar (índice)", min_value=0, max_value=len(feats)-1, value=0, step=1, key=f"idx_del_{lname}")
                    if st.button("🗑️ Eliminar", key=f"btn_del_{lname}"):
                        st.session_state.layers[lname]["features"].pop(int(idx_del)); st.rerun()

                # Editar atributos
                with coly:
                    st.markdown("**Editar atributos**")
                    idx_edit = st.number_input("Índice", min_value=0, max_value=len(feats)-1, value=0, step=1, key=f"idx_edit_{lname}")
                    f = feats[int(idx_edit)]
                    with st.form(f"edit_form_{lname}"):
                        p = f["properties"]
                        t = st.text_input("Título", p.get("titulo",""), key=f"title_edit_{lname}")
                        fe = st.date_input("Fecha", value=pd.to_datetime(p.get("fecha", dt.date.today())).date(), key=f"date_edit_{lname}")
                        resp = st.selectbox("Responsable", ["GL","FP","Mixta"],
                                            index=["GL","FP","Mixta"].index(p.get("responsable","GL")),
                                            key=f"resp_edit_{lname}")
                        prov = st.selectbox("Provincia (catálogo)", list(CR_CATALOG.keys()),
                                            index=max(0, list(CR_CATALOG.keys()).index(p.get("provincia","San José"))), key=f"prov_edit_{lname}")
                        cant = st.selectbox("Cantón (catálogo)", CR_CATALOG.get(prov, []),
                                            index=max(0, CR_CATALOG.get(prov, []).index(p.get("canton", CR_CATALOG.get(prov, [''])[0]))),
                                            key=f"cant_edit_{lname}")
                        imp  = st.text_input("Impacto", p.get("impacto",""), key=f"imp_edit_{lname}")
                        enl  = st.text_input("Enlace", p.get("enlace",""), key=f"enl_edit_{lname}")
                        des  = st.text_area("Descripción", p.get("desc",""), key=f"desc_edit_{lname}")
                        submitted = st.form_submit_button("💾 Guardar")
                    if submitted:
                        p.update({"titulo": t.strip(), "fecha": str(fe), "responsable": resp,
                                  "provincia": prov.strip(), "canton": cant.strip(),
                                  "impacto": imp.strip(), "enlace": enl.strip(), "desc": des.strip()})
                        st.success("Caso actualizado."); st.rerun()

                # Mover
                with colz:
                    st.markdown("**Mover ubicación**")
                    idx_move = st.number_input("Índice", min_value=0, max_value=len(feats)-1, value=0, step=1, key=f"idx_move_{lname}")
                    if st.button("🔀 Activar mover por clic", key=f"btn_move_{lname}"):
                        st.session_state.move_target = (lname, int(idx_move)); st.info("Haz clic en el mapa para mover.")
                    if st.button("❌ Cancelar", key=f"btn_cancel_move_{lname}"):
                        st.session_state.move_target = None; st.rerun()

    # Aplicar movimiento con clic
    if st.session_state.move_target and map_state and map_state.get("last_clicked"):
        lat = map_state["last_clicked"]["lat"]; lon = map_state["last_clicked"]["lng"]
        lname, idx = st.session_state.move_target
        st.session_state.layers[lname]["features"][idx]["geometry"]["coordinates"] = [float(lon), float(lat)]
        st.session_state.move_target = None
        st.success(f"Ubicación actualizada a ({lat:.5f}, {lon:.5f})."); st.rerun()

# ==================== 📊 DASHBOARD ====================
with tab_dashboard:
    st.subheader("Filtros")
    df = current_df()
    if df.empty:
        st.info("Aún no hay datos para graficar.")
    else:
        capas = sorted(df["Capa"].unique().tolist())
        capa_f = st.multiselect("Capas", capas, default=capas, key="capas_dash")

        provincia_f_dash = st.selectbox("Provincia", ["(todas)"] + list(CR_CATALOG.keys()), index=0, key="prov_dash")
        cantones_dash = ["(todos)"] + (CR_CATALOG.get(provincia_f_dash, []) if provincia_f_dash != "(todas)" else sorted({c for cs in CR_CATALOG.values() for c in cs}))
        canton_f_dash = st.selectbox("Cantón", cantones_dash, index=0, key="canton_dash")

        fdf = df[df["Capa"].isin(capa_f)].copy()
        if provincia_f_dash != "(todas)": fdf = fdf[fdf["Provincia"] == provincia_f_dash]
        if canton_f_dash != "(todos)":   fdf = fdf[fdf["Cantón"] == canton_f_dash]

        fdf["Fecha"] = pd.to_datetime(fdf["Fecha"], errors="coerce")
        fdf["Año-Mes"] = fdf["Fecha"].dt.to_period("M").astype(str)

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Casos por capa**")
            st.bar_chart(fdf.groupby("Capa")["Título"].count().sort_values(ascending=False))
        with c2:
            st.markdown("**Casos por responsable (GL/FP/Mixta)**")
            st.bar_chart(fdf.groupby("Resp")["Título"].count().reindex(["GL","FP","Mixta"]).fillna(0))

        c3, c4 = st.columns(2)
        with c3:
            st.markdown("**Casos por provincia**")
            st.bar_chart(fdf.groupby("Provincia")["Título"].count().sort_values(ascending=False))
        with c4:
            st.markdown("**Serie temporal (mensual)**")
            st.line_chart(fdf.groupby("Año-Mes")["Título"].count())

        st.divider(); st.markdown("**Tabla (filtrada)**")
        st.dataframe(fdf, use_container_width=True)

# ==================== 📤 EXPORTAR ====================
with tab_export:
    st.subheader("Exportar a ArcGIS (todas las capas)")
    fc = all_features_fc(); gdf = gdf_from_fc(fc)

    def export_geojson_bytes(fc: Dict[str, Any]) -> bytes: 
        return json.dumps(fc, ensure_ascii=False).encode("utf-8")

    def export_csv_bytes(gdf: gpd.GeoDataFrame) -> bytes:
        df = pd.DataFrame(gdf.drop(columns="geometry")); df["lat"] = gdf.geometry.y; df["lon"] = gdf.geometry.x
        return df.to_csv(index=False).encode("utf-8")

    def export_shapefile_zip_bytes(gdf: gpd.GeoDataFrame) -> bytes:
        with tempfile.TemporaryDirectory() as tmpd:
            shp = Path(tmpd) / "casos_exito.shp"
            gdf.to_file(shp, driver="ESRI Shapefile", encoding="utf-8")
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in Path(tmpd).glob("casos_exito.*"):
                    zf.write(p, arcname=p.name)
            zbuf.seek(0); return zbuf.getvalue()

    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("⬇️ GeoJSON", data=export_geojson_bytes(fc),
                           file_name=f"{st.session_state.project_name}.geojson", mime="application/geo+json",
                           disabled=(len(fc["features"]) == 0), key="dl_geojson")
    with c2:
        st.download_button("⬇️ Shapefile (ZIP)", data=export_shapefile_zip_bytes(gdf) if len(fc["features"]) else b"",
                           file_name=f"{st.session_state.project_name}.zip", mime="application/zip",
                           disabled=(len(fc["features"]) == 0), key="dl_shp")
    with c3:
        st.download_button("⬇️ CSV (atributos + lat/lon)", data=export_csv_bytes(gdf) if len(fc["features"]) else b"",
                           file_name=f"{st.session_state.project_name}.csv", mime="text/csv",
                           disabled=(len(fc["features"]) == 0), key="dl_csv")

# ==================== 📡 GOOGLE SHEETS ====================
with tab_sheets:
    st.subheader("Conexión a Google Sheets")
    if not HAS_SHEETS:
        st.warning("Instala dependencias y configura secrets para usar Google Sheets: `gspread` y `google-auth`. La app funciona sin Sheets.")
    else:
        try:
            sa_info = st.secrets["google_service_account"]
            creds = Credentials.from_service_account_info(sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(st.secrets["SHEETS_SPREADSHEET_ID"])
            ws = sh.worksheet(st.secrets.get("SHEETS_WORKSHEET_NAME", "Hoja 1"))
            st.success("✅ Conectado a Google Sheets.")
        except Exception as e:
            st.error(f"No se pudo conectar: {e}")
            ws = None

        st.caption("Estructura: id, layer, color, titulo, desc, fecha, provincia, canton, responsable, impacto, enlace, lat, lon")
        c1, c2 = st.columns(2)

        def rows_from_app() -> List[List[Any]]:
            fc = all_features_fc()
            rows = [["id","layer","color","titulo","desc","fecha","provincia","canton","responsable","impacto","enlace","lat","lon"]]
            for f in fc["features"]:
                p = f["properties"]; lon, lat = f["geometry"]["coordinates"]
                rows.append([p.get("id",""), p.get("layer",""), p.get("color",""), p.get("titulo",""), p.get("desc",""),
                             p.get("fecha",""), p.get("provincia",""), p.get("canton",""), p.get("responsable",""),
                             p.get("impacto",""), p.get("enlace",""), lat, lon])
            return rows

        def app_from_rows(rows: List[List[Any]]):
            layers = {}
            for r in rows[1:]:
                if not r or len(r) < 13: continue
                _id, layer, color, titulo, desc, fecha, provincia, canton, resp, impacto, enlace, lat, lon = r
                color = _clean_hex(color or "#1f77b4")
                feat = {
                    "type":"Feature",
                    "properties":{
                        "id": _id or uuid.uuid4().hex[:12], "layer": layer, "color": color, "titulo": str(titulo or ""),
                        "desc": str(desc or ""), "fecha": str(fecha or ""), "provincia": str(provincia or ""),
                        "canton": str(canton or ""), "responsable": str(resp or ""), "impacto": str(impacto or ""),
                        "enlace": str(enlace or "")
                    },
                    "geometry":{"type":"Point","coordinates":[float(lon), float(lat)]}
                }
                if layer not in layers:
                    layers[layer] = {"color": color, "visible": True, "features": []}
                layers[layer]["features"].append(feat)
                if "color" not in layers[layer] or not layers[layer]["color"]:
                    layers[layer]["color"] = color
            st.session_state.layers = layers

        with c1:
            if ws and st.button("⬇️ Cargar desde Google Sheets", key="load_sheets"):
                try:
                    values = ws.get_all_values()
                    if values:
                        app_from_rows(values)
                        st.success("Datos cargados desde Sheets a la app.")
                        st.experimental_rerun()
                    else:
                        st.info("La hoja está vacía.")
                except Exception as e:
                    st.error(f"Error al leer: {e}")

        with c2:
            if ws and st.button("⬆️ Subir (reemplaza en Sheets)", key="save_sheets"):
                try:
                    rows = rows_from_app()
                    ws.clear()
                    ws.update("A1", rows)
                    st.success("Datos subidos. (Se reemplazó el contenido de la hoja)")
                except Exception as e:
                    st.error(f"Error al escribir: {e}")


# -*- coding: utf-8 -*-
# Casos de Éxito — Mapas CR (Capas, Edición/Mover, Heatmap, Dashboard, Export, Google Sheets)
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
from folium.plugins import HeatMap, MeasureControl, MiniMap, BeautifyIcon

# ==================== Catálogo Costa Rica (Provincia → Cantón) ====================
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
st.caption("Clic en el mapa → **Confirmar** para agregar. Edita, mueve, filtra por Capa/Provincia/Cantón, Heatmap, Dashboard, Export y Google Sheets.")

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
if "last_click" not in st.session_state:
    st.session_state.last_click: Optional[tuple] = None  # (lat, lon)
if "last_added_from_click" not in st.session_state:
    st.session_state.last_added_from_click: Optional[tuple] = None
if "auto_sync" not in st.session_state:
    st.session_state.auto_sync = False

def _hex_ok(h): 
    import re as _re
    return bool(_re.fullmatch(r"#?[0-9a-fA-F]{6}", (h or "").strip()))
def _clean_hex(h):
    h = (h or "#1f77b4").strip()
    if not h.startswith("#"): h = "#" + h
    return h if _hex_ok(h) else "#1f77b4"

# ==================== Helpers Google Sheets reutilizables ====================
def _get_sa_info_from_secrets() -> Optional[dict]:
    if "google_service_account" in st.secrets:
        return st.secrets["google_service_account"]
    if "gcp_service_account" in st.secrets:
        return st.secrets["gcp_service_account"]
    return None

def get_ws():
    """Devuelve el worksheet (hoja) listo para usar o lanza una excepción con mensaje claro."""
    from google.oauth2.service_account import Credentials
    import gspread

    sa_info = _get_sa_info_from_secrets()
    if not sa_info:
        raise RuntimeError("No encuentro [google_service_account] ni [gcp_service_account] en secrets.toml")
    spreadsheet_id = st.secrets.get("SHEETS_SPREADSHEET_ID", "").strip()
    worksheet_name = st.secrets.get("SHEETS_WORKSHEET_NAME", "Hoja 1").strip()
    if not spreadsheet_id:
        raise RuntimeError("Falta SHEETS_SPREADSHEET_ID en secrets.toml")
    creds = Credentials.from_service_account_info(sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows="100", cols="20")
    # Crea encabezado si la hoja está vacía
    header = ["id","layer","color","titulo","desc","fecha","provincia","canton","responsable","impacto","enlace","lat","lon"]
    values_now = ws.get_all_values()
    if len(values_now) == 0:
        ws.update("A1", [header])
    return ws

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

def _same_click(a: Optional[tuple], b: Optional[tuple], tol=1e-7) -> bool:
    if not a or not b: return False
    return (abs(a[0]-b[0]) < tol) and (abs(a[1]-b[1]) < tol)

# ==================== 🗺️ MAPA ====================
with tab_mapa:
    st.subheader("Filtros de visualización")

    # Selectores cascada (oficiales)
    provs_catalog = ["(todas)"] + list(CR_CATALOG.keys())
    provincia_sel = st.selectbox("Provincia", provs_catalog, index=0, key="prov_map")
    cantones_catalog = ["(todos)"] + (CR_CATALOG.get(provincia_sel, []) if provincia_sel != "(todas)" else sorted({c for cs in CR_CATALOG.values() for c in cs}))
    canton_sel = st.selectbox("Cantón", cantones_catalog, index=0, key="canton_map")

    st.subheader("📝 Ficha del caso (se aplica al próximo punto que marques)")
    c1, c2, c3, c4 = st.columns([1,1,1,1])
    with c1: layer_active = st.selectbox("Capa activa", list(st.session_state.layers.keys()), key="layer_active_map")
    with c2: titulo = st.text_input("Título", "Parque recuperado y seguro", key="titulo_map")
    with c3: fecha = st.date_input("Fecha", key="fecha_map")
    with c4: responsable = st.selectbox("Responsable", ["GL", "FP", "Mixta"], key="resp_map")
    desc = st.text_area("Descripción (máx. 240)", "Rehabilitación de iluminación y mobiliario; patrullajes comunitarios.", key="desc_map")[:240]

    # Provincia/Cantón en la ficha, sincronizados con los selects superiores
    d2a, d2b, d2c = st.columns([1,1,1])
    with d2a: st.text_input("Provincia (auto)", value=("San José" if provincia_sel=="(todas)" else provincia_sel), key="prov_in_form_map", disabled=True)
    cant_list_for_form = (CR_CATALOG.get(provincia_sel, []) if provincia_sel != "(todas)" else [])
    cant_default = (canton_sel if canton_sel != "(todos)" else (cant_list_for_form[0] if cant_list_for_form else ""))
    with d2b: st.text_input("Cantón (auto)", value=cant_default, key="canton_in_form_map", disabled=True)
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

    # Filtro función (para render)
    def pass_filter(props: Dict[str, Any]) -> bool:
        if provincia_sel != "(todas)" and props.get("provincia","") != provincia_sel: return False
        if canton_sel != "(todos)" and props.get("canton","") != canton_sel: return False
        return True

    # Dibujar puntos
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

    # Render del mapa y captura de clic
    map_state = st_folium(m, height=650, width=None, key="mapa_casos", feature_group_to_add=None)

    click = map_state.get("last_clicked") if map_state else None
    if click:
        st.session_state.last_click = (float(click["lat"]), float(click["lng"]))

    # Confirmación del clic
    colc1, colc2, colc3 = st.columns([1,1,1])
    with colc1:
        st.info(f"Último clic: {st.session_state.last_click}" if st.session_state.last_click else "Haz clic en el mapa para elegir ubicación.")
    with colc2:
        confirm_add = st.button("➕ Confirmar agregar punto aquí", key="btn_confirm_add",
                                disabled=(st.session_state.last_click is None or st.session_state.move_target is not None))
    with colc3:
        clear_click = st.button("🧹 Limpiar selección de clic", key="btn_clear_click", disabled=(st.session_state.last_click is None))

    if clear_click:
        st.session_state.last_click = None
        st.session_state.last_added_from_click = None
        st.rerun()

    # Agregar (con confirmación y anti-duplicados)
    if confirm_add and st.session_state.last_click:
        lat, lon = st.session_state.last_click
        if st.session_state.last_added_from_click != (lat, lon):  # anti-duplicado
            provincia_to_save = ("San José" if provincia_sel=="(todas)" else provincia_sel)
            canton_to_save = (cant_default if provincia_sel!="(todas)" else "")
            props = {
                "id": _new_id(),
                "layer": layer_active,
                "color": _clean_hex(st.session_state.layers[layer_active]["color"]),
                "titulo": titulo.strip(),
                "desc": desc.strip(),
                "fecha": str(fecha),
                "provincia": provincia_to_save,
                "canton": canton_to_save,
                "responsable": responsable,
                "impacto": impacto.strip(),
                "enlace": enlace.strip()
            }
            st.session_state.layers[layer_active]["features"].append(_build_feature(lon, lat, props))
            st.session_state.last_added_from_click = (lat, lon)
            st.toast(f"Punto agregado en {provincia_to_save} / {canton_to_save} ({lat:.5f}, {lon:.5f})", icon="✅")

            # Auto-sync a Sheets (append)
            if st.session_state.auto_sync:
                try:
                    ws = get_ws()
                    ws.append_row([
                        props["id"], props["layer"], props["color"], props["titulo"], props["desc"],
                        props["fecha"], props["provincia"], props["canton"], props["responsable"],
                        props["impacto"], props["enlace"], lat, lon
                    ])
                    st.toast("Sincronizado en Sheets (append).", icon="🟢")
                except Exception as e:
                    st.toast(f"No se pudo auto-sincronizar: {e}", icon="🟠")

# ==================== 📋 Gestión (eliminar / editar / mover) ====================
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
                if provincia_sel != "(todas)": df_layer = df_layer[df_layer["Provincia"] == provincia_sel]
                if canton_sel != "(todos)":   df_layer = df_layer[df_layer["Cantón"] == canton_sel]
                st.dataframe(df_layer, use_container_width=True)

                colx, coly, colz = st.columns([1,1,1])
                with colx:
                    idx_del = st.number_input("Eliminar (índice)", min_value=0, max_value=len(feats)-1, value=0, step=1, key=f"idx_del_{lname}")
                    if st.button("🗑️ Eliminar", key=f"btn_del_{lname}"):
                        st.session_state.layers[lname]["features"].pop(int(idx_del)); st.rerun()

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
                        provs = list(CR_CATALOG.keys())
                        prov_idx = provs.index(p.get("provincia","San José")) if p.get("provincia","San José") in provs else 0
                        prov = st.selectbox("Provincia (catálogo)", provs, index=prov_idx, key=f"prov_edit_{lname}")
                        cant_list = CR_CATALOG.get(prov, [])
                        cant_val = p.get("canton", cant_list[0] if cant_list else "")
                        cant_idx = cant_list.index(cant_val) if cant_val in cant_list else 0
                        cant = st.selectbox("Cantón (catálogo)", cant_list, index=cant_idx, key=f"cant_edit_{lname}")
                        imp  = st.text_input("Impacto", p.get("impacto",""), key=f"imp_edit_{lname}")
                        enl  = st.text_input("Enlace", p.get("enlace",""), key=f"enl_edit_{lname}")
                        des  = st.text_area("Descripción", p.get("desc",""), key=f"desc_edit_{lname}")
                        submitted = st.form_submit_button("💾 Guardar")
                    if submitted:
                        p.update({"titulo": t.strip(), "fecha": str(fe), "responsable": resp,
                                  "provincia": prov.strip(), "canton": cant.strip(),
                                  "impacto": imp.strip(), "enlace": enl.strip(), "desc": des.strip()})
                        st.success("Caso actualizado."); st.rerun()

                with colz:
                    st.markdown("**Mover ubicación**")
                    idx_move = st.number_input("Índice", min_value=0, max_value=len(feats)-1, value=0, step=1, key=f"idx_move_{lname}")
                    if st.button("🔀 Activar mover por clic", key=f"btn_move_{lname}"):
                        st.session_state.move_target = (lname, int(idx_move)); st.info("Haz clic en el mapa para mover.")
                    if st.button("❌ Cancelar", key=f"btn_cancel_move_{lname}"):
                        st.session_state.move_target = None; st.rerun()

    # Aplicar movimiento por clic
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
    try:
        ws = get_ws()
        st.success(f"✅ Conectado a Sheets • Hoja: {st.secrets.get('SHEETS_WORKSHEET_NAME','Hoja 1')}")
        # Conteo y encabezado
        values_now = ws.get_all_values()
        st.info(f"📄 Filas actuales en la hoja: {len(values_now)}")
        HEADER = ["id","layer","color","titulo","desc","fecha","provincia","canton","responsable","impacto","enlace","lat","lon"]
        if len(values_now) == 0:
            ws.update("A1", [HEADER])
            st.success("Encabezado creado (la hoja estaba vacía).")

        def rows_from_app() -> List[List[Any]]:
            fc = all_features_fc()
            rows = [HEADER]
            for f in fc["features"]:
                p = f["properties"]; lon, lat = f["geometry"]["coordinates"]
                rows.append([p.get("id",""), p.get("layer",""), p.get("color",""), p.get("titulo",""), p.get("desc",""),
                             p.get("fecha",""), p.get("provincia",""), p.get("canton",""), p.get("responsable",""),
                             p.get("impacto",""), p.get("enlace",""), lat, lon])
            return rows

        def app_from_rows(values: List[List[Any]]):
            layers = {}
            for r in values[1:]:
                if not r or len(r) < 13: continue
                _id, layer, color, titulo, desc, fecha, provincia, canton, resp, impacto, enlace, lat, lon = r
                color = _clean_hex(color or "#1f77b4")
                feat = {
                    "type":"Feature",
                    "properties":{
                        "id": _id or _new_id(), "layer": layer, "color": color, "titulo": str(titulo or ""),
                        "desc": str(desc or ""), "fecha": str(fecha or ""), "provincia": str(provincia or ""),
                        "canton": str(canton or ""), "responsable": str(resp or ""), "impacto": str(impacto or ""),
                        "enlace": str(enlace or "")
                    },
                    "geometry":{"type":"Point","coordinates":[float(lon), float(lat)]}
                }
                if layer not in layers:
                    layers[layer] = {"color": color, "visible": True, "features": []}
                layers[layer]["features"].append(feat)
            st.session_state.layers = layers

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("🔎 Probar conexión", key="probe_sheets"):
                st.success(f"Conexión OK. Filas: {len(ws.get_all_values())}")
        with c2:
            if st.button("⬇️ Cargar desde Sheets", key="load_sheets"):
                values = ws.get_all_values()
                if values and len(values) > 1:
                    app_from_rows(values)
                    st.success(f"Cargados {len(values)-1} casos desde Sheets.")
                    st.experimental_rerun()
                else:
                    st.info("La hoja sólo tiene encabezado o está vacía.")
        with c3:
            if st.button("⬆️ Subir (reemplazar hoja)", key="save_sheets"):
                rows = rows_from_app()
                ws.clear()
                if rows:
                    ws.update("A1", rows)
                    st.success(f"Subidos {max(len(rows)-1,0)} casos (encabezado incluido).")
                else:
                    ws.update("A1", [HEADER])
                    st.info("No había casos; se dejó sólo el encabezado.")
        with c4:
            st.session_state.auto_sync = st.toggle("🔁 Auto-sincronizar al agregar", value=st.session_state.auto_sync, key="auto_sync_toggle")
            st.caption("Si está activo, cada nuevo punto se agrega también a Sheets (append).")

    except ModuleNotFoundError as e:
        st.error(f"Faltan dependencias: {e}. Agrega 'google-auth' y 'gspread' a requirements.txt y reinicia.")
    except Exception as e:
        st.error(f"❌ No se pudo conectar a Google Sheets: {e}")
        st.info("Revisa:\n• secrets.toml (IDs y bloque del service account)\n• que la hoja esté compartida con el correo del service account (Editor)\n• que la clave tenga \\n en cada salto de línea.")

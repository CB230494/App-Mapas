# -*- coding: utf-8 -*-
# Casos de Éxito – Mapas CR + Sheets persistente (auto-carga y auto-guardado)
# Ejecuta: streamlit run app.py

import io, json, zipfile, tempfile, uuid, re, datetime as dt, os
from pathlib import Path
from typing import Dict, List, Any, Optional

import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

import folium
from streamlit_folium import st_folium
from folium.plugins import HeatMap, MeasureControl, MiniMap, BeautifyIcon

# --- Google Sheets robusto (basado en la otra app) ---
import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

# ============== Catálogo CR ==============
CR_CATALOG: Dict[str, List[str]] = {
    "San José": ["San José","Escazú","Desamparados","Puriscal","Tarrazú","Aserrí","Mora",
                 "Goicoechea","Santa Ana","Alajuelita","Vázquez de Coronado","Acosta",
                 "Tibás","Moravia","Montes de Oca","Turrubares","Dota","Curridabat",
                 "Pérez Zeledón","León Cortés"],
    "Alajuela": ["Alajuela","San Ramón","Grecia","San Mateo","Atenas","Naranjo","Palmares",
                 "Poás","Orotina","San Carlos","Zarcero","Sarchí","Upala","Los Chiles","Guatuso"],
    "Cartago": ["Cartago","Paraíso","La Unión","Jiménez","Turrialba","Alvarado","Oreamuno","El Guarco"],
    "Heredia": ["Heredia","Barva","Santo Domingo","Santa Bárbara","San Rafael","San Isidro",
                "Belén","Flores","San Pablo","Sarapiquí"],
    "Guanacaste": ["Liberia","Nicoya","Santa Cruz","Bagaces","Carrillo","Cañas","Abangares",
                   "Tilarán","Nandayure","La Cruz","Hojancha"],
    "Puntarenas": ["Puntarenas","Esparza","Buenos Aires","Montes de Oro","Osa","Quepos","Golfito",
                   "Coto Brus","Parrita","Corredores","Garabito"],
    "Limón": ["Limón","Pococí","Siquirres","Talamanca","Matina","Guácimo"],
}

# ============== Config ==============
st.set_page_config(page_title="Casos de Éxito – Mapas CR", layout="wide")
st.title("🌟 Casos de Éxito – Mapas por capas (CR)")
st.caption("Agrega puntos con clic + Confirmar. Edita, mueve, borra. Heatmap, dashboard y export. **Sheets es el almacenamiento** (auto-carga y auto-guardado).")

# ========= Estado base =========
if "project_name" not in st.session_state:
    st.session_state.project_name = "casos_exito"
if "layers" not in st.session_state:
    st.session_state.layers: Dict[str, Dict[str, Any]] = {
        "Infraestructura recuperada": {"color": "#2ca02c", "visible": True, "features": []},
        "Prevención comunitaria":     {"color": "#1f77b4", "visible": True, "features": []},
        "Operativos y control":       {"color": "#ff7f0e", "visible": True, "features": []},
        "Gestión interinstitucional": {"color": "#9467bd", "visible": True, "features": []},
    }
if "move_target" not in st.session_state:
    st.session_state.move_target: Optional[tuple] = None
if "last_click" not in st.session_state:
    st.session_state.last_click: Optional[tuple] = None

# ========= Helpers comunes =========
def _hex_ok(h): 
    return bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", (h or "").strip()))
def _clean_hex(h):
    h = (h or "#1f77b4").strip()
    if not h.startswith("#"): h = "#" + h
    return h if _hex_ok(h) else "#1f77b4"
def _new_id() -> str:
    return uuid.uuid4().hex[:12]

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
    for meta in st.session_state.layers.values():
        feats.extend(meta.get("features", []))
    return {"type":"FeatureCollection","features":feats}

def gdf_from_fc(fc: Dict[str, Any]) -> gpd.GeoDataFrame:
    feats = fc["features"]
    if not feats:
        return gpd.GeoDataFrame(
            columns=["id","layer","color","titulo","desc","fecha",
                     "provincia","canton","responsable","impacto","enlace","geometry"],
            geometry="geometry", crs="EPSG:4326"
        )
    rows = []
    for f in feats:
        p = f["properties"]; lon, lat = f["geometry"]["coordinates"]
        rows.append({**p, "geometry": Point(lon, lat)})
    return gpd.GeoDataFrame(rows, crs="EPSG:4326")

# ========= Google Sheets (persistencia) =========
# Esquema de esta app
HEADER = ["id","layer","color","titulo","desc","fecha",
          "provincia","canton","responsable","impacto",
          "enlace","lat","lon"]

# ---- Cliente robusto (tomado de la otra app y adaptado) ----
@st.cache_resource(show_spinner=False)
def _get_gs_client_or_none():
    """
    Intenta autorizar un cliente de Google Sheets usando:
    - st.secrets["google_service_account"]  o
    - st.secrets["gcp_service_account"]
    """
    try:
        if "google_service_account" in st.secrets:
            sa_info = dict(st.secrets["google_service_account"])
        elif "gcp_service_account" in st.secrets:
            sa_info = dict(st.secrets["gcp_service_account"])
        else:
            st.warning("No se encontró [google_service_account] ni [gcp_service_account] en secrets.toml")
            return None

        creds = Credentials.from_service_account_info(
            sa_info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        return gspread.authorize(creds)
    except Exception as e:
        st.warning(f"No se pudo autorizar Google Sheets. Modo sin escritura. Detalle: {e}")
        return None

def ws_connect():
    """
    Abre o crea la hoja, garantizando encabezados == HEADER.
    Usa:
    - st.secrets["SHEETS_SPREADSHEET_ID"] o os.getenv("SHEET_ID")
    - st.secrets["SHEETS_WORKSHEET_NAME"] o os.getenv("WS_NAME", 'casos_exito')
    """
    gc = _get_gs_client_or_none()
    if gc is None:
        raise RuntimeError("Sin cliente de Google Sheets (revisa secrets).")

    sheet_id = (st.secrets.get("SHEETS_SPREADSHEET_ID","") or
                os.getenv("SHEET_ID","")).strip()
    if not sheet_id:
        raise RuntimeError("Falta SHEETS_SPREADSHEET_ID en secrets.toml o SHEET_ID en variables de entorno.")

    wsname = (st.secrets.get("SHEETS_WORKSHEET_NAME","") or
              os.getenv("WS_NAME","casos_exito")).strip()

    try:
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(wsname)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=wsname, rows=1000, cols=len(HEADER))
            ws.append_row(HEADER)

        # Asegurar encabezado correcto
        hdr = [h.strip().lower() for h in ws.row_values(1)]
        if hdr != [h.lower() for h in HEADER]:
            ws.resize(rows=max(2, ws.row_count), cols=len(HEADER))
            # Actualiza solo la primera fila
            last_col = chr(ord("A") + len(HEADER) - 1)
            ws.update(f"A1:{last_col}1", [HEADER])

        return ws
    except APIError as e:
        raise RuntimeError(f"No se puede acceder a la Hoja (permiso/ID). {e}")
    except Exception as e:
        raise RuntimeError(f"Error al abrir la Hoja: {e}")

def load_layers_from_ws(ws):
    values = ws.get_all_values()
    if not values or len(values) <= 1:
        return False
    layers: Dict[str, Dict[str, Any]] = {}
    # Ajuste: asegurar fila tiene al menos tantas columnas como HEADER
    for r in values[1:]:
        if not r or len(r) < len(HEADER):
            continue
        (_id, layer, color, titulo, desc, fecha,
         provincia, canton, resp, impacto, enlace, lat, lon) = r[:len(HEADER)]

        color = _clean_hex(color or "#1f77b4")
        feat = {
            "type": "Feature",
            "properties": {
                "id": _id or _new_id(),
                "layer": layer,
                "color": color,
                "titulo": str(titulo or ""),
                "desc": str(desc or ""),
                "fecha": str(fecha or ""),
                "provincia": str(provincia or ""),
                "canton": str(canton or ""),
                "responsable": str(resp or ""),
                "impacto": str(impacto or ""),
                "enlace": str(enlace or "")
            },
            "geometry": {
                "type": "Point",
                "coordinates": [float(lon), float(lat)]
            }
        }
        if layer not in layers:
            layers[layer] = {"color": color, "visible": True, "features": []}
        layers[layer]["features"].append(feat)
    st.session_state.layers = layers
    return True

def rows_from_layers() -> List[List[Any]]:
    rows = [HEADER]
    for meta in st.session_state.layers.values():
        for f in meta.get("features", []):
            p = f["properties"]; lon, lat = f["geometry"]["coordinates"]
            rows.append([
                p.get("id",""),
                p.get("layer",""),
                p.get("color",""),
                p.get("titulo",""),
                p.get("desc",""),
                p.get("fecha",""),
                p.get("provincia",""),
                p.get("canton",""),
                p.get("responsable",""),
                p.get("impacto",""),
                p.get("enlace",""),
                lat, lon
            ])
    return rows

def save_layers_to_ws(ws):
    rows = rows_from_layers()
    ws.clear()
    # Asegurar que se escribe desde A1 la matriz completa
    last_col = chr(ord("A") + len(HEADER) - 1)
    ws.update(f"A1:{last_col}{len(rows)}", rows)

# ---- Bootstrap persistente (al cargar la app) ----
_sheets_ok = False
ws0 = None
try:
    ws0 = ws_connect()
    _sheets_ok = True
    loaded = load_layers_from_ws(ws0)  # si había datos, los carga
except ModuleNotFoundError as e:
    st.warning(f"Faltan dependencias para Google Sheets ({e}). Instala 'google-auth' y 'gspread'.")
except Exception as e:
    st.warning(f"No se pudo preparar Google Sheets: {e}")

# ========= Sidebar =========
st.sidebar.header("Proyecto")
st.session_state.project_name = st.sidebar.text_input(
    "Nombre del proyecto",
    st.session_state.project_name,
    key="proj_name"
)

st.sidebar.header("Mapa base")
BASEMAPS = {
    "OSM Estándar": {
        "tiles": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": '&copy; OpenStreetMap contributors'
    },
    "Carto Claro": {
        "tiles": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attr": '&copy; CARTO & OSM'
    },
    "Esri Satélite": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": 'Tiles © Esri'
    },
    "Esri Gray (Light)": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "attr": 'Tiles © Esri'
    },
}
basemap_name = st.sidebar.selectbox(
    "Elegir mapa base",
    list(BASEMAPS.keys()),
    index=0,
    key="basemap"
)

st.sidebar.header("Capas")
for lname, meta in list(st.session_state.layers.items()):
    with st.sidebar.expander(lname, expanded=False):
        meta["visible"] = st.checkbox(
            "Visible",
            value=meta.get("visible", True),
            key=f"vis_{lname}"
        )
        meta["color"] = _clean_hex(
            st.color_picker(
                "Color",
                value=meta.get("color", "#1f77b4"),
                key=f"col_{lname}"
            )
        )
        if st.button("Eliminar capa", key=f"del_layer_{lname}"):
            del st.session_state.layers[lname]
            if _sheets_ok:
                try:
                    save_layers_to_ws(ws0)
                except Exception as e:
                    st.toast(f"No se pudo guardar en Sheets: {e}", icon="🟠")
            st.rerun()

with st.sidebar.expander("➕ Agregar capa"):
    new_name = st.text_input("Nombre nueva capa", "", key="new_layer")
    new_color = st.color_picker("Color", "#17becf", key="new_layer_color")
    if st.button("Crear capa", key="btn_new_layer"):
        if new_name and new_name not in st.session_state.layers:
            st.session_state.layers[new_name] = {
                "color": _clean_hex(new_color),
                "visible": True,
                "features": []
            }
            if _sheets_ok:
                try:
                    save_layers_to_ws(ws0)
                except Exception as e:
                    st.toast(f"No se pudo guardar en Sheets: {e}", icon="🟠")
            st.rerun()

# ========= Tabs =========
tab_mapa, tab_dashboard, tab_export, tab_sheets = st.tabs(
    ["🗺️ Mapa", "📊 Dashboard", "📤 Exportar", "📡 Google Sheets"]
)

# ========= 🗺️ MAPA =========
with tab_mapa:
    st.subheader("Filtros")
    provs = ["(todas)"] + list(CR_CATALOG.keys())
    provincia_sel = st.selectbox("Provincia", provs, index=0, key="prov_map")
    cantones = ["(todos)"] + (
        CR_CATALOG.get(provincia_sel, [])
        if provincia_sel != "(todas)"
        else sorted({c for v in CR_CATALOG.values() for c in v})
    )
    canton_sel = st.selectbox("Cantón", cantones, index=0, key="canton_map")

    st.subheader("📝 Ficha del caso para el próximo punto")
    c1, c2, c3, c4 = st.columns([1,1,1,1])
    with c1:
        layer_active = st.selectbox(
            "Capa activa",
            list(st.session_state.layers.keys()),
            key="layer_active"
        )
    with c2:
        titulo = st.text_input("Título", "Parque recuperado y seguro", key="titulo_map")
    with c3:
        fecha = st.date_input("Fecha", key="fecha_map")
    with c4:
        responsable = st.selectbox("Responsable", ["GL","FP","Mixta"], key="resp_map")
    desc = st.text_area(
        "Descripción (máx. 240)",
        "Rehabilitación de iluminación y mobiliario; patrullajes.",
        key="desc_map"
    )[:240]

    d2a, d2b, d2c = st.columns([1,1,1])
    prov_guardar = "San José" if provincia_sel == "(todas)" else provincia_sel
    cant_list = CR_CATALOG.get(prov_guardar, [])
    cant_guardar = (canton_sel if canton_sel != "(todos)"
                    else (cant_list[0] if cant_list else ""))
    with d2a:
        st.text_input("Provincia (auto)", value=prov_guardar, key="prov_auto", disabled=True)
    with d2b:
        st.text_input("Cantón (auto)", value=cant_guardar, key="cant_auto", disabled=True)
    with d2c:
        impacto = st.text_input("Impacto (opcional)", "↓ 35% incidentes en 3 meses", key="impacto_map")
    enlace = st.text_input("Enlace a evidencia (opcional)", "", key="enlace_map")

    # ----- Mapa -----
    m = folium.Map(location=[9.94, -84.10], zoom_start=7, control_scale=True)
    bm = BASEMAPS[basemap_name]
    folium.TileLayer(
        tiles=bm["tiles"],
        name=basemap_name,
        attr=bm["attr"],
        control=False
    ).add_to(m)
    for nm, cfg in BASEMAPS.items():
        if nm != basemap_name:
            folium.TileLayer(
                tiles=cfg["tiles"],
                name=nm,
                attr=cfg["attr"],
                control=True
            ).add_to(m)
    folium.plugins.Fullscreen(position="topleft").add_to(m)
    m.add_child(MiniMap(toggle_display=True))
    m.add_child(MeasureControl(primary_length_unit="meters",
                               secondary_length_unit="kilometers"))
    folium.LatLngPopup().add_to(m)

    def pass_filter(p: Dict[str, Any]) -> bool:
        if provincia_sel != "(todas)" and p.get("provincia") != provincia_sel:
            return False
        if canton_sel != "(todos)" and p.get("canton") != canton_sel:
            return False
        return True

    heat_points = []
    for lname, meta in st.session_state.layers.items():
        if not meta.get("visible", True):
            continue
        fg = folium.FeatureGroup(name=lname, show=True)
        color = _clean_hex(meta["color"])
        for f in meta.get("features", []):
            p = f["properties"]
            if not pass_filter(p):
                continue
            lon, lat = f["geometry"]["coordinates"]; lat, lon = float(lat), float(lon)
            icon = BeautifyIcon(
                icon="circle",
                icon_shape="marker",
                text_color="white",
                background_color=color,
                border_color=color,
                spin=False
            )
            html = f"""<b>{p.get('titulo','(sin título)')}</b><br>
            <i>{p.get('fecha','')}</i><br>
            <b>Resp:</b> {p.get('responsable','')} · <b>Capa:</b> {p.get('layer','')}<br>
            <b>Prov/Cantón:</b> {p.get('provincia','')}/{p.get('canton','')}<br>
            <b>Impacto:</b> {p.get('impacto','')}<br>
            <a target="_blank" href="{p.get('enlace','')}">Evidencia</a><hr>{p.get('desc','')}"""
            folium.Marker(
                [lat, lon],
                icon=icon,
                tooltip=p.get('titulo','(Caso)'),
                popup=folium.Popup(html, max_width=320)
            ).add_to(fg)
            heat_points.append([lat, lon, 1])
        fg.add_to(m)

    show_heat = st.checkbox("🔥 Heatmap", value=False, key="heat")
    if show_heat and heat_points:
        HeatMap(
            heat_points,
            radius=25,
            blur=25,
            min_opacity=0.3,
            name="Heatmap"
        ).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    state = st_folium(m, height=640, key="mapa_main")
    click = state.get("last_clicked") if state else None
    if click:
        st.session_state.last_click = (float(click["lat"]), float(click["lng"]))

    cA, cB, cC = st.columns(3)
    with cA:
        st.info(
            "Haz clic en el mapa para elegir ubicación."
            if not st.session_state.last_click
            else f"Último clic: {st.session_state.last_click}"
        )
    with cB:
        add_ok = st.button(
            "➕ Confirmar agregar punto aquí",
            key="btn_add",
            disabled=(st.session_state.last_click is None)
        )
    with cC:
        if st.button(
            "🧹 Limpiar selección de clic",
            key="btn_clear",
            disabled=(st.session_state.last_click is None)
        ):
            st.session_state.last_click = None
            st.rerun()

    # Agregar y guardar a Sheets
    if add_ok and st.session_state.last_click:
        lat, lon = st.session_state.last_click
        props = {
            "id": _new_id(),
            "layer": layer_active,
            "color": _clean_hex(st.session_state.layers[layer_active]["color"]),
            "titulo": titulo.strip(),
            "desc": desc.strip(),
            "fecha": str(fecha),
            "provincia": prov_guardar,
            "canton": cant_guardar,
            "responsable": responsable,
            "impacto": impacto.strip(),
            "enlace": enlace.strip()
        }
        st.session_state.layers[layer_active]["features"].append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)]
                }
            }
        )
        st.toast("Punto agregado.", icon="✅")
        if _sheets_ok:
            try:
                save_layers_to_ws(ws0)
            except Exception as e:
                st.toast(f"No se pudo guardar en Sheets: {e}", icon="🟠")
        st.session_state.last_click = None
        st.rerun()

    # ------- Gestión por capa (eliminar/editar/mover) -------
    st.divider()
    st.subheader("📋 Gestión por capas (eliminar / **editar / mover**)")
    subtabs = st.tabs(list(st.session_state.layers.keys()))
    for i, lname in enumerate(list(st.session_state.layers.keys())):
        with subtabs[i]:
            feats = st.session_state.layers[lname]["features"]
            if not feats:
                st.info("Sin casos aún.")
                continue
            df_layer = pd.DataFrame([feature_to_row(f) for f in feats])
            if provincia_sel != "(todas)":
                df_layer = df_layer[df_layer["Provincia"] == provincia_sel]
            if canton_sel != "(todos)":
                df_layer = df_layer[df_layer["Cantón"] == canton_sel]
            st.dataframe(df_layer, use_container_width=True)

            c1, c2, c3 = st.columns(3)
            with c1:
                idx_del = st.number_input(
                    "Eliminar (índice)",
                    0, len(feats)-1, 0, 1,
                    key=f"idx_del_{lname}"
                )
                if st.button("🗑️ Eliminar", key=f"btn_del_{lname}"):
                    st.session_state.layers[lname]["features"].pop(int(idx_del))
                    if _sheets_ok:
                        try:
                            save_layers_to_ws(ws0)
                        except Exception as e:
                            st.toast(f"No se pudo guardar en Sheets: {e}", icon="🟠")
                    st.rerun()
            with c2:
                st.markdown("**Editar atributos**")
                ied = st.number_input(
                    "Índice",
                    0, len(feats)-1, 0, 1,
                    key=f"idx_edit_{lname}"
                )
                f = feats[int(ied)]; p = f["properties"]
                with st.form(f"form_edit_{lname}"):
                    t = st.text_input("Título", p.get("titulo",""), key=f"t_{lname}")
                    fe = st.date_input(
                        "Fecha",
                        value=pd.to_datetime(
                            p.get("fecha", dt.date.today())
                        ).date(),
                        key=f"fe_{lname}"
                    )
                    resp = st.selectbox(
                        "Responsable", ["GL","FP","Mixta"],
                        index=["GL","FP","Mixta"].index(p.get("responsable","GL")),
                        key=f"resp_{lname}"
                    )
                    provs_edit = list(CR_CATALOG.keys())
                    prov_idx = (provs_edit.index(p.get("provincia","San José"))
                                if p.get("provincia","San José") in provs_edit
                                else 0)
                    prov = st.selectbox("Provincia", provs_edit, index=prov_idx, key=f"prov_{lname}")
                    clist = CR_CATALOG.get(prov, [])
                    cval = p.get("canton", clist[0] if clist else "")
                    cint = clist.index(cval) if cval in clist else 0
                    cant = st.selectbox("Cantón", clist, index=cint, key=f"cant_{lname}")
                    imp = st.text_input("Impacto", p.get("impacto",""), key=f"imp_{lname}")
                    enl = st.text_input("Enlace", p.get("enlace",""), key=f"enl_{lname}")
                    des = st.text_area("Descripción", p.get("desc",""), key=f"des_{lname}")
                    ok = st.form_submit_button("💾 Guardar")
                if ok:
                    p.update({
                        "titulo": t.strip(),
                        "fecha": str(fe),
                        "responsable": resp,
                        "provincia": prov,
                        "canton": cant,
                        "impacto": imp.strip(),
                        "enlace": enl.strip(),
                        "desc": des.strip()
                    })
                    if _sheets_ok:
                        try:
                            save_layers_to_ws(ws0)
                        except Exception as e:
                            st.toast(f"No se pudo guardar en Sheets: {e}", icon="🟠")
                    st.success("Actualizado.")
                    st.rerun()
            with c3:
                st.markdown("**Mover ubicación**")
                imv = st.number_input(
                    "Índice",
                    0, len(feats)-1, 0, 1,
                    key=f"idx_move_{lname}"
                )
                if st.button("🔀 Activar mover por clic", key=f"btn_move_{lname}"):
                    st.session_state.move_target = (lname, int(imv))
                    st.info("Haz clic en el mapa para mover.")
                if st.button("❌ Cancelar", key=f"btn_cancel_{lname}"):
                    st.session_state.move_target = None
                    st.rerun()

    # Aplicar movimiento si hay target y un clic nuevo
    if st.session_state.move_target and state and state.get("last_clicked"):
        lat = state["last_clicked"]["lat"]; lon = state["last_clicked"]["lng"]
        lname, idx = st.session_state.move_target
        st.session_state.layers[lname]["features"][idx]["geometry"]["coordinates"] = [
            float(lon), float(lat)
        ]
        st.session_state.move_target = None
        if _sheets_ok:
            try:
                save_layers_to_ws(ws0)
            except Exception as e:
                st.toast(f"No se pudo guardar en Sheets: {e}", icon="🟠")
        st.success(f"Ubicación actualizada a ({lat:.5f}, {lon:.5f}).")
        st.rerun()

# ========= 📊 DASHBOARD =========
with tab_dashboard:
    df = pd.DataFrame([feature_to_row(f) for f in all_features_fc()["features"]])
    if df.empty:
        st.info("Aún no hay datos.")
    else:
        st.subheader("Filtros")
        capas = sorted(df["Capa"].unique().tolist())
        capa_f = st.multiselect("Capas", capas, default=capas, key="capas_dash")
        prov_f = st.selectbox("Provincia", ["(todas)"] + list(CR_CATALOG.keys()), 0, key="prov_dash")
        cant_f = st.selectbox(
            "Cantón",
            ["(todos)"] + (
                CR_CATALOG.get(prov_f, [])
                if prov_f != "(todas)"
                else sorted({c for v in CR_CATALOG.values() for c in v})
            ),
            0,
            key="cant_dash"
        )
        fdf = df[df["Capa"].isin(capa_f)].copy()
        if prov_f != "(todas)":
            fdf = fdf[fdf["Provincia"] == prov_f]
        if cant_f != "(todos)":
            fdf = fdf[fdf["Cantón"] == cant_f]
        fdf["Fecha"] = pd.to_datetime(fdf["Fecha"], errors="coerce")
        fdf["Año-Mes"] = fdf["Fecha"].dt.to_period("M").astype(str)

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Casos por capa**")
            st.bar_chart(
                fdf.groupby("Capa")["Título"].count().sort_values(ascending=False)
            )
        with c2:
            st.markdown("**GL/FP/Mixta**")
            st.bar_chart(
                fdf.groupby("Resp")["Título"].count()
            )
        c3, c4 = st.columns(2)
        with c3:
            st.markdown("**Por provincia**")
            st.bar_chart(
                fdf.groupby("Provincia")["Título"].count().sort_values(ascending=False)
            )
        with c4:
            st.markdown("**Serie mensual**")
            st.line_chart(
                fdf.groupby("Año-Mes")["Título"].count()
            )
        st.divider()
        st.markdown("**Tabla (filtrada)**")
        st.dataframe(fdf, use_container_width=True)

# ========= 📤 EXPORT =========
with tab_export:
    st.subheader("Exportar todo (todas las capas)")
    fc = all_features_fc()
    gdf = gdf_from_fc(fc)

    def _geojson_bytes():
        return json.dumps(fc, ensure_ascii=False).encode("utf-8")

    def _csv_bytes():
        if gdf.empty:
            return b""
        df = pd.DataFrame(gdf.drop(columns="geometry"))
        df["lat"] = gdf.geometry.y
        df["lon"] = gdf.geometry.x
        return df.to_csv(index=False).encode("utf-8")

    def _shp_zip():
        if gdf.empty:
            return b""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "casos_exito.shp"
            gdf.to_file(path, driver="ESRI Shapefile", encoding="utf-8")
            bio = io.BytesIO()
            with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in Path(td).glob("casos_exito.*"):
                    zf.write(p, arcname=p.name)
            bio.seek(0)
            return bio.getvalue()

    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button(
            "⬇️ GeoJSON",
            data=_geojson_bytes(),
            file_name=f"{st.session_state.project_name}.geojson",
            mime="application/geo+json",
            disabled=(len(fc['features']) == 0)
        )
    with c2:
        st.download_button(
            "⬇️ Shapefile (ZIP)",
            data=_shp_zip(),
            file_name=f"{st.session_state.project_name}.zip",
            mime="application/zip",
            disabled=(len(fc['features']) == 0)
        )
    with c3:
        st.download_button(
            "⬇️ CSV",
            data=_csv_bytes(),
            file_name=f"{st.session_state.project_name}.csv",
            mime="text/csv",
            disabled=(len(fc['features']) == 0)
        )

# ========= 📡 GOOGLE SHEETS (panel visible) =========
with tab_sheets:
    st.subheader("Estado de conexión")
    if _sheets_ok and ws0 is not None:
        try:
            ws = ws_connect()
            st.success(f"✅ Conectado • Hoja: {ws.title}")
            vals = ws.get_all_values()
            st.info(f"📄 Filas actuales (incluye encabezado): {len(vals)}")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⬇️ Forzar cargar desde Sheets"):
                    if load_layers_from_ws(ws):
                        st.success("Datos cargados.")
                        st.rerun()
                    else:
                        st.info("La hoja está vacía (sólo encabezado).")
            with c2:
                if st.button("⬆️ Forzar subir (reemplazar hoja)"):
                    save_layers_to_ws(ws)
                    st.success("Datos subidos.")
        except Exception as e:
            st.error(f"No se pudo abrir la hoja: {e}")
    else:
        st.error("No hay conexión a Google Sheets (revisa requirements y secrets).")

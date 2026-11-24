import copy
import unicodedata

import branca.colormap as cm
import folium
import geopandas as gpd
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(
    page_title="Zapatón y La Cangreja | Puriscal",
    page_icon="🗺️",
    layout="wide",
)

URL_CANTONES_INEC = (
    "https://raw.githubusercontent.com/gf0657-programacionsig/2026-ii/"
    "main/datos/inec/cantones-2022.csv"
)
URL_DISTRITOS_INEC = (
    "https://raw.githubusercontent.com/gf0657-programacionsig/2026-ii/"
    "refs/heads/main/datos/inec/distritos-2022.csv"
)
URL_DISTRITOS = (
    "https://services5.arcgis.com/4u1m1BBDkNDTVWsd/"
    "arcgis/rest/services/Distritos_CR/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&returnGeometry=true"
    "&outSR=4326&f=geojson"
)
URL_TERRITORIOS = (
    "https://services1.arcgis.com/aWQmxJWy7lM2Qqmo/"
    "arcgis/rest/services/CE_Publicos_CR/FeatureServer/4/query"
    "?where=Nombre%3D%27ZAPAT%C3%93N%27&outFields=*&returnGeometry=true"
    "&outSR=4326&f=geojson"
)
URL_ASP = (
    "https://services9.arcgis.com/RrvMEynxDB8hycVO/"
    "ArcGIS/rest/services/ÁSP_Costa_Rica_2021/FeatureServer/0/query"
)

CRS_METRICO = 5367  # CRTM05, solo para calcular áreas
VARIABLES = {
    "Población total": ("poblacion", "Población (habitantes)"),
    "Densidad de población": ("densidad", "Densidad (hab./km²)"),
    "Tasa de crecimiento": ("tasa_crecimiento_poblacion", "Tasa de crecimiento (%)"),
}
COLS_INEC = [
    "clave", "poblacion", "hombres", "mujeres", "viviendas",
    "viviendas_ocupadas", "viviendas_desocupadas",
    "tasa_crecimiento_poblacion", "densidad",
]


def clave(texto):
    """Normaliza un nombre (sin tildes, mayúsculas) para unir tablas y capas."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return t.strip().upper()


def limpiar_geometrias(gdf):
    """Filtra geometrías vacías/nulas y repara topologías básicas."""
    if gdf is None or gdf.empty:
        return gdf
    out = gdf[gdf.geometry.notna() & (~gdf.geometry.is_empty)].copy()
    out["geometry"] = out.geometry.buffer(0)
    return out[out.geometry.notna() & (~out.geometry.is_empty)].copy()


@st.cache_data(show_spinner="Cargando datos desde los servicios...")
def cargar_datos():
    # 1. Tablas del INEC (Censo 2022) - Puriscal (cantón 104)
    _cantones_inec = pd.read_csv(URL_CANTONES_INEC)
    _cantones_inec.columns = [c.strip().lower() for c in _cantones_inec.columns]

    inec = pd.read_csv(URL_DISTRITOS_INEC)
    inec.columns = [c.strip().lower() for c in inec.columns]
    inec = inec[inec["codigo_canton"] == 104].copy()
    inec["clave"] = inec["distrito"].map(clave)
    claves_puriscal = set(inec["clave"])

    # 2. Capa de distritos desde ArcGIS y filtrado ESTRICTO a Puriscal (cantón 104)
    dist = gpd.read_file(URL_DISTRITOS).to_crs(4326)
    if "nom_distr" not in dist.columns:
        for col in ["NOM_DIST", "nom_dist", "DISTRITO", "distrito", "NOMBRE", "nombre"]:
            if col in dist.columns:
                dist = dist.rename(columns={col: "nom_distr"})
                break
    if "nom_distr" not in dist.columns:
        raise ValueError(
            f"No se encontró columna de nombre de distrito en la capa. Columnas: {list(dist.columns)}"
        )

    if "cod_canton" in dist.columns:
        dist = dist[pd.to_numeric(dist["cod_canton"], errors="coerce") == 104].copy()
    elif "codigo_canton" in dist.columns:
        dist = dist[pd.to_numeric(dist["codigo_canton"], errors="coerce") == 104].copy()
    elif "canton" in dist.columns:
        dist = dist[dist["canton"].map(clave) == "PURISCAL"].copy()
    elif "nom_canton" in dist.columns:
        dist = dist[dist["nom_canton"].map(clave) == "PURISCAL"].copy()

    dist["clave"] = dist["nom_distr"].map(clave)
    dist = dist[dist["clave"].isin(claves_puriscal)].copy()
    dist = limpiar_geometrias(dist)
    if dist.empty:
        raise ValueError(
            f"No hubo coincidencia de distritos de Puriscal entre ArcGIS y el INEC. "
            f"Claves INEC Puriscal: {sorted(claves_puriscal)}"
        )

    # Conservar un nombre limpio por clave de distrito
    mapa_nombres = dist.groupby("clave")["nom_distr"].first().to_dict()
    dist = dist.dissolve(by="clave", as_index=False).copy()
    dist["nom_distr"] = dist["clave"].map(mapa_nombres)

    # 3. Territorio Huetar de Zapatón
    zap = limpiar_geometrias(gpd.read_file(URL_TERRITORIOS).to_crs(4326))

    # 4. Parque Nacional La Cangreja
    resp_asp = requests.get(
        URL_ASP,
        params={
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        },
        timeout=60,
    )
    resp_asp.raise_for_status()
    datos_asp = resp_asp.json()
    areas_protegidas = gpd.GeoDataFrame.from_features(datos_asp["features"], crs="EPSG:4326")
    col_asp = "nombre_asp" if "nombre_asp" in areas_protegidas.columns else next(
        (c for c in areas_protegidas.columns if "nombre" in c.lower() or "asp" in c.lower()),
        None,
    )
    if col_asp:
        can = areas_protegidas[
            areas_protegidas[col_asp].astype(str).str.contains("La Cangreja", case=False, na=False)
        ].copy()
    else:
        can = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
    can = limpiar_geometrias(can)

    rios = None

    dist_m = dist.to_crs(CRS_METRICO)
    dist["area_km2"] = dist_m.area / 1e6

    def por_distrito(capa_m):
        if capa_m is None or capa_m.empty:
            return gpd.GeoDataFrame(columns=["clave", "ha", "geometry"], crs=CRS_METRICO)
        p = gpd.overlay(
            capa_m[["geometry"]], dist_m[["clave", "geometry"]],
            how="intersection", keep_geom_type=True,
        )
        p = limpiar_geometrias(p)
        if p.empty:
            return gpd.GeoDataFrame(columns=["clave", "ha", "geometry"], crs=CRS_METRICO)
        p = p.dissolve(by="clave").reset_index()
        p["ha"] = p.area / 1e4
        return p

    zap_m = zap.to_crs(CRS_METRICO) if not zap.empty else gpd.GeoDataFrame(columns=["geometry"], crs=CRS_METRICO)
    can_m = can.to_crs(CRS_METRICO) if not can.empty else gpd.GeoDataFrame(columns=["geometry"], crs=CRS_METRICO)
    tras_m = (
        gpd.overlay(zap_m[["geometry"]], can_m[["geometry"]], how="intersection", keep_geom_type=True)
        if (not zap_m.empty and not can_m.empty)
        else gpd.GeoDataFrame(columns=["geometry"], crs=CRS_METRICO)
    )
    tras_m = limpiar_geometrias(tras_m)

    piezas = {
        "zapaton": por_distrito(zap_m),
        "cangreja": por_distrito(can_m),
        "traslape": por_distrito(tras_m),
    }
    for nombre, p in list(piezas.items()):
        if not p.empty:
            dist[f"{nombre}_ha"] = dist["clave"].map(p.set_index("clave")["ha"]).fillna(0.0)
            piezas[nombre] = p.merge(dist[["clave", "nom_distr"]], on="clave").to_crs(4326)
        else:
            dist[f"{nombre}_ha"] = 0.0
            piezas[nombre] = p.to_crs(4326)

    cols_inec_ok = [c for c in COLS_INEC if c in inec.columns]
    if "clave" not in cols_inec_ok:
        cols_inec_ok = ["clave"] + cols_inec_ok

    datos = dist.merge(inec[cols_inec_ok], on="clave", how="left", suffixes=("_geo", ""))

    for col_base in ["poblacion", "hombres", "mujeres", "viviendas", "densidad", "tasa_crecimiento_poblacion"]:
        if col_base not in datos.columns:
            for cand in [f"{col_base}_y", f"{col_base}_inec", f"{col_base}_x", f"{col_base}_geo"]:
                if cand in datos.columns:
                    datos[col_base] = datos[cand]
                    break
        for dup in [f"{col_base}_x", f"{col_base}_y", f"{col_base}_geo", f"{col_base}_inec"]:
            if dup in datos.columns:
                datos = datos.drop(columns=dup)

    if "poblacion" not in datos.columns:
        raise ValueError(
            f"Falta 'poblacion' tras el merge. Columnas INEC: {list(inec.columns)} | "
            f"Columnas datos: {list(datos.columns)}"
        )

    for col_num in ["poblacion", "hombres", "mujeres", "viviendas", "densidad", "tasa_crecimiento_poblacion", "area_km2"]:
        if col_num in datos.columns:
            datos[col_num] = pd.to_numeric(datos[col_num], errors="coerce").fillna(0)

    return datos, piezas, rios


@st.cache_resource(show_spinner="Construyendo mapa...")
def construir_mapa(distritos, pob_min, variable):
    datos, piezas, rios = cargar_datos()
    sel = datos[datos["nom_distr"].isin(distritos) & (datos["poblacion"] >= pob_min)]
    claves = set(sel["clave"])
    col, titulo = VARIABLES[variable]

    mapa = folium.Map(tiles=None, control_scale=True)
    folium.TileLayer("CartoDB positron", name="Mapa base claro").add_to(mapa)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=False).add_to(mapa)

    vmin, vmax = float(sel[col].min()), float(sel[col].max())
    if vmin == vmax:
        vmax = vmin + 1
    colores = cm.LinearColormap(["#ffffcc", "#fd8d3c", "#bd0026"], vmin=vmin, vmax=vmax, caption=titulo)

    folium.GeoJson(
        sel[["nom_distr", "poblacion", "densidad", "tasa_crecimiento_poblacion", "geometry"]],
        name=f"Distritos: {variable.lower()}",
        style_function=lambda f: {
            "fillColor": colores(f["properties"][col]),
            "color": "#333333", "weight": 1, "fillOpacity": 0.65,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["nom_distr", "poblacion", "densidad", "tasa_crecimiento_poblacion"],
            aliases=["Distrito", "Población (hab.)", "Densidad (hab./km²)", "Crecimiento (%)"],
            localize=True,
        ),
    ).add_to(mapa)
    colores.add_to(mapa)

    capas = [
        ("zapaton", "Territorio Huetar de Zapatón", "#B5502D"),
        ("cangreja", "Parque Nacional La Cangreja", "#00B4D8"),
        ("traslape", "Traslape Zapatón - La Cangreja", "#FF0000"),
    ]
    for llave, nombre, color in capas:
        p = piezas[llave][piezas[llave]["clave"].isin(claves)]
        if p.empty:
            continue
        folium.GeoJson(
            p[["nom_distr", "ha", "geometry"]],
            name=nombre,
            style_function=lambda f, c=color: {"color": c, "fillColor": c, "weight": 2, "fillOpacity": 0.45},
            tooltip=folium.GeoJsonTooltip(
                fields=["nom_distr", "ha"],
                aliases=["Distrito", f"Área de {nombre.split(' ')[-1]} (ha)"],
                localize=True,
            ),
        ).add_to(mapa)

    if rios is not None:
        r = rios[rios.intersects(sel.union_all())]
        if not r.empty:
            folium.GeoJson(
                r[["nombre", "tipo", "relacion", "geometry"]],
                name="Ríos y quebradas (2 km)",
                style_function=lambda f: {"color": "#1f78b4", "weight": 2},
                tooltip=folium.GeoJsonTooltip(fields=["nombre", "tipo", "relacion"], aliases=["Nombre", "Tipo", "Relación"]),
            ).add_to(mapa)

    minx, miny, maxx, maxy = sel.total_bounds
    mapa.fit_bounds([[miny, minx], [maxy, maxx]])
    folium.LayerControl(collapsed=False).add_to(mapa)
    return mapa


# ---------------------------------------------------------------- Encabezado
st.title("Contextualización del Territorio Huetar de Zapatón y Parque Nacional La Cangreja")
st.markdown(
    "Esta aplicación describe el cantón de **Puriscal** (San José, Costa Rica) por distrito, "
    "con los datos del **Censo 2022 del INEC** y las capas del **Territorio Indígena Huetar de Zapatón** "
    "y del **Parque Nacional La Cangreja** (SNIT y SINAC). Esto para dar una contextualización de mi investigacion de tesis titulada "
    "´El patrimonio cultural del Territorio Indigena de Zapatón en la planificacion del Parque Nacional La Cangreja´." 
    "Use los filtros de la barra lateral: "
    "los indicadores, la tabla, los gráficos y el mapa."
)

try:
    datos, piezas, rios = cargar_datos()
except Exception as e:
    st.error(f"No se pudieron cargar los datos desde las fuentes remotas. Detalle: {e}")
    st.stop()

# ------------------------------------------------------------------ Controles
st.sidebar.header("Filtros")
opciones = sorted(datos["nom_distr"])
distritos = st.sidebar.multiselect("Distritos", opciones, default=opciones)
pob_max = int(datos["poblacion"].max())
pob_min = st.sidebar.slider("Población mínima del distrito (hab.)", 0, pob_max, 0, step=100)
variable = st.sidebar.selectbox("Variable que colorea el mapa", list(VARIABLES))

filtrado = datos[datos["nom_distr"].isin(distritos) & (datos["poblacion"] >= pob_min)]
if filtrado.empty:
    st.warning("Ningún distrito cumple los filtros. Amplíe la selección o reduzca la población mínima.")
    st.stop()

# ----------------------------------------------------------------- Indicadores
pob = filtrado["poblacion"].sum()
area_total = filtrado["area_km2"].sum()
dens = (pob / area_total) if area_total > 0 else 0.0
c1, c2, c3, c4 = st.columns(4)
c1.metric("Población total", f"{pob:,.0f} hab.")
c2.metric("Densidad de población", f"{dens:,.1f} hab./km²")
c3.metric("Territorio de Zapatón", f"{filtrado['zapaton_ha'].sum():,.1f} ha")
c4.metric("Parque La Cangreja", f"{filtrado['cangreja_ha'].sum():,.1f} ha")

# ------------------------------------------------------------------- Pestañas
tab_tabla, tab_graf, tab_mapa = st.tabs(["Tabla", "Gráficos", "Mapa"])

with tab_tabla:
    st.subheader("Datos demográficos y áreas por distrito")
    tabla = filtrado.drop(columns="geometry")[
        ["nom_distr", "poblacion", "hombres", "mujeres", "densidad",
         "tasa_crecimiento_poblacion", "viviendas", "zapaton_ha", "cangreja_ha", "traslape_ha"]
    ]
    st.dataframe(
        tabla, hide_index=True, use_container_width=True,
        column_config={
            "nom_distr": "Distrito",
            "poblacion": st.column_config.NumberColumn("Población (hab.)", format="%d"),
            "hombres": st.column_config.NumberColumn("Hombres (hab.)", format="%d"),
            "mujeres": st.column_config.NumberColumn("Mujeres (hab.)", format="%d"),
            "densidad": st.column_config.NumberColumn("Densidad (hab./km²)", format="%.1f"),
            "tasa_crecimiento_poblacion": st.column_config.NumberColumn("Crecimiento (%)", format="%.2f"),
            "viviendas": st.column_config.NumberColumn("Viviendas", format="%d"),
            "zapaton_ha": st.column_config.NumberColumn("Zapatón (ha)", format="%.1f"),
            "cangreja_ha": st.column_config.NumberColumn("La Cangreja (ha)", format="%.1f"),
            "traslape_ha": st.column_config.NumberColumn("Traslape (ha)", format="%.1f"),
        },
    )
    st.download_button(
        "Descargar tabla (CSV)", tabla.to_csv(index=False).encode("utf-8"),
        "puriscal_distritos.csv", "text/csv",
    )

with tab_graf:
    st.subheader("Población por distrito y sexo")
    sexo = filtrado.melt(id_vars="nom_distr", value_vars=["hombres", "mujeres"],
                         var_name="Sexo", value_name="Habitantes")
    sexo["Sexo"] = sexo["Sexo"].str.capitalize()
    fig1 = px.bar(
        sexo, x="nom_distr", y="Habitantes", color="Sexo", barmode="group",
        title="Población por distrito según sexo, Censo 2022",
        labels={"nom_distr": "Distrito", "Habitantes": "Población (habitantes)"},
        color_discrete_map={"Hombres": "#1F77B4", "Mujeres": "#FF7F0E"},
    )
    st.plotly_chart(fig1, use_container_width=True)

    st.subheader("Áreas del territorio y del parque por distrito")
    ha = filtrado.melt(id_vars="nom_distr", value_vars=["zapaton_ha", "cangreja_ha", "traslape_ha"],
                       var_name="Área", value_name="Hectáreas")
    ha["Área"] = ha["Área"].map({"zapaton_ha": "Territorio de Zapatón",
                                 "cangreja_ha": "Parque La Cangreja",
                                 "traslape_ha": "Traslape"})
    ha = ha[ha["Hectáreas"] > 0]
    if ha.empty:
        st.info("Los distritos seleccionados no contienen el territorio ni el parque.")
    else:
        fig2 = px.bar(
            ha, x="nom_distr", y="Hectáreas", color="Área", barmode="group",
            title="Área del territorio, del parque y de su traslape por distrito",
            labels={"nom_distr": "Distrito", "Hectáreas": "Área (hectáreas)"},
            color_discrete_map={"Territorio de Zapatón": "#B5502D",
                                "Parque La Cangreja": "#00B4D8", "Traslape": "#FF0000"},
        )
        st.plotly_chart(fig2, use_container_width=True)
        st.caption("El Plan de Manejo del parque (SINAC, 2018) reporta un traslape de 68,05 ha.")

with tab_mapa:
    st.subheader(f"Mapa de Puriscal: {variable.lower()}")
    mapa = construir_mapa(tuple(distritos), pob_min, variable)
    st_folium(copy.deepcopy(mapa), height=560, use_container_width=True,
              returned_objects=[], key="mapa")

# ---------------------------------------------------------------------- Pie
st.divider()
st.caption(
    "**Fuentes:** "
    "[INEC, Censo 2022](https://admin.inec.cr/sites/default/files/2023-11/reResultadosEstimacionPoblacionVivienda2022_3.xlsx) · "
    "[IGN / SNIT, límites distritales](https://www.snitcr.go.cr/) · "
    "SINAC, áreas silvestres protegidas · "
    "Territorios indígenas, servicio de ArcGIS (CE_Publicos_CR). "
    "Elaborado por Andrés Moya M., 2026."
)
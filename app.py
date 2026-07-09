"""
Dashboard MCP — Auditoría de correcciones del padrón de MCPs (RENIEC)
======================================================================

Contexto: RENIEC corrige periódicamente el conteo de electores de cada MCP
(Municipalidad de Centro Poblado) a lo largo de varias rondas de revisión.
Este dashboard visualiza cómo evolucionó el conteo de electores de cada MCP
desde el primer registro (febrero) hasta el valor final consolidado, y hace
seguimiento de los errores detectados en el proceso: cuáles ya fueron
subsanados (con su causa) y cuáles siguen pendientes de resolución.

Fuente de datos: DATABASES/PADRON_MODIFICACIONES_VF.xlsx (fuente única,
una fila por MCP con las 4 etapas de corrección + metadata de errores).
Límites geográficos para el mapa de coropletas: geofiles/*.gpkg (provincia).
Pipeline de limpieza y consolidación: ver notebooks/AUDITORIA_MODIFICACIONES.ipynb
(misma lógica, documentada paso a paso con etapas numeradas).

Ejecutar: streamlit run app.py
Documentación completa: ver README.md en la raíz del proyecto.
"""

import io
import json
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

try:
    import geopandas as gpd
    GEOPANDAS_OK = True
except ImportError:
    GEOPANDAS_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

DATABASES_DIR = Path(__file__).parent / "DATABASES"
PADRON_PATH = DATABASES_DIR / "PADRON_MODIFICACIONES_VF.xlsx"
GEOFILES_DIR = Path(__file__).parent / "geofiles"
PROVINCIA_GPKG = GEOFILES_DIR / "PROVINCIA.gpkg"

ETAPAS_ORDEN = ["FEBRERO", "ABRIL", "JUNIO_1", "JUNIO_2", "FINAL"]

st.set_page_config(
    page_title="Dashboard MCP",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES DE LIMPIEZA
# ─────────────────────────────────────────────────────────────────────────────

def unescape_excel_xml(s: str) -> str:
    """Convierte escapes _xHHHH_ de openpyxl al carácter Unicode real.

    openpyxl serializa bytes UTF-8 no válidos en cp1252 (ej. el segundo byte
    de "Á") como el literal "_x0081_" en el XML del .xlsx. Debe revertirse
    ANTES de fix_garbled(), o ese texto de escape se trata como si fueran
    caracteres reales del nombre de la MCP.
    """
    return re.sub(r'_x([0-9A-Fa-f]{4})_', lambda m: chr(int(m.group(1), 16)), s)


def fix_garbled(s: str) -> str:
    """Repara mojibake UTF-8→cp1252. Debe aplicarse ANTES de upper()."""
    try:
        buf = bytearray()
        for c in s:
            o = ord(c)
            if 0x0080 <= o <= 0x009F:
                buf.append(o)
            else:
                try:
                    buf.extend(c.encode("cp1252"))
                except UnicodeEncodeError:
                    return s
        return buf.decode("utf-8")
    except (UnicodeDecodeError, Exception):
        return s


def normalize_text(x) -> str:
    """Limpieza estándar (con upper) para columnas geográficas / categóricas."""
    if pd.isna(x):
        return np.nan
    s = str(x).strip()
    s = unescape_excel_xml(s)
    s = fix_garbled(s)
    s = s.upper()
    s = unicodedata.normalize("NFC", s)
    return s


def normalize_text_preserve_case(x) -> str:
    """Igual que normalize_text pero sin upper(): para texto narrativo (causas,
    detalles de error) pensado para lectura humana, donde mayusculizar todo
    lo haría ilegible."""
    if pd.isna(x):
        return np.nan
    s = str(x).strip()
    s = unescape_excel_xml(s)
    s = fix_garbled(s)
    s = unicodedata.normalize("NFC", s)
    return s


def normalize_cod(x, width: int = 9) -> str:
    if pd.isna(x):
        return pd.NA
    try:
        return str(int(float(x))).zfill(width)
    except (ValueError, TypeError):
        return pd.NA


def categorizar_causa(causa) -> str:
    """Agrupa las ~12 causas crudas en 6 categorías legibles (propuesta B,
    aprobada con el usuario). Usa coincidencia por substring (no igualdad
    exacta) para tolerar variantes menores de redacción sin mantener un
    diccionario 1 a 1 con el texto exacto del Excel."""
    if pd.isna(causa):
        return np.nan
    c = causa.lower()
    if "revisión manual" in c or "revision manual" in c:
        return "Error de revisión manual"
    if "distrito" in c and "quitad" in c:
        return "Electores de otro distrito quitados"
    if "no validada" in c:
        return "Otras causas puntuales"
    if any(k in c for k in ["omitió la inclusión", "omitio la inclusion", "omitió un anexo", "omitio un anexo", "faltó incluir", "falto incluir"]):
        return "GEO: omisión de listas/anexos"
    if any(k in c for k in ["desactualizada", "extemporaneo", "extemporáneo", "extemporaneos", "extemporáneos"]):
        return "GEO: información desactualizada o extemporánea"
    if any(k in c for k in ["asignó erroneamente", "asigno erroneamente", "dni"]):
        return "GEO: asignación incorrecta (códigos/DNI)"
    return "Otras causas puntuales"


# ─────────────────────────────────────────────────────────────────────────────
# CARGA Y CONSOLIDACIÓN DE DATOS
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_data():
    """Carga PADRON_MODIFICACIONES_VF.xlsx y construye las tablas base.

    Retorna:
        df: una fila por MCP única, con las 5 etapas "efectivas" (forward-fill
            horizontal sobre las columnas de corrección crudas) y columnas
            derivadas de variación y estado de error.
        df_evolucion: una fila por MCP × etapa (tabla larga, análoga a un
            "df_long"), usada para graficar tendencias sin huecos.

    Tratamiento de las columnas de corrección crudas (ELECTORES_FEBRERO,
    CORRECCION_ABRIL, CORRECCION_JUNIO_1, CORRECCION_JUNIO_2): solo tienen
    valor cuando esa ronda tocó la MCP; el resto es NaN, que NO significa
    "sin electores" sino "sigue vigente el último valor conocido". Se resuelve
    con forward-fill horizontal en orden temporal. ETAPA_FINAL se fija siempre
    a CANTIDAD_FINAL (no se deriva por ffill): es el valor consolidado
    autoritativo y diverge en 2 filas del ffill puro, señal de ajustes
    manuales no capturados en las columnas de ronda.
    """
    if not PADRON_PATH.exists():
        st.error(f"No se encontró el archivo fuente en {PADRON_PATH}")
        st.stop()

    df = pd.read_excel(PADRON_PATH, sheet_name="Hoja1")

    df["COD_MCP_RENIEC"] = df["COD_MCP_RENIEC"].apply(lambda x: normalize_cod(x, 9))
    df["UBIGEO"] = df["UBIGEO"].apply(lambda x: normalize_cod(x, 6))

    for col in ["DEPARTAMENTO", "PROVINCIA", "DISTRITO", "MCP"]:
        df[col] = df[col].apply(normalize_text)

    for col in ["CAUSA_ERROR_SUBSANADO", "ERROR_PENDIENTE_CAUSA", "ERROR_PENDIENTE_DETALLE"]:
        df[col] = df[col].apply(normalize_text_preserve_case)

    df["ES_SUBSANADO"] = df["DISTRITO_CON_ERROR_SUBSANADO"].astype(str).str.strip().str.upper().eq("SI")
    df["ES_PENDIENTE"] = df["ERROR_PENDIENTE"].astype(str).str.strip().str.upper().eq("SI")

    stage_raw_cols = [
        "ELECTORES_FEBRERO", "CORRECCION_ABRIL",
        "CORRECCION_JUNIO_1", "CORRECCION_JUNIO_2", "CANTIDAD_FINAL",
    ]
    for c in stage_raw_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    etapas_prev = ["ELECTORES_FEBRERO", "CORRECCION_ABRIL", "CORRECCION_JUNIO_1", "CORRECCION_JUNIO_2"]
    eff = df[etapas_prev].ffill(axis=1)

    df["ETAPA_FEBRERO"] = eff["ELECTORES_FEBRERO"]
    df["ETAPA_ABRIL"] = eff["CORRECCION_ABRIL"]
    df["ETAPA_JUNIO_1"] = eff["CORRECCION_JUNIO_1"]
    df["ETAPA_JUNIO_2"] = eff["CORRECCION_JUNIO_2"]
    df["ETAPA_FINAL"] = df["CANTIDAD_FINAL"]

    df["NUEVA_POST_FEBRERO"] = df["ELECTORES_FEBRERO"].isna()

    df["VARIACION_ABS"] = df["CANTIDAD_FINAL"] - df["ELECTORES_FEBRERO"]
    df["VARIACION_PCT"] = np.where(
        df["ELECTORES_FEBRERO"].fillna(0) != 0,
        df["VARIACION_ABS"] / df["ELECTORES_FEBRERO"] * 100,
        np.nan,
    )
    df["N_CORRECCIONES"] = df[["CORRECCION_ABRIL", "CORRECCION_JUNIO_1", "CORRECCION_JUNIO_2"]].notna().sum(axis=1)

    def estado_error(row) -> str:
        """PENDIENTE tiene prioridad sobre SUBSANADO en el solape de 8 filas
        donde una MCP quedó marcada con ambos flags."""
        if row["ES_PENDIENTE"]:
            return "PENDIENTE"
        if row["ES_SUBSANADO"]:
            return "SUBSANADO"
        return "SIN ERROR"

    df["ESTADO_ERROR"] = df.apply(estado_error, axis=1)

    def causa_unificada(row):
        if row["ESTADO_ERROR"] == "PENDIENTE":
            return row["ERROR_PENDIENTE_CAUSA"]
        if row["ESTADO_ERROR"] == "SUBSANADO":
            return row["CAUSA_ERROR_SUBSANADO"]
        return np.nan

    df["CAUSA"] = df.apply(causa_unificada, axis=1)
    # Categoría agrupada de la causa (6 buckets legibles) — solo para UI,
    # no reemplaza a CAUSA (que conserva el texto original íntegro).
    df["CAUSA_CATEGORIA"] = df["CAUSA"].apply(categorizar_causa)

    # ── df_evolucion: tabla larga MCP × etapa (para gráficos de tendencia) ──
    etapa_col = {
        "FEBRERO": ("ETAPA_FEBRERO", "ELECTORES_FEBRERO"),
        "ABRIL": ("ETAPA_ABRIL", "CORRECCION_ABRIL"),
        "JUNIO_1": ("ETAPA_JUNIO_1", "CORRECCION_JUNIO_1"),
        "JUNIO_2": ("ETAPA_JUNIO_2", "CORRECCION_JUNIO_2"),
        "FINAL": ("ETAPA_FINAL", "CANTIDAD_FINAL"),
    }
    id_cols = ["COD_MCP_RENIEC", "UBIGEO", "DEPARTAMENTO", "PROVINCIA", "DISTRITO", "MCP"]
    frames = []
    for etapa in ETAPAS_ORDEN:
        eff_col, raw_col = etapa_col[etapa]
        sub = df[id_cols + [eff_col, raw_col]].rename(columns={eff_col: "CANTIDAD"}).copy()
        sub["ETAPA"] = etapa
        sub["ES_ESTIMADO"] = sub[raw_col].isna() & sub["CANTIDAD"].notna()
        frames.append(sub.drop(columns=[raw_col]))

    df_evolucion = pd.concat(frames, ignore_index=True)
    df_evolucion = df_evolucion.dropna(subset=["CANTIDAD"]).reset_index(drop=True)
    df_evolucion["ETAPA"] = pd.Categorical(df_evolucion["ETAPA"], categories=ETAPAS_ORDEN, ordered=True)

    return df, df_evolucion


@st.cache_data(show_spinner=False)
def load_provincia_geojson():
    """Lee geofiles/PROVINCIA.gpkg y devuelve un GeoJSON (dict) con una
    propiedad ID_PROV = "DEPARTAMENTO - PROVINCIA" para unir con `df` vía
    px.choropleth (featureidkey="properties.ID_PROV").

    Simplifica la geometría (tolerancia ~1 km) para que el payload sea liviano
    en Streamlit Cloud. Corrige un alias de escritura confirmado entre la
    fuente del padrón y el geopackage: "ANTONIO RAYMONDI" (geopackage) es la
    misma provincia que "ANTONIO RAIMONDI" (padrón), en Áncash.
    """
    gdf = gpd.read_file(PROVINCIA_GPKG)
    gdf["nombdep"] = gdf["nombdep"].str.upper().str.strip()
    gdf["nombprov"] = gdf["nombprov"].str.upper().str.strip()
    gdf["nombprov"] = gdf["nombprov"].replace({"ANTONIO RAYMONDI": "ANTONIO RAIMONDI"})
    gdf["ID_PROV"] = gdf["nombdep"] + " - " + gdf["nombprov"]
    gdf["geometry"] = gdf["geometry"].simplify(0.01, preserve_topology=True)
    return json.loads(gdf[["ID_PROV", "nombdep", "nombprov", "geometry"]].to_json())


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="MCP")
    return buf.getvalue()


# ── Paleta institucional RENIEC ──────────────────────────────────────────────
# Azul marino oficial (#002F56, confirmado en el manual gráfico de RENIEC)
# como color primario/neutro; semáforo semántico para estado de error para
# no perder la lectura inmediata de "bien / subsanado / pendiente".
RENIEC_AZUL = "#002F56"
RENIEC_AZUL_CLARO = "#2E6F9E"
RENIEC_AZUL_SUAVE = "#EAF1F8"
RENIEC_GRIS = "#7F8C8D"

ESTADO_COLOR_MAP = {
    "SIN ERROR": "#27AE60",
    "SUBSANADO": RENIEC_AZUL_CLARO,
    "PENDIENTE": "#C0392B",
}

CAUSA_CATEGORIA_COLOR_MAP = {
    "Error de revisión manual": RENIEC_AZUL,
    "GEO: omisión de listas/anexos": RENIEC_AZUL_CLARO,
    "GEO: información desactualizada o extemporánea": "#6FA3C7",
    "GEO: asignación incorrecta (códigos/DNI)": "#A9C4DE",
    "Electores de otro distrito quitados": "#C0392B",
    "Otras causas puntuales": RENIEC_GRIS,
}

TIMELINE_COLORES = {
    "PRIMERA": "#D5E8D4",
    "SIN_CAMBIO": "#EAEAEA",
    "SUBIO": "#D5E8D4",
    "BAJO": "#FFE6CC",
}

# Nombres de columnas más explícitos para tablas orientadas al usuario final
# (solo afecta la presentación; el DataFrame interno conserva sus nombres).
DISPLAY_NAMES = {
    "DEPARTAMENTO": "Departamento",
    "PROVINCIA": "Provincia",
    "DISTRITO": "Distrito",
    "MCP": "MCP",
    "COD_MCP_RENIEC": "Código RENIEC",
    "ESTADO_ERROR": "Estado del error",
    "CAUSA": "Causa del error",
    "CAUSA_CATEGORIA": "Categoría de causa",
    "ELECTORES_FEBRERO": "Electores (febrero)",
    "CORRECCION_ABRIL": "Corrección (abril)",
    "CORRECCION_JUNIO_1": "Corrección (junio, 1ª ronda)",
    "CORRECCION_JUNIO_2": "Corrección (junio, 2ª ronda)",
    "CANTIDAD_FINAL": "Electores (final)",
    "VARIACION_ABS": "Variación absoluta",
    "VARIACION_PCT": "Variación (%)",
    "N_CORRECCIONES": "N° de correcciones",
}


def con_nombres_amigables(df_in: pd.DataFrame) -> pd.DataFrame:
    """Devuelve una copia solo para mostrar/descargar con encabezados legibles."""
    return df_in.rename(columns=DISPLAY_NAMES)


# ─────────────────────────────────────────────────────────────────────────────
# CARGA
# ─────────────────────────────────────────────────────────────────────────────

with st.spinner("Cargando datos..."):
    df, df_evolucion = load_data()

total_mcps = len(df)
n_subsanados = int((df["ESTADO_ERROR"] == "SUBSANADO").sum())
n_pendientes = int((df["ESTADO_ERROR"] == "PENDIENTE").sum())

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────

st.sidebar.title("📋 Dashboard MCP")
st.sidebar.caption("Auditoría de correcciones del padrón de MCPs — RENIEC")
st.sidebar.divider()

pagina = st.sidebar.radio(
    "Vista",
    [
        "🏠  Resumen ejecutivo",
        "📈  Evolución de electores",
        "⚠️  Mapa de errores",
        "🔍  Ficha por MCP",
    ],
)

st.sidebar.divider()
st.sidebar.caption(
    f"**{total_mcps:,}** MCPs · **{n_subsanados}** subsanados · **{n_pendientes}** pendientes"
)

# ─────────────────────────────────────────────────────────────────────────────
# VISTA 1 — RESUMEN EJECUTIVO
# ─────────────────────────────────────────────────────────────────────────────

if pagina == "🏠  Resumen ejecutivo":
    st.title("Resumen ejecutivo")

    electores_finales = int(df["CANTIDAD_FINAL"].sum())
    n_nuevas = int(df["NUEVA_POST_FEBRERO"].sum())
    pct_subsanados = n_subsanados / total_mcps * 100
    pct_pendientes = n_pendientes / total_mcps * 100

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("MCPs totales", f"{total_mcps:,}")
    c1.caption("Municipalidades de Centro Poblado registradas en el padrón.")
    c2.metric("Electores (final)", f"{electores_finales:,}")
    c2.caption("Suma de electores en la etapa final consolidada.")
    c3.metric("Errores subsanados", f"{n_subsanados:,}", f"{pct_subsanados:.1f} %")
    c4.metric("Errores pendientes", f"{n_pendientes:,}", f"−{pct_pendientes:.1f} %", delta_color="inverse")
    c5.metric("MCPs nuevas (post-febrero)", f"{n_nuevas:,}")

    st.divider()

    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.subheader("Evolución del padrón — electores totales y MCPs corregidas por etapa")
        st.caption(
            "La línea muestra el total de electores acumulado en cada etapa; las barras "
            "muestran cuántas MCPs tuvieron un dato reportado explícitamente en esa ronda "
            "(no arrastrado de una etapa anterior)."
        )
        evol_total = (
            df_evolucion.groupby("ETAPA", observed=True)["CANTIDAD"]
            .sum()
            .reindex(ETAPAS_ORDEN)
            .reset_index(name="Total de electores")
        )
        mcps_por_etapa = pd.Series(
            {
                "FEBRERO": df["ELECTORES_FEBRERO"].notna().sum(),
                "ABRIL": df["CORRECCION_ABRIL"].notna().sum(),
                "JUNIO_1": df["CORRECCION_JUNIO_1"].notna().sum(),
                "JUNIO_2": df["CORRECCION_JUNIO_2"].notna().sum(),
                "FINAL": df["CANTIDAD_FINAL"].notna().sum(),
            }
        ).reindex(ETAPAS_ORDEN)

        fig_evol = make_subplots(specs=[[{"secondary_y": True}]])
        fig_evol.add_trace(
            go.Bar(
                x=ETAPAS_ORDEN, y=mcps_por_etapa.values,
                name="MCPs corregidas en la etapa",
                marker_color=RENIEC_AZUL_SUAVE,
                marker_line_color=RENIEC_AZUL_CLARO, marker_line_width=1,
                text=mcps_por_etapa.values, textposition="outside",
            ),
            secondary_y=False,
        )
        fig_evol.add_trace(
            go.Scatter(
                x=ETAPAS_ORDEN, y=evol_total["Total de electores"],
                name="Total de electores", mode="lines+markers+text",
                line=dict(color=RENIEC_AZUL, width=3),
                text=evol_total["Total de electores"].map("{:,}".format),
                textposition="top center",
            ),
            secondary_y=True,
        )
        fig_evol.update_layout(
            margin=dict(t=10, b=10), legend=dict(orientation="h", y=-0.15),
        )
        fig_evol.update_yaxes(title_text="MCPs corregidas", secondary_y=False)
        fig_evol.update_yaxes(title_text="Total de electores", secondary_y=True)
        st.plotly_chart(fig_evol, width="stretch")

    with col_r:
        st.subheader("Composición por estado de error")
        estado_counts = df["ESTADO_ERROR"].value_counts().reindex(
            ["SIN ERROR", "SUBSANADO", "PENDIENTE"]
        ).reset_index()
        estado_counts.columns = ["Estado", "MCPs"]
        fig_estado = px.pie(
            estado_counts, names="Estado", values="MCPs",
            color="Estado", color_discrete_map=ESTADO_COLOR_MAP, hole=0.45,
        )
        fig_estado.update_traces(textinfo="value+percent")
        fig_estado.update_layout(margin=dict(t=10, b=10))
        st.plotly_chart(fig_estado, width="stretch")

    st.divider()

    col_causa, col_prov = st.columns([2, 3])

    with col_causa:
        st.subheader("Top causas de error subsanado")
        st.caption("Causas agrupadas en 6 categorías para facilitar la lectura.")
        causas = (
            df[df["ESTADO_ERROR"] == "SUBSANADO"]
            .groupby("CAUSA_CATEGORIA")
            .size()
            .reset_index(name="Cantidad")
            .sort_values("Cantidad", ascending=False)
            .rename(columns={"CAUSA_CATEGORIA": "Categoría de causa"})
        )
        causas["% del total subsanado"] = (causas["Cantidad"] / n_subsanados * 100).map("{:.1f} %".format)
        st.dataframe(causas, hide_index=True, width="stretch")

    with col_prov:
        st.subheader("¿En qué provincias se dieron las causas de error?")
        st.caption("Top 15 provincias con más MCPs con error, por categoría de causa.")
        errores_prov = (
            df[df["ESTADO_ERROR"] != "SIN ERROR"]
            .assign(PROV_LABEL=lambda d: d["PROVINCIA"] + " (" + d["DEPARTAMENTO"] + ")")
            .groupby(["PROV_LABEL", "CAUSA_CATEGORIA"])
            .size()
            .reset_index(name="MCPs")
        )
        if errores_prov.empty:
            st.info("No hay MCPs con error para mostrar.")
        else:
            top_provs = (
                errores_prov.groupby("PROV_LABEL")["MCPs"].sum().nlargest(15).index.tolist()
            )
            fig_prov_causa = px.bar(
                errores_prov[errores_prov["PROV_LABEL"].isin(top_provs)],
                x="MCPs", y="PROV_LABEL", orientation="h",
                color="CAUSA_CATEGORIA", color_discrete_map=CAUSA_CATEGORIA_COLOR_MAP,
                labels={"PROV_LABEL": "Provincia", "CAUSA_CATEGORIA": "Categoría de causa"},
            )
            fig_prov_causa.update_layout(
                yaxis={"categoryorder": "total ascending"},
                margin=dict(t=10, b=10), legend=dict(orientation="h", y=-0.2),
                height=460,
            )
            st.plotly_chart(fig_prov_causa, width="stretch")


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 2 — EVOLUCIÓN DE ELECTORES
# ─────────────────────────────────────────────────────────────────────────────

elif pagina == "📈  Evolución de electores":
    st.title("Evolución de electores")

    col_l, col_r = st.columns(2)

    with col_l:
        sub_l, pop_l = st.columns([4, 1])
        sub_l.subheader("Top 20 MCPs por variación absoluta (Feb. → Final)")
        with pop_l.popover("ℹ️"):
            st.write(
                "Cada barra es cuánto cambió el conteo de electores de una MCP entre el "
                "primer registro (febrero) y el valor final consolidado.\n\n"
                "- **Verde (subió):** normalmente refleja electores agregados en una "
                "corrección posterior (nuevos empadronados o anexos incorporados).\n"
                "- **Rojo (bajó):** suele corresponder a correcciones donde se retiraron "
                "electores que habían sido asignados por error a esta MCP o a un distrito "
                "equivocado. Para ver la causa exacta de una MCP puntual, revisa la pestaña "
                "**Mapa de errores** o **Ficha por MCP**."
            )
        variadas = (
            df[df["VARIACION_ABS"].notna()]
            .assign(Signo=lambda d: np.where(d["VARIACION_ABS"] >= 0, "Subió", "Bajó"))
            .reindex(df["VARIACION_ABS"].abs().sort_values(ascending=False).index)
            .head(20)
        )
        fig_var = px.bar(
            variadas, x="VARIACION_ABS", y="MCP", orientation="h",
            color="Signo", color_discrete_map={"Subió": "#27AE60", "Bajó": "#C0392B"},
            text="VARIACION_ABS",
            hover_data={"DEPARTAMENTO": True, "PROVINCIA": True},
        )
        fig_var.update_traces(texttemplate="%{text:,}", textposition="outside")
        fig_var.update_layout(
            yaxis={"categoryorder": "total ascending"},
            margin=dict(t=10, b=10, l=10, r=60),
            height=500, xaxis_title="Variación absoluta de electores",
        )
        st.plotly_chart(fig_var, width="stretch")

    with col_r:
        st.subheader("¿Cuántas veces se corrigió cada MCP?")
        st.caption("Proporción de MCPs según su número de correcciones (0 a 3 rondas).")
        dist_correcciones = (
            df["N_CORRECCIONES"].value_counts().sort_index().reset_index()
        )
        dist_correcciones.columns = ["N_CORRECCIONES", "Cantidad"]
        dist_correcciones["Nº de correcciones"] = dist_correcciones["N_CORRECCIONES"].map(
            lambda n: f"{n} corrección(es)"
        )
        dist_correcciones["Grupo"] = "Todas las MCPs"
        dist_correcciones["Porcentaje"] = dist_correcciones["Cantidad"] / total_mcps * 100

        pct_sin_correccion = dist_correcciones.loc[
            dist_correcciones["N_CORRECCIONES"] == 0, "Porcentaje"
        ].sum()
        st.metric("MCPs sin ninguna corrección", f"{pct_sin_correccion:.1f} %")

        fig_ncorr = px.bar(
            dist_correcciones, x="Porcentaje", y="Grupo", orientation="h",
            color="Nº de correcciones", barmode="stack",
            color_discrete_sequence=px.colors.sequential.Blues[2:],
            text=dist_correcciones["Porcentaje"].map("{:.1f}%".format),
            hover_data={"Cantidad": True},
        )
        fig_ncorr.update_traces(textposition="inside")
        fig_ncorr.update_layout(
            margin=dict(t=10, b=10), height=340,
            xaxis_title="% de MCPs", yaxis_title="",
            legend=dict(orientation="h", y=-0.3),
        )
        st.plotly_chart(fig_ncorr, width="stretch")

    st.divider()

    st.subheader("Tabla de correcciones por MCP")
    deptos_e = ["(Todos)"] + sorted(df["DEPARTAMENTO"].dropna().unique().tolist())
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        depto_e_sel = st.selectbox("Departamento", deptos_e, key="depto_evol")

    df_tabla = df.copy()
    if depto_e_sel != "(Todos)":
        df_tabla = df_tabla[df_tabla["DEPARTAMENTO"] == depto_e_sel]

    with col_f2:
        provs_e = ["(Todas)"] + sorted(df_tabla["PROVINCIA"].dropna().unique().tolist())
        prov_e_sel = st.selectbox("Provincia", provs_e, key="prov_evol")
    if prov_e_sel != "(Todas)":
        df_tabla = df_tabla[df_tabla["PROVINCIA"] == prov_e_sel]

    cols_evol = [
        "DEPARTAMENTO", "PROVINCIA", "DISTRITO", "MCP", "COD_MCP_RENIEC",
        "ELECTORES_FEBRERO", "CORRECCION_ABRIL", "CORRECCION_JUNIO_1",
        "CORRECCION_JUNIO_2", "CANTIDAD_FINAL", "VARIACION_ABS", "VARIACION_PCT",
        "N_CORRECCIONES",
    ]
    st.caption(f"**{len(df_tabla):,}** MCPs")
    st.dataframe(con_nombres_amigables(df_tabla[cols_evol]).reset_index(drop=True), hide_index=True, width="stretch")

    st.download_button(
        "⬇ Descargar tabla (.xlsx)",
        data=to_excel_bytes(con_nombres_amigables(df_tabla[cols_evol])),
        file_name="mcp_evolucion.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 3 — MAPA DE ERRORES
# ─────────────────────────────────────────────────────────────────────────────

elif pagina == "⚠️  Mapa de errores":
    st.title("Mapa de errores")

    deptos = ["(Todos)"] + sorted(df["DEPARTAMENTO"].dropna().unique().tolist())
    causas_disp = sorted(df["CAUSA"].dropna().unique().tolist())

    with st.expander("Filtros", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            depto_sel = st.selectbox("Departamento", deptos, key="depto_err")
            provs = ["(Todas)"]
            if depto_sel != "(Todos)":
                provs += sorted(df[df["DEPARTAMENTO"] == depto_sel]["PROVINCIA"].dropna().unique().tolist())
            prov_sel = st.selectbox("Provincia", provs, key="prov_err", disabled=(depto_sel == "(Todos)"))
        with col2:
            estado_sel = st.multiselect("Estado del error", ["SUBSANADO", "PENDIENTE"], default=[])
        with col3:
            causa_sel = st.multiselect("Causa", causas_disp, default=[])
        solo_error = st.checkbox("Solo MCPs con error", value=True)

    df_filt = df.copy()
    if depto_sel != "(Todos)":
        df_filt = df_filt[df_filt["DEPARTAMENTO"] == depto_sel]
        if prov_sel != "(Todas)":
            df_filt = df_filt[df_filt["PROVINCIA"] == prov_sel]

    if estado_sel:
        df_filt = df_filt[df_filt["ESTADO_ERROR"].isin(estado_sel)]
    if causa_sel:
        df_filt = df_filt[df_filt["CAUSA"].isin(causa_sel)]
    if solo_error:
        df_filt = df_filt[df_filt["ESTADO_ERROR"] != "SIN ERROR"]

    # ── Gráfico: errores por provincia (respeta los filtros de arriba) ──────
    st.subheader("Errores por provincia")
    df_prov_chart = df_filt[df_filt["ESTADO_ERROR"] != "SIN ERROR"].copy()
    if df_prov_chart.empty:
        st.info("No hay MCPs con error para los filtros seleccionados.")
    else:
        df_prov_chart["PROV_LABEL"] = df_prov_chart["PROVINCIA"] + " (" + df_prov_chart["DEPARTAMENTO"] + ")"
        heat_data = (
            df_prov_chart.groupby(["PROV_LABEL", "ESTADO_ERROR"])
            .size()
            .reset_index(name="n")
        )
        # Si hay un departamento/provincia puntual filtrado se muestran todas
        # sus provincias; si no, se limita a las 20 con más errores a nivel
        # nacional para no saturar el eje.
        if depto_sel == "(Todos)":
            top_provs = heat_data.groupby("PROV_LABEL")["n"].sum().nlargest(20).index.tolist()
            heat_data = heat_data[heat_data["PROV_LABEL"].isin(top_provs)]
        fig_stk = px.bar(
            heat_data, x="PROV_LABEL", y="n",
            color="ESTADO_ERROR", barmode="stack",
            color_discrete_map=ESTADO_COLOR_MAP,
            labels={"n": "MCPs", "PROV_LABEL": "Provincia", "ESTADO_ERROR": "Estado"},
        )
        fig_stk.update_layout(
            xaxis_tickangle=-40, margin=dict(t=10, b=90), legend_title="Estado del error",
            xaxis={"categoryorder": "total descending"},
        )
        st.plotly_chart(fig_stk, width="stretch")

    st.divider()

    # ── Mapa de coropletas por provincia ─────────────────────────────────────
    st.subheader("Mapa de coropletas — MCPs con error por provincia")
    st.caption(
        "Vista geográfica a nivel nacional (independiente de los filtros de arriba). "
        "Elige si quieres ver el total de errores o el conteo de una causa específica."
    )

    if not GEOPANDAS_OK:
        st.warning(
            "El paquete `geopandas` no está disponible en este entorno, así que el mapa "
            "de coropletas no se puede mostrar. Revisa `requirements.txt`."
        )
    else:
        modo_mapa = st.radio(
            "Ver mapa por:", ["Estado del error", "Causa específica"],
            horizontal=True, key="modo_mapa",
        )

        df_err_nacional = df[df["ESTADO_ERROR"] != "SIN ERROR"].dropna(subset=["DEPARTAMENTO", "PROVINCIA"]).copy()
        df_err_nacional["ID_PROV"] = df_err_nacional["DEPARTAMENTO"] + " - " + df_err_nacional["PROVINCIA"]

        try:
            geojson_prov = load_provincia_geojson()
        except Exception as exc:  # noqa: BLE001 — mostramos el error, no rompemos el resto del tab
            geojson_prov = None
            st.error(f"No se pudo leer geofiles/PROVINCIA.gpkg: {exc}")

        if geojson_prov is not None:
            # Nota técnica: se usa choropleth_map (MapLibre) y NO choropleth
            # (geo/d3-geo) porque este último tiene un bug de renderizado
            # confirmado en esta versión de Plotly con GeoJSON custom de
            # muchos polígonos: en vez de recortar cada polígono a su forma
            # real, pinta todo el lienzo con el color de la última
            # feature. choropleth_map no tiene ese problema y además trae
            # mapa base real (ríos, fronteras, ciudades) de regalo.
            mapa_kwargs = dict(
                geojson=geojson_prov, locations="ID_PROV",
                featureidkey="properties.ID_PROV",
                center={"lat": -9.2, "lon": -75.0}, zoom=4.3, opacity=0.85,
            )

            if modo_mapa == "Estado del error":
                agg = df_err_nacional.groupby("ID_PROV").size().reset_index(name="MCPs con error")
                if agg.empty:
                    st.info("No hay MCPs para esta selección.")
                else:
                    # Escala Blues recortada (sin los tonos casi blancos) para
                    # que hasta las provincias con pocos errores se vean con
                    # un azul reconocible en vez de quedar lavadas.
                    fig_map = px.choropleth_map(
                        agg, color="MCPs con error",
                        color_continuous_scale=px.colors.sequential.Blues[3:],
                        title="MCPs con error por provincia",
                        **mapa_kwargs,
                    )
                    fig_map.update_layout(margin=dict(t=40, b=10, l=0, r=0), height=560)
                    st.plotly_chart(fig_map, width="stretch")
            else:
                # Causa predominante por provincia: para cada provincia con
                # error se cuenta cuántas MCPs caen en cada categoría de
                # causa y se colorea por la categoría más frecuente — así se
                # ven todas las causas a la vez, sin necesidad de un filtro.
                por_causa = (
                    df_err_nacional.groupby(["ID_PROV", "CAUSA_CATEGORIA"])
                    .size()
                    .reset_index(name="MCPs")
                )
                if por_causa.empty:
                    st.info("No hay MCPs para esta selección.")
                else:
                    idx_dominante = por_causa.groupby("ID_PROV")["MCPs"].idxmax()
                    agg = por_causa.loc[idx_dominante].reset_index(drop=True)
                    fig_map = px.choropleth_map(
                        agg, color="CAUSA_CATEGORIA",
                        color_discrete_map=CAUSA_CATEGORIA_COLOR_MAP,
                        hover_data={"MCPs": True},
                        title="Causa de error predominante por provincia",
                        **mapa_kwargs,
                    )
                    fig_map.update_layout(
                        margin=dict(t=40, b=10, l=0, r=0), height=560,
                        legend_title="Categoría de causa",
                    )
                    st.plotly_chart(fig_map, width="stretch")

    st.divider()

    # ── Tabla filtrable (al final, según lo pedido) ──────────────────────────
    st.subheader("Tabla de MCPs con error")
    cols_show = [
        "DEPARTAMENTO", "PROVINCIA", "DISTRITO", "MCP", "COD_MCP_RENIEC",
        "ESTADO_ERROR", "CAUSA",
        "ELECTORES_FEBRERO", "CORRECCION_ABRIL", "CORRECCION_JUNIO_1",
        "CORRECCION_JUNIO_2", "CANTIDAD_FINAL", "VARIACION_ABS",
    ]

    st.caption(f"**{len(df_filt):,}** MCPs")
    st.dataframe(con_nombres_amigables(df_filt[cols_show]).reset_index(drop=True), hide_index=True, width="stretch")

    st.download_button(
        "⬇ Descargar tabla filtrada (.xlsx)",
        data=to_excel_bytes(con_nombres_amigables(df_filt[cols_show])),
        file_name="mcp_errores.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 4 — FICHA POR MCP
# ─────────────────────────────────────────────────────────────────────────────

elif pagina == "🔍  Ficha por MCP":
    st.title("Ficha por MCP")
    st.caption("Busca una MCP para ver su trayectoria de electores por etapa y el detalle de sus errores.")

    col1, col2 = st.columns([3, 2])
    with col1:
        busqueda = st.text_input("Nombre de MCP", placeholder="Ej: CHOMZA ALTA")
    with col2:
        cod_busq = st.text_input("Código RENIEC", placeholder="Ej: 010201100")

    col3, col4 = st.columns(2)
    with col3:
        deptos_ficha = ["(Todos)"] + sorted(df["DEPARTAMENTO"].dropna().unique().tolist())
        depto_ficha_sel = st.selectbox("Departamento", deptos_ficha, key="depto_ficha")
    with col4:
        provs_ficha = ["(Todas)"]
        if depto_ficha_sel != "(Todos)":
            provs_ficha += sorted(df[df["DEPARTAMENTO"] == depto_ficha_sel]["PROVINCIA"].dropna().unique().tolist())
        prov_ficha_sel = st.selectbox(
            "Provincia", provs_ficha, key="prov_ficha", disabled=(depto_ficha_sel == "(Todos)")
        )

    def build_timeline(row: pd.Series) -> pd.DataFrame:
        """Construye el DataFrame de trayectoria con columna '_estado' para colorear."""
        etapa_val = {
            "FEBRERO": (row["ETAPA_FEBRERO"], pd.notna(row["ELECTORES_FEBRERO"])),
            "ABRIL": (row["ETAPA_ABRIL"], pd.notna(row["CORRECCION_ABRIL"])),
            "JUNIO_1": (row["ETAPA_JUNIO_1"], pd.notna(row["CORRECCION_JUNIO_1"])),
            "JUNIO_2": (row["ETAPA_JUNIO_2"], pd.notna(row["CORRECCION_JUNIO_2"])),
            "FINAL": (row["ETAPA_FINAL"], True),
        }
        registros = []
        prev = None
        for etapa in ETAPAS_ORDEN:
            valor, es_real = etapa_val[etapa]
            if pd.isna(valor):
                registros.append({
                    "Etapa": etapa, "Cantidad": "—", "Origen": "—",
                    "Estado": "⬜ Sin dato", "_estado": "SIN_CAMBIO",
                })
                continue

            origen = "Dato real" if es_real else "Arrastrado (sin cambio)"
            if prev is None:
                estado_label, estado_key = "🟢 Primera aparición", "PRIMERA"
            elif valor == prev:
                estado_label, estado_key = "⬜ Sin cambio", "SIN_CAMBIO"
            elif valor > prev:
                estado_label, estado_key = "🟢 Subió", "SUBIO"
            else:
                estado_label, estado_key = "🟠 Bajó", "BAJO"

            registros.append({
                "Etapa": etapa,
                "Cantidad": str(int(valor)),
                "Origen": origen,
                "Estado": estado_label,
                "_estado": estado_key,
            })
            prev = valor

        return pd.DataFrame(registros)

    def style_timeline(df_tl: pd.DataFrame):
        """Aplica color de fondo a cada fila según el estado."""
        # Capturar la serie _estado ANTES de hacer drop (row.name = índice de fila)
        estado_series = df_tl["_estado"].copy()
        display_df = df_tl.drop(columns=["_estado"])

        def row_color(row):
            bg = TIMELINE_COLORES.get(estado_series.loc[row.name], "#FFFFFF")
            return [f"background-color: {bg}"] * len(row)

        return display_df.style.apply(row_color, axis=1)

    hay_filtro_geo = depto_ficha_sel != "(Todos)"
    if busqueda or cod_busq or hay_filtro_geo:
        mask = pd.Series([True] * len(df))
        if busqueda:
            mask &= df["MCP"].str.contains(busqueda.strip().upper(), na=False, regex=False)
        if cod_busq:
            mask &= df["COD_MCP_RENIEC"].fillna("").str.contains(cod_busq.strip(), regex=False)
        if depto_ficha_sel != "(Todos)":
            mask &= df["DEPARTAMENTO"] == depto_ficha_sel
            if prov_ficha_sel != "(Todas)":
                mask &= df["PROVINCIA"] == prov_ficha_sel

        resultados = df[mask]

        if len(resultados) == 0:
            st.warning("No se encontraron MCPs con esos criterios.")
        else:
            st.success(f"**{len(resultados)}** resultado(s)")

            for _, row in resultados.iterrows():
                estado = row["ESTADO_ERROR"]
                badge = {"SIN ERROR": "🟢", "SUBSANADO": "🔵", "PENDIENTE": "🔴"}[estado]
                label = (
                    f"{badge}  {row['DEPARTAMENTO']} › {row['PROVINCIA']} › "
                    f"**{row['MCP']}** — `{estado}`"
                )

                with st.expander(label, expanded=(len(resultados) == 1)):
                    cA, cB, cC = st.columns(3)
                    cA.metric("Electores finales", f"{int(row['CANTIDAD_FINAL']):,}")
                    variacion = row["VARIACION_ABS"]
                    cB.metric(
                        "Variación vs. febrero",
                        f"{int(variacion):,}" if pd.notna(variacion) else "—",
                        f"{row['VARIACION_PCT']:.1f} %" if pd.notna(row["VARIACION_PCT"]) else None,
                    )
                    cC.metric("Estado de error", estado)

                    if estado != "SIN ERROR":
                        st.warning(f"⚠️ **Causa:** {row['CAUSA']}")
                        if estado == "PENDIENTE" and pd.notna(row["ERROR_PENDIENTE_DETALLE"]):
                            st.write(f"**Detalle:** {row['ERROR_PENDIENTE_DETALLE']}")

                    st.markdown("##### Trayectoria de electores por etapa")
                    df_mcp_evol = df_evolucion[df_evolucion["COD_MCP_RENIEC"] == row["COD_MCP_RENIEC"]]
                    if len(df_mcp_evol) > 0:
                        fig_tl = px.line(
                            df_mcp_evol.sort_values("ETAPA"), x="ETAPA", y="CANTIDAD",
                            markers=True, symbol="ES_ESTIMADO",
                            symbol_map={True: "circle-open", False: "circle"},
                            category_orders={"ETAPA": ETAPAS_ORDEN},
                        )
                        fig_tl.update_traces(line_color=RENIEC_AZUL)
                        fig_tl.update_layout(
                            margin=dict(t=10, b=10), height=280,
                            showlegend=False, xaxis_title="Etapa", yaxis_title="Electores",
                        )
                        st.plotly_chart(fig_tl, width="stretch")

                    df_tl = build_timeline(row)
                    st.dataframe(style_timeline(df_tl), hide_index=True, width="stretch")

    else:
        st.info("Ingresa un nombre, código, o elige un departamento/provincia para buscar.")

        st.subheader("MCPs con error pendiente — muestra")
        sample = (
            df[df["ESTADO_ERROR"] == "PENDIENTE"][
                ["DEPARTAMENTO", "PROVINCIA", "MCP", "COD_MCP_RENIEC",
                 "CANTIDAD_FINAL", "CAUSA"]
            ]
            .sort_values(["DEPARTAMENTO", "MCP"])
            .head(30)
            .reset_index(drop=True)
        )
        st.dataframe(con_nombres_amigables(sample), hide_index=True, width="stretch")

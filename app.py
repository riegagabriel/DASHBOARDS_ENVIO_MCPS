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
Pipeline de limpieza y consolidación: ver notebooks/AUDITORIA_MODIFICACIONES.ipynb
(misma lógica, documentada paso a paso con etapas numeradas).

Ejecutar: streamlit run app.py
Documentación completa: ver README.md en la raíz del proyecto.
"""

import io
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

DATABASES_DIR = Path(__file__).parent / "DATABASES"
PADRON_PATH = DATABASES_DIR / "PADRON_MODIFICACIONES_VF.xlsx"

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


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="MCP")
    return buf.getvalue()


ESTADO_COLOR_MAP = {
    "SIN ERROR": "#27AE60",
    "SUBSANADO": "#2980B9",
    "PENDIENTE": "#E74C3C",
}

TIMELINE_COLORES = {
    "PRIMERA": "#D5E8D4",
    "SIN_CAMBIO": "#EAEAEA",
    "SUBIO": "#D5E8D4",
    "BAJO": "#FFE6CC",
}

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
    c2.metric("Electores (final)", f"{electores_finales:,}")
    c3.metric("Errores subsanados", f"{n_subsanados:,}", f"{pct_subsanados:.1f} %")
    c4.metric("Errores pendientes", f"{n_pendientes:,}", f"−{pct_pendientes:.1f} %", delta_color="inverse")
    c5.metric("MCPs nuevas (post-febrero)", f"{n_nuevas:,}")

    st.divider()

    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.subheader("Evolución del padrón — total de electores por etapa")
        evol_total = (
            df_evolucion.groupby("ETAPA", observed=True)["CANTIDAD"]
            .sum()
            .reindex(ETAPAS_ORDEN)
            .reset_index(name="Total de electores")
        )
        fig_evol = px.line(
            evol_total, x="ETAPA", y="Total de electores",
            markers=True, text="Total de electores",
        )
        fig_evol.update_traces(texttemplate="%{text:,}", textposition="top center")
        fig_evol.update_layout(margin=dict(t=10, b=10), yaxis_title="Electores", xaxis_title="Etapa")
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
    st.subheader("Top causas de error subsanado")
    causas = (
        df[df["ESTADO_ERROR"] == "SUBSANADO"]
        .groupby("CAUSA")
        .size()
        .reset_index(name="Cantidad")
        .sort_values("Cantidad", ascending=False)
    )
    causas["% del total subsanado"] = (causas["Cantidad"] / n_subsanados * 100).map("{:.1f} %".format)
    st.dataframe(causas, hide_index=True, width="stretch")


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 2 — EVOLUCIÓN DE ELECTORES
# ─────────────────────────────────────────────────────────────────────────────

elif pagina == "📈  Evolución de electores":
    st.title("Evolución de electores")

    st.subheader("Actividad por ronda de corrección")
    st.caption("Cuántas MCPs tuvieron un dato reportado explícitamente en cada ronda (no arrastrado).")
    actividad = pd.DataFrame({
        "Ronda": ["FEBRERO", "ABRIL", "JUNIO_1", "JUNIO_2"],
        "MCPs corregidas": [
            df["ELECTORES_FEBRERO"].notna().sum(),
            df["CORRECCION_ABRIL"].notna().sum(),
            df["CORRECCION_JUNIO_1"].notna().sum(),
            df["CORRECCION_JUNIO_2"].notna().sum(),
        ],
    })
    fig_act = px.bar(
        actividad, x="Ronda", y="MCPs corregidas", text="MCPs corregidas",
        color="MCPs corregidas", color_continuous_scale="Blues",
    )
    fig_act.update_traces(textposition="outside")
    fig_act.update_layout(coloraxis_showscale=False, margin=dict(t=10, b=10))
    st.plotly_chart(fig_act, width="stretch")

    st.divider()

    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Top 20 MCPs por variación absoluta (Feb. → Final)")
        variadas = (
            df[df["VARIACION_ABS"].notna()]
            .assign(Signo=lambda d: np.where(d["VARIACION_ABS"] >= 0, "Subió", "Bajó"))
            .reindex(df["VARIACION_ABS"].abs().sort_values(ascending=False).index)
            .head(20)
        )
        fig_var = px.bar(
            variadas, x="VARIACION_ABS", y="MCP", orientation="h",
            color="Signo", color_discrete_map={"Subió": "#27AE60", "Bajó": "#E74C3C"},
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
        dist_correcciones = (
            df["N_CORRECCIONES"].value_counts().sort_index().reset_index()
        )
        dist_correcciones.columns = ["Nº de correcciones", "Cantidad de MCPs"]
        fig_ncorr = px.bar(
            dist_correcciones, x="Nº de correcciones", y="Cantidad de MCPs",
            text="Cantidad de MCPs", color="Cantidad de MCPs",
            color_continuous_scale="Purples",
        )
        fig_ncorr.update_traces(textposition="outside")
        fig_ncorr.update_layout(
            coloraxis_showscale=False, margin=dict(t=10, b=10),
            xaxis=dict(tickmode="linear", dtick=1), height=500,
        )
        st.plotly_chart(fig_ncorr, width="stretch")

    st.divider()

    st.subheader("Tabla de correcciones por MCP")
    deptos_e = ["(Todos)"] + sorted(df["DEPARTAMENTO"].dropna().unique().tolist())
    depto_e_sel = st.selectbox("Departamento", deptos_e, key="depto_evol")

    df_tabla = df.copy()
    if depto_e_sel != "(Todos)":
        df_tabla = df_tabla[df_tabla["DEPARTAMENTO"] == depto_e_sel]

    cols_evol = [
        "DEPARTAMENTO", "PROVINCIA", "DISTRITO", "MCP", "COD_MCP_RENIEC",
        "ELECTORES_FEBRERO", "CORRECCION_ABRIL", "CORRECCION_JUNIO_1",
        "CORRECCION_JUNIO_2", "CANTIDAD_FINAL", "VARIACION_ABS", "VARIACION_PCT",
        "N_CORRECCIONES",
    ]
    st.caption(f"**{len(df_tabla):,}** MCPs")
    st.dataframe(df_tabla[cols_evol].reset_index(drop=True), hide_index=True, width="stretch")

    st.download_button(
        "⬇ Descargar tabla (.xlsx)",
        data=to_excel_bytes(df_tabla[cols_evol]),
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
        with col2:
            estado_sel = st.multiselect("Estado del error", ["SUBSANADO", "PENDIENTE"], default=[])
        with col3:
            causa_sel = st.multiselect("Causa", causas_disp, default=[])
        solo_error = st.checkbox("Solo MCPs con error", value=True)

    df_filt = df.copy()
    if depto_sel != "(Todos)":
        df_filt = df_filt[df_filt["DEPARTAMENTO"] == depto_sel]
        provs = ["(Todas)"] + sorted(df_filt["PROVINCIA"].dropna().unique().tolist())
        prov_sel = st.selectbox("Provincia", provs, key="prov_err")
        if prov_sel != "(Todas)":
            df_filt = df_filt[df_filt["PROVINCIA"] == prov_sel]

    if estado_sel:
        df_filt = df_filt[df_filt["ESTADO_ERROR"].isin(estado_sel)]
    if causa_sel:
        df_filt = df_filt[df_filt["CAUSA"].isin(causa_sel)]
    if solo_error:
        df_filt = df_filt[df_filt["ESTADO_ERROR"] != "SIN ERROR"]

    cols_show = [
        "DEPARTAMENTO", "PROVINCIA", "DISTRITO", "MCP", "COD_MCP_RENIEC",
        "ESTADO_ERROR", "CAUSA",
        "ELECTORES_FEBRERO", "CORRECCION_ABRIL", "CORRECCION_JUNIO_1",
        "CORRECCION_JUNIO_2", "CANTIDAD_FINAL", "VARIACION_ABS",
    ]

    st.caption(f"**{len(df_filt):,}** MCPs")
    st.dataframe(df_filt[cols_show].reset_index(drop=True), hide_index=True, width="stretch")

    st.download_button(
        "⬇ Descargar tabla filtrada (.xlsx)",
        data=to_excel_bytes(df_filt[cols_show]),
        file_name="mcp_errores.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.divider()

    st.subheader("Errores por tipo × departamento (Top 20)")
    heat_data = (
        df[df["ESTADO_ERROR"] != "SIN ERROR"]
        .groupby(["DEPARTAMENTO", "ESTADO_ERROR"])
        .size()
        .reset_index(name="n")
    )
    top_deptos = heat_data.groupby("DEPARTAMENTO")["n"].sum().nlargest(20).index.tolist()
    fig_stk = px.bar(
        heat_data[heat_data["DEPARTAMENTO"].isin(top_deptos)],
        x="DEPARTAMENTO", y="n",
        color="ESTADO_ERROR", barmode="stack",
        color_discrete_map=ESTADO_COLOR_MAP,
        labels={"n": "MCPs", "DEPARTAMENTO": "Departamento", "ESTADO_ERROR": "Estado"},
    )
    fig_stk.update_layout(xaxis_tickangle=-40, margin=dict(t=10, b=90), legend_title="Estado del error")
    st.plotly_chart(fig_stk, width="stretch")

    st.divider()

    # ── Treemap: distribución geográfica de errores ─────────────────────────
    st.subheader("Mapa de calor geográfico — MCPs con error")
    st.caption(
        "Haz clic en un departamento para explorar sus provincias y MCPs individuales. "
        "El color indica si el error está subsanado o pendiente."
    )
    # px.treemap exige que cada fila del `path` termine en una hoja real.
    # Si DEPARTAMENTO/PROVINCIA/MCP viene NaN o "" en alguna fila, Plotly la
    # trata como un nodo intermedio duplicado y falla con
    # "ValueError: ... is not a leaf". Por eso se filtra antes de graficar
    # en vez de dejar que Plotly infiera la jerarquía.
    df_tree = (
        df[df["ESTADO_ERROR"] != "SIN ERROR"][
            ["DEPARTAMENTO", "PROVINCIA", "MCP", "ESTADO_ERROR", "CAUSA"]
        ]
        .dropna(subset=["DEPARTAMENTO", "PROVINCIA", "MCP"])
        .copy()
    )
    df_tree = df_tree[
        (df_tree["DEPARTAMENTO"] != "") &
        (df_tree["PROVINCIA"] != "") &
        (df_tree["MCP"] != "")
    ]
    df_tree["_n"] = 1

    fig_tree = px.treemap(
        df_tree,
        path=["DEPARTAMENTO", "PROVINCIA", "MCP"],
        values="_n",
        color="ESTADO_ERROR",
        color_discrete_map=ESTADO_COLOR_MAP,
        hover_data={"ESTADO_ERROR": True, "CAUSA": True, "_n": False},
    )
    fig_tree.update_traces(textinfo="label")
    fig_tree.update_layout(margin=dict(t=10, b=10), height=560, legend_title="Estado del error")
    st.plotly_chart(fig_tree, width="stretch")

    st.divider()

    # ── Treemap secundario por causa ─────────────────────────────────────────
    st.subheader("Mapa de calor por causa del error")
    st.caption("Mismo universo de MCPs con error, coloreado por causa (causas con menos de 3 casos se agrupan en 'OTRAS').")

    causa_counts = df_tree["CAUSA"].value_counts()
    causas_chicas = causa_counts[causa_counts < 3].index
    df_tree_causa = df_tree.copy()
    df_tree_causa["CAUSA"] = df_tree_causa["CAUSA"].apply(
        lambda c: "OTRAS" if c in causas_chicas or pd.isna(c) else c
    )

    fig_tree_causa = px.treemap(
        df_tree_causa,
        path=["DEPARTAMENTO", "PROVINCIA", "MCP"],
        values="_n",
        color="CAUSA",
        hover_data={"CAUSA": True, "_n": False},
    )
    fig_tree_causa.update_traces(textinfo="label")
    fig_tree_causa.update_layout(margin=dict(t=10, b=10), height=560, legend_title="Causa")
    st.plotly_chart(fig_tree_causa, width="stretch")


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

    if busqueda or cod_busq:
        mask = pd.Series([True] * len(df))
        if busqueda:
            mask &= df["MCP"].str.contains(busqueda.strip().upper(), na=False, regex=False)
        if cod_busq:
            mask &= df["COD_MCP_RENIEC"].fillna("").str.contains(cod_busq.strip(), regex=False)

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
                        )
                        fig_tl.update_layout(
                            margin=dict(t=10, b=10), height=280,
                            showlegend=False, xaxis_title="Etapa", yaxis_title="Electores",
                        )
                        st.plotly_chart(fig_tl, width="stretch")

                    df_tl = build_timeline(row)
                    st.dataframe(style_timeline(df_tl), hide_index=True, width="stretch")

    else:
        st.info("Ingresa un nombre o código para buscar.")

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
        st.dataframe(sample, hide_index=True, width="stretch")

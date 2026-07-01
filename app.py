"""
Dashboard MCP — Auditoría de envíos del padrón de MCPs
Ejecutar: streamlit run app.py
"""

import io
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

DATABASES_DIR = Path(__file__).parent / "DATABASES"

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
    if pd.isna(x):
        return np.nan
    s = str(x).strip()
    s = unescape_excel_xml(s)
    s = fix_garbled(s)
    s = s.upper()
    s = unicodedata.normalize("NFC", s)
    return s


def normalize_cod(x) -> str:
    if pd.isna(x):
        return pd.NA
    try:
        return str(int(float(x))).zfill(9)
    except (ValueError, TypeError):
        return pd.NA


# ─────────────────────────────────────────────────────────────────────────────
# CARGA Y CONSOLIDACIÓN DE DATOS
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_data():
    archivos = sorted(DATABASES_DIR.glob("*.xlsx"))
    if not archivos:
        st.error(f"No se encontraron archivos .xlsx en {DATABASES_DIR}")
        st.stop()

    lista = []
    for i, archivo in enumerate(archivos, start=1):
        envio = f"{i:02d}"
        df = pd.read_excel(archivo)
        if "COD_MCP_RENIEC" in df.columns:
            df["COD_MCP_RENIEC"] = df["COD_MCP_RENIEC"].apply(normalize_cod)
        if "COUNT(1)" in df.columns:
            df = df.rename(columns={"COUNT(1)": "CANTIDAD"})
        for col in ["DEPARTAMENTO", "PROVINCIA", "DISTRITO", "MCP"]:
            if col in df.columns:
                df[col] = df[col].apply(normalize_text)
        df["ENVIO"] = envio
        df["ARCHIVO"] = archivo.stem
        lista.append(df)

    df_long = pd.concat(lista, ignore_index=True)

    base = (
        df_long[["DEPARTAMENTO", "PROVINCIA", "MCP"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    df_cons = base.copy()
    envios = sorted(df_long["ENVIO"].unique())

    for envio in envios:
        aux = (
            df_long[df_long["ENVIO"] == envio][
                ["DEPARTAMENTO", "PROVINCIA", "MCP", "COD_MCP_RENIEC", "DISTRITO", "CANTIDAD"]
            ].rename(columns={
                "COD_MCP_RENIEC": f"COD_{envio}",
                "DISTRITO": f"DIST_{envio}",
                "CANTIDAD": f"CANT_{envio}",
            })
        )
        df_cons = df_cons.merge(aux, how="left", on=["DEPARTAMENTO", "PROVINCIA", "MCP"])

    cod_cols  = sorted([c for c in df_cons.columns if c.startswith("COD_")])
    dist_cols = sorted([c for c in df_cons.columns if c.startswith("DIST_")])

    df_cons["COD_ACTUAL"]     = df_cons[cod_cols].ffill(axis=1).iloc[:, -1]
    df_cons["DIST_ACTUAL"]    = df_cons[dist_cols].ffill(axis=1).iloc[:, -1]
    df_cons["N_ENVIOS"]       = df_cons[cod_cols].notna().sum(axis=1)
    df_cons["CAMBIO_CODIGO"]  = df_cons[cod_cols].nunique(axis=1, dropna=True)
    df_cons["CAMBIO_DISTRITO"]= df_cons[dist_cols].nunique(axis=1, dropna=True)

    homonimos = (
        df_long.groupby(["DEPARTAMENTO", "MCP"])
        .agg(
            N_PROVINCIAS=("PROVINCIA", "nunique"),
            PROVINCIAS=("PROVINCIA", lambda x: " | ".join(sorted(set(x)))),
        )
        .reset_index()
    )
    df_cons = df_cons.merge(homonimos, on=["DEPARTAMENTO", "MCP"], how="left")

    rev_cod = (
        df_long.groupby(["DEPARTAMENTO", "PROVINCIA", "MCP"])
        .agg(
            N_CODIGOS=("COD_MCP_RENIEC", "nunique"),
            CODIGOS=("COD_MCP_RENIEC", lambda x: " | ".join(sorted(set(x.dropna())))),
        )
        .reset_index()
    )
    df_cons = df_cons.merge(rev_cod, on=["DEPARTAMENTO", "PROVINCIA", "MCP"], how="left")

    rev_dist = (
        df_long.groupby(["DEPARTAMENTO", "PROVINCIA", "MCP"])
        .agg(
            N_DISTRITOS=("DISTRITO", "nunique"),
            DISTRITOS=("DISTRITO", lambda x: " | ".join(sorted(set(x.dropna())))),
        )
        .reset_index()
    )
    df_cons = df_cons.merge(rev_dist, on=["DEPARTAMENTO", "PROVINCIA", "MCP"], how="left")

    def observaciones(row):
        obs = []
        if row["N_PROVINCIAS"] > 1:
            obs.append("HOMÓNIMO")
        if row["N_CODIGOS"] > 1:
            obs.append("CAMBIO CÓDIGO")
        if row["CAMBIO_DISTRITO"] > 1:
            obs.append("CAMBIO DISTRITO")
        return " + ".join(obs) if obs else "OK"

    df_cons["OBSERVACIONES"]     = df_cons.apply(observaciones, axis=1)
    df_cons["REQUIERE_REVISION"] = df_cons["OBSERVACIONES"] != "OK"

    return df_long, df_cons


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="MCP")
    return buf.getvalue()


COLOR_MAP = {
    "OK":                                       "#27AE60",
    "HOMÓNIMO":                                 "#F39C12",
    "CAMBIO CÓDIGO":                            "#E74C3C",
    "CAMBIO DISTRITO":                          "#2980B9",
    "CAMBIO CÓDIGO + CAMBIO DISTRITO":          "#8E44AD",
    "HOMÓNIMO + CAMBIO CÓDIGO":                 "#C0392B",
    "HOMÓNIMO + CAMBIO DISTRITO":               "#E67E22",
    "HOMÓNIMO + CAMBIO CÓDIGO + CAMBIO DISTRITO": "#922B21",
}

# colores para el timeline por MCP
ESTADO_COLORES = {
    "AUSENTE":  "#F0F0F0",
    "PRIMERA":  "#D5E8D4",
    "OK":       "#D5E8D4",
    "CODIGO":   "#F8CECC",
    "DISTRITO": "#FFE6CC",
    "AMBOS":    "#F4CCCC",
}

ENVIO_LABELS = {
    "01": "REPORTE_MCP_24022026_01",
    "02": "RESUMEN_17062026_1",
    "03": "RESUMEN_3",
    "04": "RESUMEN_4_5_6",
    "05": "RESUMEN_SEGUNDO_GRUPO",
    "06": "RESUMEN_SEGUNDO_GRUPO_18062026_2",
    "07": "RESUMEN_TAYACAJA",
    "08": "RESUMEN_TERCER_GRUPO",
}

# ─────────────────────────────────────────────────────────────────────────────
# CARGA
# ─────────────────────────────────────────────────────────────────────────────

with st.spinner("Cargando datos..."):
    df_long, df_cons = load_data()

n_envios = df_long["ENVIO"].nunique()

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────

st.sidebar.title("📋 Dashboard MCP")
st.sidebar.caption("Auditoría del padrón de MCPs — RENIEC")
st.sidebar.divider()

pagina = st.sidebar.radio(
    "Vista",
    [
        "🏠  Resumen ejecutivo",
        "📦  Análisis por envío",
        "⚠️  Mapa de problemas",
        "🔍  Historial por MCP",
    ],
)

st.sidebar.divider()
st.sidebar.caption(
    f"**{len(df_long):,}** registros · **{len(df_cons):,}** MCPs únicas · **{n_envios}** envíos"
)

# ─────────────────────────────────────────────────────────────────────────────
# VISTA 1 — RESUMEN EJECUTIVO
# ─────────────────────────────────────────────────────────────────────────────

if pagina == "🏠  Resumen ejecutivo":
    st.title("Resumen ejecutivo")

    total    = len(df_cons)
    con_prob = int(df_cons["REQUIERE_REVISION"].sum())
    ok       = total - con_prob
    pct_prob = con_prob / total * 100

    # ── KPIs ────────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("MCPs únicas", f"{total:,}")
    c2.metric("Sin incidencias", f"{ok:,}", f"{100 - pct_prob:.1f} %")
    c3.metric("Con incidencias", f"{con_prob:,}", f"−{pct_prob:.1f} %", delta_color="inverse")
    c4.metric("Envíos analizados", str(n_envios))

    st.divider()

    # ── Dos barras: Departamentos + Provincias ───────────────────────────────
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Top 15 departamentos con incidencias")
        dept_prob = (
            df_cons[df_cons["REQUIERE_REVISION"]]
            .groupby("DEPARTAMENTO")
            .size()
            .nlargest(15)
            .reset_index(name="MCPs con incidencias")
        )
        fig_dept = px.bar(
            dept_prob,
            x="MCPs con incidencias",
            y="DEPARTAMENTO",
            orientation="h",
            color="MCPs con incidencias",
            color_continuous_scale="Reds",
            text="MCPs con incidencias",
        )
        fig_dept.update_traces(textposition="outside")
        fig_dept.update_layout(
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False,
            margin=dict(t=10, b=10, l=10, r=40),
            height=420,
        )
        st.plotly_chart(fig_dept, use_container_width=True)

    with col_r:
        st.subheader("Top 15 provincias con incidencias")
        prov_prob = (
            df_cons[df_cons["REQUIERE_REVISION"]]
            .groupby(["DEPARTAMENTO", "PROVINCIA"])
            .size()
            .nlargest(15)
            .reset_index(name="MCPs con incidencias")
        )
        prov_prob["Provincia (Dept.)"] = (
            prov_prob["PROVINCIA"] + "  (" + prov_prob["DEPARTAMENTO"] + ")"
        )
        fig_prov = px.bar(
            prov_prob,
            x="MCPs con incidencias",
            y="Provincia (Dept.)",
            orientation="h",
            color="MCPs con incidencias",
            color_continuous_scale="Oranges",
            text="MCPs con incidencias",
        )
        fig_prov.update_traces(textposition="outside")
        fig_prov.update_layout(
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False,
            margin=dict(t=10, b=10, l=10, r=40),
            height=420,
        )
        st.plotly_chart(fig_prov, use_container_width=True)

    st.divider()
    st.subheader("Resumen por tipo de incidencia")
    resumen = (
        df_cons.groupby("OBSERVACIONES")
        .size()
        .reset_index(name="Cantidad")
        .sort_values("Cantidad", ascending=False)
    )
    resumen["% del total"] = (resumen["Cantidad"] / total * 100).map("{:.1f} %".format)
    st.dataframe(resumen, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 2 — ANÁLISIS POR ENVÍO
# ─────────────────────────────────────────────────────────────────────────────

elif pagina == "📦  Análisis por envío":
    st.title("Análisis por envío")

    envio_stats = (
        df_long.groupby("ENVIO")
        .agg(
            Archivo=("ARCHIVO", "first"),
            MCPs=("COD_MCP_RENIEC", "nunique"),
            Filas=("COD_MCP_RENIEC", "count"),
            Departamentos=("DEPARTAMENTO", "nunique"),
            Provincias=("PROVINCIA", "nunique"),
            Sin_codigo=("COD_MCP_RENIEC", lambda x: x.isna().sum()),
        )
        .reset_index()
        .rename(columns={"ENVIO": "Envío", "Sin_codigo": "Sin código"})
    )

    # MCPs nuevas (no vistas en envíos anteriores)
    mcp_key = df_long[["ENVIO", "DEPARTAMENTO", "PROVINCIA", "MCP"]].drop_duplicates()
    mcp_key = mcp_key.sort_values("ENVIO")
    vistas = set()
    nuevas_por_envio = {}
    for envio, grupo in mcp_key.groupby("ENVIO"):
        keys = set(zip(grupo["DEPARTAMENTO"], grupo["PROVINCIA"], grupo["MCP"]))
        nuevas_por_envio[envio] = len(keys - vistas)
        vistas |= keys
    envio_stats["MCPs nuevas"] = envio_stats["Envío"].map(nuevas_por_envio)
    envio_stats["MCPs repetidas"] = envio_stats["MCPs"] - envio_stats["MCPs nuevas"]

    st.subheader("Estadísticas por envío")
    st.dataframe(envio_stats, hide_index=True, use_container_width=True)

    st.divider()

    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("MCPs por envío — nuevas vs. repetidas")
        fig_new = px.bar(
            envio_stats.melt(
                id_vars="Envío",
                value_vars=["MCPs nuevas", "MCPs repetidas"],
                var_name="Tipo", value_name="Cantidad"
            ),
            x="Envío", y="Cantidad", color="Tipo",
            barmode="stack", text="Cantidad",
            color_discrete_map={"MCPs nuevas": "#2980B9", "MCPs repetidas": "#BDC3C7"},
        )
        fig_new.update_traces(textposition="inside")
        fig_new.update_layout(margin=dict(t=10, b=10), legend_title="")
        st.plotly_chart(fig_new, use_container_width=True)

    with col_r:
        st.subheader("Cobertura geográfica por envío")
        fig2 = px.bar(
            envio_stats.melt(
                id_vars="Envío",
                value_vars=["Departamentos", "Provincias"],
                var_name="Nivel", value_name="Cantidad"
            ),
            x="Envío", y="Cantidad", color="Nivel",
            barmode="group", text="Cantidad",
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(margin=dict(t=10, b=10))
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()

    st.subheader("¿En cuántos envíos aparece cada MCP?")
    n_envios_dist = (
        df_cons["N_ENVIOS"]
        .value_counts().sort_index()
        .reset_index()
        .rename(columns={"N_ENVIOS": "Nº de envíos en que aparece", "count": "Cantidad de MCPs"})
    )
    fig3 = px.bar(
        n_envios_dist,
        x="Nº de envíos en que aparece", y="Cantidad de MCPs",
        text="Cantidad de MCPs", color="Cantidad de MCPs",
        color_continuous_scale="Purples",
    )
    fig3.update_traces(textposition="outside")
    fig3.update_layout(
        coloraxis_showscale=False, margin=dict(t=10, b=10),
        xaxis=dict(tickmode="linear", dtick=1),
    )
    st.plotly_chart(fig3, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 3 — MAPA DE PROBLEMAS
# ─────────────────────────────────────────────────────────────────────────────

elif pagina == "⚠️  Mapa de problemas":
    st.title("Mapa de problemas")

    tipos_obs = sorted([o for o in df_cons["OBSERVACIONES"].unique() if o != "OK"])
    deptos    = ["(Todos)"] + sorted(df_cons["DEPARTAMENTO"].dropna().unique().tolist())

    with st.expander("Filtros", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            depto_sel = st.selectbox("Departamento", deptos, key="depto_prob")
        with col2:
            obs_sel = st.multiselect("Tipo de incidencia", tipos_obs, default=[])
        with col3:
            solo_revision = st.checkbox("Solo los que requieren revisión", value=True)

    df_filt = df_cons.copy()
    if depto_sel != "(Todos)":
        df_filt = df_filt[df_filt["DEPARTAMENTO"] == depto_sel]
        provs = ["(Todas)"] + sorted(df_filt["PROVINCIA"].dropna().unique().tolist())
        prov_sel = st.selectbox("Provincia", provs, key="prov_prob")
        if prov_sel != "(Todas)":
            df_filt = df_filt[df_filt["PROVINCIA"] == prov_sel]

    if obs_sel:
        df_filt = df_filt[df_filt["OBSERVACIONES"].isin(obs_sel)]
    if solo_revision:
        df_filt = df_filt[df_filt["REQUIERE_REVISION"]]

    cod_cols_e  = sorted([c for c in df_cons.columns if c.startswith("COD_") and c[4:].isdigit()])
    dist_cols_e = sorted([c for c in df_cons.columns if c.startswith("DIST_") and c[5:].isdigit()])

    cols_show = (
        ["DEPARTAMENTO", "PROVINCIA", "MCP", "OBSERVACIONES",
         "N_ENVIOS", "N_CODIGOS", "CODIGOS",
         "N_DISTRITOS", "DISTRITOS",
         "N_PROVINCIAS", "PROVINCIAS"]
        + cod_cols_e
        + dist_cols_e
    )

    st.caption(f"**{len(df_filt):,}** MCPs")
    st.dataframe(df_filt[cols_show].reset_index(drop=True), hide_index=True, use_container_width=True)

    st.download_button(
        "⬇ Descargar tabla filtrada (.xlsx)",
        data=to_excel_bytes(df_filt[cols_show]),
        file_name="mcp_problemas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.divider()

    st.subheader("Incidencias por tipo × departamento (Top 20)")
    heat_data = (
        df_cons[df_cons["REQUIERE_REVISION"]]
        .groupby(["DEPARTAMENTO", "OBSERVACIONES"])
        .size()
        .reset_index(name="n")
    )
    top_deptos = (
        heat_data.groupby("DEPARTAMENTO")["n"].sum().nlargest(20).index.tolist()
    )
    fig_stk = px.bar(
        heat_data[heat_data["DEPARTAMENTO"].isin(top_deptos)],
        x="DEPARTAMENTO", y="n",
        color="OBSERVACIONES", barmode="stack",
        color_discrete_map=COLOR_MAP,
        labels={"n": "MCPs", "DEPARTAMENTO": "Departamento"},
    )
    fig_stk.update_layout(
        xaxis_tickangle=-40, margin=dict(t=10, b=90),
        legend_title="Tipo de incidencia",
    )
    st.plotly_chart(fig_stk, use_container_width=True)

    st.divider()

    # ── Treemap: distribución geográfica de MCPs con incidencias ─────────────
    st.subheader("Mapa de calor geográfico — MCPs con incidencias")
    st.caption(
        "Haz clic en un departamento para explorar sus provincias y MCPs individuales. "
        "El color indica el tipo de incidencia de cada MCP."
    )
    df_tree = (
        df_cons[df_cons["REQUIERE_REVISION"]][
            ["DEPARTAMENTO", "PROVINCIA", "MCP", "OBSERVACIONES"]
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
        color="OBSERVACIONES",
        color_discrete_map=COLOR_MAP,
        hover_data={"OBSERVACIONES": True, "_n": False},
    )
    fig_tree.update_traces(textinfo="label")
    fig_tree.update_layout(
        margin=dict(t=10, b=10),
        height=560,
        legend_title="Tipo de incidencia",
    )
    st.plotly_chart(fig_tree, use_container_width=True)

    st.divider()

    # ── Comparador de dos envíos (treemap doble) ─────────────────────────────
    st.subheader("Comparar ubicación de MCPs entre dos envíos")
    st.caption(
        "Selecciona dos envíos para ver cómo estaban distribuidas las MCPs "
        "en cada uno. Útil para detectar cambios de provincia o distrito entre remesas."
    )

    envios_list = sorted(df_long["ENVIO"].unique())
    col_e1, col_e2 = st.columns(2)
    with col_e1:
        envio_a = st.selectbox("Envío A", envios_list, index=0, key="cmp_envio_a")
    with col_e2:
        envio_b = st.selectbox("Envío B", envios_list,
                               index=min(1, len(envios_list) - 1), key="cmp_envio_b")

    def make_envio_tree(envio_id: str):
        df_e = (
            df_long[df_long["ENVIO"] == envio_id][
                ["DEPARTAMENTO", "PROVINCIA", "MCP"]
            ]
            .drop_duplicates()
            .dropna(subset=["DEPARTAMENTO", "PROVINCIA", "MCP"])
            .merge(
                df_cons[["DEPARTAMENTO", "PROVINCIA", "MCP", "OBSERVACIONES"]],
                on=["DEPARTAMENTO", "PROVINCIA", "MCP"],
                how="left",
            )
        )
        df_e = df_e[
            (df_e["DEPARTAMENTO"] != "") &
            (df_e["PROVINCIA"] != "") &
            (df_e["MCP"] != "")
        ]
        df_e["OBSERVACIONES"] = df_e["OBSERVACIONES"].fillna("OK")
        df_e["_n"] = 1
        if df_e.empty:
            return go.Figure()
        fig = px.treemap(
            df_e,
            path=["DEPARTAMENTO", "PROVINCIA", "MCP"],
            values="_n",
            color="OBSERVACIONES",
            color_discrete_map=COLOR_MAP,
            hover_data={"OBSERVACIONES": True, "_n": False},
        )
        fig.update_traces(textinfo="label")
        fig.update_layout(
            margin=dict(t=10, b=10),
            height=480,
            showlegend=False,
        )
        return fig

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        n_a = df_long[df_long["ENVIO"] == envio_a]["MCP"].nunique()
        st.markdown(f"**Envío {envio_a}** — {n_a} MCPs")
        st.plotly_chart(make_envio_tree(envio_a), use_container_width=True, key="tree_a")
    with col_t2:
        n_b = df_long[df_long["ENVIO"] == envio_b]["MCP"].nunique()
        st.markdown(f"**Envío {envio_b}** — {n_b} MCPs")
        st.plotly_chart(make_envio_tree(envio_b), use_container_width=True, key="tree_b")


# ─────────────────────────────────────────────────────────────────────────────
# VISTA 4 — HISTORIAL POR MCP
# ─────────────────────────────────────────────────────────────────────────────

elif pagina == "🔍  Historial por MCP":
    st.title("Historial por MCP")
    st.caption(
        "Busca una MCP para ver qué se envió en cada remesa y detectar "
        "inconsistencias de código o distrito entre envíos."
    )

    col1, col2 = st.columns([3, 2])
    with col1:
        busqueda = st.text_input("Nombre de MCP", placeholder="Ej: MAURE KALLACHIRI")
    with col2:
        cod_busq = st.text_input("Código RENIEC", placeholder="Ej: 220201001")

    # ── Leyenda de colores ──────────────────────────────────────────────────
    st.markdown(
        """
        <div style="display:flex; gap:16px; font-size:0.83rem; margin-bottom:4px">
          <span style="background:#D5E8D4; padding:2px 8px; border-radius:4px">🟢 Sin cambios / Primera aparición</span>
          <span style="background:#F8CECC; padding:2px 8px; border-radius:4px">🔴 Código cambió</span>
          <span style="background:#FFE6CC; padding:2px 8px; border-radius:4px">🟠 Distrito cambió</span>
          <span style="background:#F4CCCC; padding:2px 8px; border-radius:4px">🔴🟠 Código y distrito cambiaron</span>
          <span style="background:#F0F0F0; padding:2px 8px; border-radius:4px">⬜ Ausente en ese envío</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    def build_timeline(row: pd.Series) -> pd.DataFrame:
        """Construye el DataFrame de timeline con columna 'estado_key' para colorear."""
        envios_all = sorted(df_long["ENVIO"].unique())
        prev_cod  = None
        prev_dist = None
        registros = []

        for envio in envios_all:
            sub = df_long[
                (df_long["ENVIO"] == envio)
                & (df_long["DEPARTAMENTO"] == row["DEPARTAMENTO"])
                & (df_long["PROVINCIA"]    == row["PROVINCIA"])
                & (df_long["MCP"]          == row["MCP"])
            ]

            if len(sub) == 0:
                registros.append({
                    "Envío":    envio,
                    "Código":   "—",
                    "Distrito": "—",
                    "Cantidad": "—",
                    "Estado":   "⬜ Ausente",
                    "_estado":  "AUSENTE",
                })
                continue

            sr = sub.iloc[0]
            cod  = sr["COD_MCP_RENIEC"]
            dist = sr["DISTRITO"]
            cant = sr.get("CANTIDAD", np.nan)

            if prev_cod is None:
                estado_label = "🟢 Primera aparición"
                estado_key   = "PRIMERA"
            else:
                cod_ok  = (pd.isna(cod) and pd.isna(prev_cod)) or (cod == prev_cod)
                dist_ok = (pd.isna(dist) and pd.isna(prev_dist)) or (dist == prev_dist)

                if cod_ok and dist_ok:
                    estado_label = "✅ Sin cambios"
                    estado_key   = "OK"
                elif not cod_ok and not dist_ok:
                    estado_label = "🔴🟠 Código y distrito cambiaron"
                    estado_key   = "AMBOS"
                elif not cod_ok:
                    estado_label = "🔴 Código cambió"
                    estado_key   = "CODIGO"
                else:
                    estado_label = "🟠 Distrito cambió"
                    estado_key   = "DISTRITO"

            prev_cod  = cod
            prev_dist = dist

            registros.append({
                "Envío":    envio,
                "Código":   cod if pd.notna(cod) else "—",
                "Distrito": dist if pd.notna(dist) else "—",
                "Cantidad": int(cant) if pd.notna(cant) else "—",
                "Estado":   estado_label,
                "_estado":  estado_key,
            })

        return pd.DataFrame(registros)

    def style_timeline(df_tl: pd.DataFrame):
        """Aplica color de fondo a cada fila según el estado."""
        # Capturar la serie _estado ANTES de hacer drop (row.name = índice de fila)
        estado_series = df_tl["_estado"].copy()
        display_df = df_tl.drop(columns=["_estado"])

        def row_color(row):
            bg = ESTADO_COLORES.get(estado_series.loc[row.name], "#FFFFFF")
            return [f"background-color: {bg}"] * len(row)

        return display_df.style.apply(row_color, axis=1)

    # ── Búsqueda ────────────────────────────────────────────────────────────
    if busqueda or cod_busq:
        mask = pd.Series([True] * len(df_cons))
        if busqueda:
            mask &= df_cons["MCP"].str.contains(
                busqueda.strip().upper(), na=False, regex=False
            )
        if cod_busq:
            mask &= (
                df_cons["COD_ACTUAL"].fillna("").str.contains(cod_busq.strip(), regex=False)
                | df_cons["CODIGOS"].fillna("").str.contains(cod_busq.strip(), regex=False)
            )

        resultados = df_cons[mask]

        if len(resultados) == 0:
            st.warning("No se encontraron MCPs con esos criterios.")
        else:
            st.success(f"**{len(resultados)}** resultado(s)")

            for _, row in resultados.iterrows():
                obs = row["OBSERVACIONES"]
                badge = "🟢" if obs == "OK" else "🔴"
                label = (
                    f"{badge}  {row['DEPARTAMENTO']} › {row['PROVINCIA']} › "
                    f"**{row['MCP']}** — `{obs}`"
                )

                with st.expander(label, expanded=(len(resultados) == 1)):
                    cA, cB, cC = st.columns(3)
                    cA.metric("Código actual",  row["COD_ACTUAL"]  or "—")
                    cB.metric("Distrito actual", row["DIST_ACTUAL"] or "—")
                    cC.metric("Nº de envíos",    int(row["N_ENVIOS"]))

                    if obs != "OK":
                        st.warning(f"⚠️ **Incidencias detectadas:** {obs}")
                        if row["N_CODIGOS"] > 1:
                            st.write(f"**Códigos registrados a lo largo de los envíos:** {row['CODIGOS']}")
                        if row["N_PROVINCIAS"] > 1:
                            st.write(f"**Provincias homónimas encontradas:** {row['PROVINCIAS']}")

                    st.markdown("##### Trayectoria por envío")
                    df_tl = build_timeline(row)
                    st.dataframe(
                        style_timeline(df_tl),
                        hide_index=True,
                        use_container_width=True,
                    )

    else:
        st.info("Ingresa un nombre o código para buscar.")

        st.subheader("MCPs con incidencias — muestra")
        sample = (
            df_cons[df_cons["REQUIERE_REVISION"]][
                ["DEPARTAMENTO", "PROVINCIA", "MCP", "COD_ACTUAL",
                 "DIST_ACTUAL", "N_ENVIOS", "OBSERVACIONES"]
            ]
            .sort_values(["OBSERVACIONES", "DEPARTAMENTO"])
            .head(30)
            .reset_index(drop=True)
        )
        st.dataframe(sample, hide_index=True, use_container_width=True)

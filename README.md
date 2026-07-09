# Dashboard MCP — Auditoría de correcciones del padrón de MCPs (RENIEC)

## Contexto

RENIEC corrige periódicamente el conteo de electores de cada **MCP**
(Municipalidad de Centro Poblado) a lo largo de varias rondas de revisión:
un primer registro en **febrero**, y sucesivas correcciones en **abril**,
**junio (1ª ronda)** y **junio (2ª ronda)**, hasta llegar a un valor
**final consolidado**. En ese proceso se detectan errores — algunos ya
**subsanados** (con causa documentada) y otros que quedan **pendientes** de
resolución.

Este proyecto tiene dos entregables:

1. **`notebooks/AUDITORIA_MODIFICACIONES.ipynb`** — el pipeline de limpieza
   y consolidación, documentado paso a paso (8 etapas).
2. **`app.py`** — un dashboard Streamlit que visualiza cómo evolucionó el
   padrón etapa por etapa y hace seguimiento de los errores subsanados y
   pendientes.

> **Nota histórica:** una versión anterior de este dashboard auditaba **8
> envíos separados** de RENIEC (uno por archivo) buscando MCPs homónimas y
> cambios de código/distrito entre remesas. Esa fuente y ese pipeline se
> archivaron en `archive/` (ver sección "Estructura de carpetas") cuando el
> proyecto pasó a construirse enteramente sobre
> `PADRON_MODIFICACIONES_VF.xlsx`, que tiene un modelo de datos distinto
> (una fila por MCP con su historial de correcciones, no un archivo por
> envío).

---

## Estructura de carpetas

```
DASHBOARD_MCPS_PROBLEMAS/
├── README.md              ← este archivo
├── CHANGELOG.md           ← historial de sesiones, bugs y fixes
├── requirements.txt       ← dependencias con versión mínima
├── app.py                 ← dashboard Streamlit (ejecutar desde aquí)
├── DATABASES/
│   └── PADRON_MODIFICACIONES_VF.xlsx   ← fuente única del dashboard (no modificar)
├── geofiles/
│   ├── DEPARTAMENTO.gpkg  ← límites departamentales (no usado aún en la UI)
│   ├── PROVINCIA.gpkg     ← límites provinciales, usado por el mapa de coropletas
│   └── DISTRITO.gpkg      ← límites distritales (no usado aún en la UI)
├── .streamlit/
│   └── config.toml        ← tema visual (azul institucional RENIEC #002F56)
├── notebooks/
│   └── AUDITORIA_MODIFICACIONES.ipynb  ← pipeline de limpieza/consolidación documentado
├── outputs/               ← df_padron.xlsx, df_evolucion.xlsx, df_errores.xlsx
│                             (generados por el notebook, no versionar a mano)
├── docs/
│   └── MD_PREVIO.md       ← documento original de la primera sesión de trabajo
└── archive/
    ├── DATABASES_ENVIOS_HISTORICOS/   ← los 8 Excel de envíos (versión anterior
    │                                     del dashboard, ya no es la fuente activa)
    ├── AUDITORIA_MCPS_ENVIOS.ipynb    ← notebook de la versión anterior (8 envíos)
    ├── outputs_envios/                ← outputs de la versión anterior
    └── NOTEBOOK_REVISION_EXCEL_MCPS_FINAL_REVISION.ipynb
                            ← notebook de OTRO proyecto (EXCEL_NIVEL_MCP) que
                              apareció mezclado en esta carpeta; se archivó
                              aquí sin borrarlo por si se copió a propósito
```

`DATABASES/` y `app.py` permanecen en la raíz porque el código de `app.py`
resuelve la ruta con `Path(__file__).parent / "DATABASES"` — si se mueve
`app.py`, hay que mover `DATABASES/` con él o ajustar esa línea.

---

## Cómo ejecutar

```bash
pip install -r requirements.txt
streamlit run app.py
```

Para volver a generar los archivos de `outputs/` desde cero (por ejemplo tras
actualizar `PADRON_MODIFICACIONES_VF.xlsx`), ejecutar todas las celdas de
`notebooks/AUDITORIA_MODIFICACIONES.ipynb`. Las rutas del notebook son
relativas a su propia ubicación (`notebooks/`), no al directorio desde donde
se lance Jupyter, así que funciona sin importar la herramienta usada para
abrirlo.

---

## Pipeline de limpieza de datos

El problema más grave detectado fue **mojibake**: varios archivos tienen
texto UTF-8 mal interpretado como cp1252 (ej. `Ñ` aparece como `Ã'`). El fix
tiene 4 pasos que **deben aplicarse en este orden exacto**:

| Paso | Función | Por qué |
|------|---------|---------|
| 1 | `str.strip()` | quitar espacios laterales |
| 2 | `unescape_excel_xml()` | openpyxl serializa bytes cp1252 indefinidos (ej. U+0081) como el literal `_x0081_` en el XML — hay que revertirlo antes de tocar el mojibake |
| 3 | `fix_garbled()` | repara los bytes UTF-8 malinterpretados como cp1252. **Debe ir antes de `.upper()`**: mayusculizar cambia el byte cp1252 de algunos caracteres (`š`0x9A → `Š`0x8A), lo que hace que el mismo byte decodifique distinto en UTF-8 (`Ú` en vez de `Ê`) |
| 4 | `.upper()` + `unicodedata.normalize("NFC", ...)` | normalización final |

Se agregó una variante, `normalize_text_preserve_case()` (mismos pasos 1-3,
sin `.upper()`), para las columnas de texto narrativo (`CAUSA_ERROR_SUBSANADO`,
`ERROR_PENDIENTE_CAUSA`, `ERROR_PENDIENTE_DETALLE`): son texto pensado para
lectura humana, y mayusculizarlas las haría ilegibles.

Las funciones viven duplicadas — una copia en `app.py`, otra en el
notebook — porque cada uno debe poder ejecutarse de forma independiente sin
importar del otro. Si se corrige el pipeline, **corregir en ambos lugares**.

---

## Esquema de datos

Fuente: `DATABASES/PADRON_MODIFICACIONES_VF.xlsx`, hoja `Hoja1`, **una fila
por MCP** (3388 filas), con 5 etapas de conteo de electores y metadata de
errores.

### `df` — una fila por MCP única

Columnas originales relevantes: `COD_MCP_RENIEC, UBIGEO, DEPARTAMENTO,
PROVINCIA, DISTRITO, MCP, ELECTORES_FEBRERO, CORRECCION_ABRIL,
CORRECCION_JUNIO_1, CORRECCION_JUNIO_2, CANTIDAD_FINAL,
DISTRITO_CON_ERROR_SUBSANADO, CAUSA_ERROR_SUBSANADO, ERROR_PENDIENTE,
ERROR_PENDIENTE_CAUSA, ERROR_PENDIENTE_DETALLE`.

> `CORRECION_2_ENVIADA` se lee pero **no se usa** en la UI: solo 15/3388
> filas tienen dato (siempre `"NO"`), cobertura insuficiente para aportar
> señal.

Las columnas de corrección crudas (`CORRECCION_ABRIL/JUNIO_1/JUNIO_2`) solo
tienen valor cuando esa ronda tocó la MCP — el resto es `NaN`, que **no**
significa "sin electores" sino "sigue vigente el último valor conocido". Se
resuelve con **forward-fill horizontal** en orden temporal, generando las
columnas "efectivas":

- `ETAPA_FEBRERO`, `ETAPA_ABRIL`, `ETAPA_JUNIO_1`, `ETAPA_JUNIO_2`: ffill de
  las columnas crudas correspondientes.
- `ETAPA_FINAL`: se fija siempre a `CANTIDAD_FINAL` (no se deriva por
  ffill) — es el valor consolidado autoritativo y diverge en 2 filas del
  ffill puro, señal de ajustes manuales no capturados en las columnas de
  ronda.
- `NUEVA_POST_FEBRERO`: `True` si `ELECTORES_FEBRERO` es `NaN` (129 MCPs
  que aún no existían/no se reportaron en la primera etapa).
- `VARIACION_ABS` / `VARIACION_PCT`: `CANTIDAD_FINAL - ELECTORES_FEBRERO`
  (absoluta y porcentual; `NaN` si no hay base comparable en febrero).
- `N_CORRECCIONES`: en cuántas de las 3 rondas intermedias (abril,
  junio 1, junio 2) tuvo dato crudo no nulo la MCP.
- `ES_SUBSANADO` / `ES_PENDIENTE`: booleanos derivados de
  `DISTRITO_CON_ERROR_SUBSANADO == "SI"` y `ERROR_PENDIENTE == "SI"`.
- `ESTADO_ERROR`: `"SIN ERROR"`, `"SUBSANADO"` o `"PENDIENTE"`. Hay 8 MCPs
  marcadas con ambos flags a la vez; en ese solape **prioriza PENDIENTE**
  (un error que sigue pendiente pesa más que uno ya subsanado).
- `CAUSA`: causa unificada — toma `ERROR_PENDIENTE_CAUSA` si está pendiente,
  `CAUSA_ERROR_SUBSANADO` si está subsanado, `NaN` si no tiene error.
- `CAUSA_CATEGORIA`: agrupación de `CAUSA` en 6 categorías legibles (función
  `categorizar_causa()`, coincidencia por substring): *Error de revisión
  manual*, *GEO: omisión de listas/anexos*, *GEO: información desactualizada
  o extemporánea*, *GEO: asignación incorrecta (códigos/DNI)*, *Electores de
  otro distrito quitados*, *Otras causas puntuales*. Solo para UI — `CAUSA`
  conserva el texto original íntegro.

### `df_evolucion` — tabla larga (una fila por MCP × etapa)

Construida vía `melt` sobre las 5 columnas efectivas. Columnas:
`COD_MCP_RENIEC, UBIGEO, DEPARTAMENTO, PROVINCIA, DISTRITO, MCP, ETAPA,
CANTIDAD, ES_ESTIMADO`. `ETAPA` es categórica ordenada (`FEBRERO → ABRIL →
JUNIO_1 → JUNIO_2 → FINAL`) para que los gráficos respeten el orden temporal
y no el alfabético. `ES_ESTIMADO` indica si el valor de esa etapa vino de
ffill (la columna cruda era `NaN`) — filas con `CANTIDAD` `NaN` (MCP
inexistente en esa etapa) se excluyen, así las líneas de tendencia empiezan
donde la MCP nace.

---

## Las 4 vistas del dashboard

1. **Resumen ejecutivo** — 5 KPIs (los dos primeros con una línea de texto
   explicativo debajo) + gráfico combinado (barras de MCPs corregidas por
   etapa + línea de electores totales) + composición por estado de error
   (dona) + tabla de top causas de error subsanado (agrupadas en 6
   categorías) + barra horizontal de causas por provincia (Top 15).
2. **Evolución de electores** — Top 20 MCPs por variación absoluta (con un
   popover explicando qué significa subir/bajar) + proporción de MCPs según
   su nº de correcciones (barra 100% apilada) + tabla filtrable por
   departamento y provincia, con encabezados en español legible, descargable
   a Excel.
3. **Mapa de errores** — filtros (departamento, provincia, estado, causa) →
   gráfico de errores por provincia (respeta los filtros) → **mapa de
   coropletas** a nivel provincia (`geofiles/PROVINCIA.gpkg`) con selector
   "Estado del error" / "Causa específica" → tabla filtrable al final,
   descargable a Excel.
4. **Ficha por MCP** — buscador por nombre, código, departamento y/o
   provincia; para cada resultado muestra métricas (electores finales,
   variación, estado de error), causa y detalle si tiene error, un line
   chart de trayectoria por etapa (marcador relleno = dato real, hueco =
   arrastrado por ffill), y una tabla de trayectoria coloreada (verde =
   subió/primera aparición, naranja = bajó, gris = sin cambio o sin dato).

### Mapa de coropletas (Vista 3) — notas técnicas

- Usa `px.choropleth_map` (MapLibre), **no** `px.choropleth` (geo/d3-geo):
  esta última tiene un bug de renderizado confirmado en la versión de Plotly
  del proyecto con GeoJSON custom de muchos polígonos — en vez de recortar
  cada polígono a su forma real, pinta todo el lienzo con el color de la
  última feature. Reproducido incluso con un GeoJSON mínimo de 3 cuadrados;
  `choropleth_map` no tiene ese problema y de paso trae un mapa base real
  (ríos, fronteras, ciudades).
- `load_provincia_geojson()` lee `geofiles/PROVINCIA.gpkg`, arma una clave
  `ID_PROV = "DEPARTAMENTO - PROVINCIA"` y corrige un alias de escritura
  confirmado: `"ANTONIO RAYMONDI"` (geopackage) = `"ANTONIO RAIMONDI"`
  (padrón), en Áncash. El match es 180/180 provincias del padrón.
- Si `geopandas` no está instalado, la vista muestra una advertencia en vez
  de romper el resto de la pestaña (`GEOPANDAS_OK` flag).

---

## Limitaciones conocidas

- **`CANTIDAD_FINAL` no siempre coincide con el ffill puro de las 3 rondas
  intermedias** (2 filas de 3388 divergen) — se asume que incorpora ajustes
  manuales hechos directamente en el Excel fuente, y por eso se usa como
  valor autoritativo de `ETAPA_FINAL` en vez de derivarla.
- **8 MCPs están marcadas simultáneamente como `SUBSANADO` y `PENDIENTE`**
  (`DISTRITO_CON_ERROR_SUBSANADO == "SI"` y `ERROR_PENDIENTE == "SI"` a la
  vez). `ESTADO_ERROR` prioriza `PENDIENTE` en ese solape.
- **`CORRECION_2_ENVIADA` no se usa** en la UI (ver "Esquema de datos").
- **Los archivos de `outputs/` no se regeneran automáticamente** — solo se
  actualizan si se vuelve a ejecutar el notebook. El dashboard (`app.py`) no
  depende de `outputs/`; lee directamente de `DATABASES/` y cachea con
  `@st.cache_data`.

---

## Historial de cambios

Ver [CHANGELOG.md](CHANGELOG.md) para el detalle sesión por sesión (bugs
encontrados, fixes aplicados, features agregadas).

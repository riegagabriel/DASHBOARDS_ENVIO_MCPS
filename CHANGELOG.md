# Changelog — Dashboard MCP

Registro cronológico de decisiones, bugs encontrados y features agregadas.
Para el estado actual del proyecto ver [README.md](README.md).

---

## Sesión 1–2 (26 de junio de 2026) — Construcción inicial

### Auditoría y refactor del notebook

- Se auditó el trabajo previo (documentado en `docs/MD_PREVIO.md`) y se
  reconstruyó en `notebooks/AUDITORIA_MCPS.ipynb` con 8 etapas numeradas y
  explicadas (carga → revisión estructural → limpieza → `df_long` →
  auditoría de llaves → `df_consolidado` → clasificación → exportación).

### Bugs de encoding resueltos (los más críticos del proyecto)

1. **Codec equivocado (latin-1 en vez de cp1252).** El mojibake de `Ñ`
   incluye el byte U+2018 (comilla simple izquierda), que no existe en
   Latin-1 (tope U+00FF) pero sí en cp1252 (0x91). Cambiar de
   `.encode("latin-1")` a una estrategia basada en cp1252 redujo los
   códigos con múltiples nombres de **29 a 8**.
2. **Escapes XML de openpyxl sin revertir.** openpyxl serializa bytes cp1252
   indefinidos (ej. el segundo byte de "Á") como el literal `_x0081_` en el
   XML del `.xlsx`. `JORGE CHÁVEZ` aparecía como `JORGE CHÃ_x0081_VEZ`
   (7 caracteres ASCII literales). Se agregó `unescape_excel_xml()` antes
   del fix de mojibake.
3. **`.upper()` aplicado antes del fix de mojibake.** Mayusculizar cambia el
   byte cp1252 de algunos caracteres (`š` 0x9A → `Š` 0x8A), lo que hace que
   el mismo byte se decodifique distinto en UTF-8 (`Ê` en vez de `Ú`). Mover
   `fix_garbled()` antes de `.upper()` redujo los casos de 8 a **6 genuinos**
   que sí requieren revisión manual (ej. `MAURE KALLACHIRI` vs
   `MAURE KALLAPUMA`, código `220201001`).
4. **Emojis en `print()` rompían stdout en Windows (cp1252).** Se agregó
   `sys.stdout.reconfigure(encoding='utf-8')` en los scripts de prueba.

### Dashboard v1 (`app.py`)

Se construyó el dashboard Streamlit con 4 vistas (Resumen ejecutivo, Análisis
por envío, Mapa de problemas, Historial por MCP), verificado con captura de
pantalla mostrando los KPIs correctos (3,821 MCPs, 537 con incidencias).

### Iteración de diseño (feedback del usuario)

- El pie chart de la Vista 1 no comunicaba bien la evolución temporal →
  reemplazado por una barra apilada envío × tipo de incidencia.
- Se agregó el gráfico de Top 15 Provincias junto al de Departamentos.
- Se rediseñó la Vista 4 (Historial por MCP) con una tabla de trayectoria
  coloreada por fila en vez de una tabla plana.

---

## Sesión 3 (9 de julio de 2026) — Refinamiento, treemaps y documentación

### Ajustes de la Vista 1 y Vista 3 (por feedback del usuario)

- **Se eliminó la barra apilada "Composición de incidencias en cada envío"**
  de la Vista 1: el usuario la consideró de poco valor informativo.
- **Se agregó la columna `DISTRITOS`** (análoga a `PROVINCIAS`) al
  `df_cons`/Mapa de problemas, para ver a qué distrito(s) perteneció cada MCP.
- **Se agregaron todas las columnas `COD_01..08` y `DIST_01..08`** a la
  tabla de Mapa de problemas, para inspeccionar exactamente qué código y
  distrito tuvo una MCP en cada envío puntual (no solo el resumen agregado).

### Bug: `KeyError: '_estado'` en Historial por MCP

`style_timeline()` hacía `df_tl.drop(columns=["_estado"])` **antes** de que
la función de coloreado (`row_color`) intentara leer `row["_estado"]` — la
columna ya no existía en ese punto. Fix: capturar `estado_series =
df_tl["_estado"].copy()` antes del drop, y dentro de `row_color` usar
`estado_series.loc[row.name]` (el índice de fila) en vez de indexar la fila
recortada.

### Treemaps agregados al Mapa de problemas

- Un treemap `Departamento → Provincia → MCP` con todas las MCPs con
  incidencias, coloreado por tipo de incidencia.
- Un comparador de dos treemaps lado a lado (uno por envío seleccionado) para
  ver cómo estaba distribuida geográficamente una MCP en cada remesa.

### Bug: `ValueError: ... is not a leaf` en los treemaps

Causa: se usaba `px.Constant("Perú")` como raíz artificial del `path`. Si
alguna fila tenía `DEPARTAMENTO`/`PROVINCIA`/`MCP` vacío o `NaN`, Plotly la
asignaba como fila intermedia (no-hoja) bajo esa raíz, y el treemap fallaba
en la primera carga de la Vista 3. Fix: se quitó `px.Constant()` (el path
queda en 3 niveles: `DEPARTAMENTO → PROVINCIA → MCP`) y se filtran filas con
`.dropna()` + comparación a string vacío **antes** de pasar el DataFrame a
`px.treemap`. De paso se confirmó que el treemap ya operaba a nivel de MCP
individual (hoja del árbol), que era el pedido explícito del usuario.

### Reorganización de carpetas y documentación

- Se creó la estructura `notebooks/`, `docs/`, `outputs/`, `archive/`
  (detalle en README.md).
- Se movió `AUDITORIA_MCPS.ipynb` a `notebooks/` y se corrigieron sus rutas
  relativas (`DATABASES_DIR`, exportación a `outputs/`) para que sigan
  funcionando desde la nueva ubicación — verificado ejecutando las 26 celdas
  de código de punta a punta sin errores (5,553 filas en `df_long`, 3,821 en
  `df_consolidado`, 537 requieren revisión, 6 casos genuinos de múltiples
  nombres por código — cifras idénticas a las de la sesión anterior).
- Se archivó `NOTEBOOK_REVISION_EXCEL_MCPS_FINAL_REVISION.ipynb` (un notebook
  de 384 KB perteneciente al otro proyecto, `EXCEL_NIVEL_MCP`, que apareció
  mezclado en esta carpeta) en `archive/` sin eliminarlo.
- Se creó `requirements.txt` con las versiones instaladas y verificadas
  (`streamlit>=1.59`, `pandas>=3.0`, `plotly>=6.9`, `openpyxl>=3.1`,
  `numpy>=2.3`).
- Se agregaron docstrings y comentarios explicando el "por qué" en los
  puntos no obvios del código: el pipeline de limpieza, el orden
  alfabético (no cronológico) de los envíos, y el fix del treemap.
- Se eliminó `ENVIO_LABELS`, un diccionario definido en `app.py` que ya no
  tenía ninguna referencia en el código (código muerto).

### Bugs adicionales encontrados durante la verificación en vivo del dashboard

- **Columna `Cantidad` de tipo mixto en el timeline.** `build_timeline()`
  mezclaba `int` (cantidad real) con el string `"—"` (placeholder de
  ausencia) en la misma columna, produciendo `dtype=object`. PyArrow no
  podía serializarla de forma limpia para el `st.dataframe` estilizado
  (arrojaba un warning y aplicaba un fix automático interno de Streamlit,
  sin llegar a romper la UI). Fix: castear siempre a `str`.
- **`use_container_width` deprecado.** Streamlit 1.59 marca este parámetro
  para remoción; se reemplazaron las 14 ocurrencias por `width="stretch"`
  en todas las llamadas a `st.plotly_chart` / `st.dataframe`.

Ambos bugs se detectaron ejecutando el dashboard real (no solo revisando el
código) y navegando las 4 vistas con búsquedas concretas (`MAURE`), tal como
indica la buena práctica de probar el camino dorado antes de dar por
terminado un cambio de UI.

---

## Sesión 4 (9 de julio de 2026) — Reconstrucción sobre PADRON_MODIFICACIONES_VF

### Cambio de fuente de datos

El usuario pidió reconstruir todo el dashboard a partir de un archivo nuevo,
`DATABASES/PADRON_MODIFICACIONES_VF.xlsx`, que reemplaza a los 8 Excel de
envíos usados hasta la Sesión 3. Se inspeccionó primero la estructura real
del archivo (una hoja, 3388 filas, **una fila por MCP**) antes de planificar,
porque el modelo de datos resultó ser completamente distinto: no hay
concepto de "envío separado" ni homónimos/cambio de código/distrito; en su
lugar hay 4 etapas de corrección de electores (`FEBRERO → ABRIL → JUNIO_1 →
JUNIO_2`) que convergen en `CANTIDAD_FINAL`, más columnas explícitas de
trazabilidad de errores (`ES_SUBSANADO`, 169/3388; `ES_PENDIENTE`, 56/3388).

Se usó un agente Opus para planificar la reconstrucción (nuevo modelo de
datos, decisión de archivar el pipeline viejo, diseño de las 4 vistas
nuevas) antes de implementar con Sonnet 5, siguiendo el pedido explícito del
usuario de separar planificación de ejecución.

### Decisiones de diseño del pipeline nuevo

- **Forward-fill horizontal** sobre las 4 columnas de corrección crudas
  (`ELECTORES_FEBRERO...CORRECCION_JUNIO_2`) para obtener las "etapas
  efectivas": un `NaN` en `CORRECCION_ABRIL`, por ejemplo, no significa "sin
  electores" sino "esa ronda no tocó la MCP, sigue vigente el valor
  anterior". Sin este fix, una línea de tendencia por etapa mostraría caídas
  falsas a 0 en cada MCP no corregida en una ronda dada.
- **`ETAPA_FINAL` se fija siempre a `CANTIDAD_FINAL`**, no se deriva por
  ffill: 2 de 3388 filas divergen del ffill puro de `CORRECCION_JUNIO_2`,
  señal de que `CANTIDAD_FINAL` incorpora ajustes manuales hechos
  directamente en el Excel fuente.
- **Solape `ES_SUBSANADO` ∩ `ES_PENDIENTE`**: 8 MCPs tienen ambos flags en
  `"SI"` simultáneamente. `ESTADO_ERROR` prioriza `"PENDIENTE"` sobre
  `"SUBSANADO"` en ese caso (un error que sigue abierto pesa más que uno ya
  resuelto).
- **`normalize_text_preserve_case()`** (variante de `normalize_text()` sin
  `.upper()`) para las columnas de causa/detalle de error
  (`CAUSA_ERROR_SUBSANADO`, `ERROR_PENDIENTE_CAUSA`,
  `ERROR_PENDIENTE_DETALLE`): son texto narrativo en español pensado para
  lectura humana; aplicarles el mismo `.upper()` que a las columnas
  geográficas las haría ilegibles.
- **`CORRECION_2_ENVIADA` se descarta de la UI**: solo 15/3388 filas tienen
  dato (siempre `"NO"`), cobertura insuficiente para aportar señal.

Validado en el notebook: 3388 MCPs, 3388 códigos únicos (sin duplicados),
169 subsanados, 56 pendientes, 129 MCPs nuevas post-febrero, 3,360,656
electores finales vs. 3,212,432 en febrero.

### Archivado del pipeline anterior (8 envíos)

Se archivaron sin borrar, siguiendo la política del proyecto:

- Los 8 Excel de envíos → `archive/DATABASES_ENVIOS_HISTORICOS/`
  (`DATABASES/` queda solo con `PADRON_MODIFICACIONES_VF.xlsx`).
- `notebooks/AUDITORIA_MCPS.ipynb` → `archive/AUDITORIA_MCPS_ENVIOS.ipynb`.
- Los 3 `outputs/*.xlsx` viejos (`df_consolidado`, `df_long`, `df_revision`)
  → `archive/outputs_envios/`.

Se decidió **no** conservar el pipeline viejo como vista secundaria del
dashboard: mantener dos modelos de datos incompatibles en el mismo `app.py`
habría duplicado estado y confundido los KPIs, y el usuario pidió
explícitamente que todo el dashboard se reconstruyera desde la fuente nueva.
Queda recuperable en `archive/` si se necesita en el futuro.

### Dashboard reescrito (`app.py`)

Reescritura completa de `load_data()` y las 4 vistas (se conservan los
nombres "Resumen ejecutivo" y "Ficha por MCP"/"Mapa de errores" como
evolución de "Historial por MCP"/"Mapa de problemas", pero con contenido
enteramente nuevo — ver README.md, sección "Las 4 vistas del dashboard").
Se conservaron textuales: el pipeline de limpieza de mojibake
(`unescape_excel_xml`, `fix_garbled`, `normalize_text`, `normalize_cod`), el
helper `to_excel_bytes`, y el patrón anti-`"is not a leaf"` de los treemaps
(dropna + filtro de string vacío antes de `px.treemap`, sin `px.Constant`).

### Notebook nuevo (`notebooks/AUDITORIA_MODIFICACIONES.ipynb`)

Creado desde cero con 8 etapas numeradas (carga → limpieza → flags/
validaciones → etapas efectivas → `df_evolucion` → derivadas → validaciones
finales → exportación a `outputs/df_padron.xlsx`, `df_evolucion.xlsx`,
`df_errores.xlsx`). Ejecutado de punta a punta con `jupyter nbconvert
--execute` (se instaló `ipykernel` para poder correrlo) sin errores,
reproduciendo las mismas cifras validadas arriba.

### Verificación en vivo

Se instalaron `streamlit` y `plotly` en el intérprete Python del entorno (no
estaban presentes) y se ejecutó `streamlit run app.py`, navegando las 4
vistas nuevas con datos reales (departamento con errores, búsqueda de una
MCP pendiente concreta) antes de dar por terminado el cambio.

---

## Sesión 5 (9 de julio de 2026) — Rediseño por pestaña, mapa de coropletas y paleta RENIEC

Pedido del usuario: iterar el dashboard ya desplegado en Streamlit Cloud,
pestaña por pestaña, con una lista de cambios concretos de diseño/
funcionalidad. Antes de tocar código se presentaron 3 propuestas a decidir
con el usuario (agrupación de causas, plan técnico del mapa de coropletas,
paleta institucional) — las 3 se aprobaron en su opción recomendada.

### Investigación previa

- Se inspeccionaron los 3 `.gpkg` de `geofiles/` (departamento, provincia,
  distrito; EPSG:4326, fuente "V Censo Nacional Economico") y se comprobó el
  cruce de nombres contra el padrón: 180/180 provincias matchean tras un
  alias (`ANTONIO RAYMONDI` en el geopackage = `ANTONIO RAIMONDI` en el
  padrón).
- Se buscaron los colores institucionales oficiales de RENIEC: azul marino
  `#002F56` (confirmado en su manual gráfico, CMYK 100/79/42/38), usado como
  color primario del nuevo tema.

### Nueva columna derivada: `CAUSA_CATEGORIA`

Función `categorizar_causa()` agrupa las ~12 causas crudas de
`CAUSA_ERROR_SUBSANADO`/`ERROR_PENDIENTE_CAUSA` en 6 categorías legibles por
coincidencia de substring (no diccionario 1 a 1, para tolerar variantes
menores de redacción). No reemplaza a `CAUSA` — es una columna adicional
solo para agrupar en la UI.

### Bug encontrado y resuelto: `px.choropleth` pinta todo el mapa de un solo color

El mapa de coropletas por provincia, construido inicialmente con
`px.choropleth` (trace tipo "geo", basado en d3-geo) + `fitbounds="locations"`,
renderizaba un único polígono minúsculo sin relleno en vez de las ~33-196
provincias esperadas. Se diagnosticó paso a paso:

1. Se confirmó que el join `ID_PROV = "DEPARTAMENTO - PROVINCIA"` entre el
   padrón y el geopackage era perfecto (180/180, y 33/33 en el subconjunto
   con error) — descartado como causa.
2. Se generó la figura fuera de Streamlit (`fig.write_html(...)`) para
   descartar que fuera un problema de integración con el iframe de
   Streamlit — el bug se reproducía igual en un HTML plano abierto en Chrome.
3. Se probó con las 196 provincias (no solo las 33 con error) y con
   geometría sin simplificar: el bug persistía — toda la pantalla se pintaba
   con el color de la ÚLTIMA feature de la colección, dejando ver apenas 1-2
   polígonos pequeños por encima.
4. Se probó corrigiendo el sentido de los anillos de los polígonos
   (`shapely.orient_polygons`, por si el geopackage traía el winding order
   de shapefile —horario— en vez del de GeoJSON —antihorario—): no cambió
   nada, descartado.
5. Se aisló con un GeoJSON mínimo hecho a mano (3 cuadrados simples,
   bien separados, en coordenadas válidas de Perú): el bug se reprodujo
   igual — confirma que es un bug de `px.choropleth`/`geo` en esta versión
   de Plotly (6.9.0), no un problema de los datos.
6. Se probó el mismo GeoJSON mínimo con `px.choropleth_map` (MapLibre): 3
   cuadrados correctamente recortados y coloreados, sobre un mapa base real
   de Perú (calles, fronteras, Lima). Se confirmó con los datos reales (33
   provincias con error): funciona perfecto.

**Fix:** se reemplazó `px.choropleth` por `px.choropleth_map` en
`app.py`. Documentado como nota técnica en el código y en el README.

### Cambios por pestaña

- **Resumen ejecutivo:** caption de texto bajo "MCPs totales" y "Electores
  (final)"; el gráfico de evolución ahora combina barras (MCPs corregidas
  por etapa) + línea (electores totales) en un solo gráfico con eje Y
  secundario; tabla de causas agrupada en 6 categorías; nuevo gráfico de
  barras horizontal "¿en qué provincias se dieron las causas de error?"
  (Top 15, coloreado por categoría de causa).
- **Evolución de electores:** se eliminó el gráfico "Actividad por ronda de
  corrección" (su información se movió al combo de la Vista 1); se agregó
  un popover explicando qué significa que una MCP suba o baje; el histograma
  de "¿cuántas veces se corrigió cada MCP?" se reemplazó por una barra 100%
  apilada de proporciones, con un `st.metric` destacando el % sin ninguna
  corrección; la tabla ahora tiene filtro de provincia (además de
  departamento) y encabezados renombrados a español legible.
- **Mapa de errores:** se reordenó la página (filtros → gráfico de errores
  por provincia → mapa de coropletas → tabla al final); el gráfico de
  errores pasó de ser por departamento a ser por provincia y ahora respeta
  los filtros de arriba; se eliminaron ambos treemaps; se agregó el mapa de
  coropletas con selector "Estado del error" / "Causa específica".
- **Ficha por MCP:** se agregaron filtros de departamento y provincia
  (cascada), usables también sin nombre/código para listar todas las MCPs
  de una zona.

### Paleta institucional RENIEC

Se creó `.streamlit/config.toml` con tema `primaryColor = "#002F56"` (azul
RENIEC) y se actualizó `ESTADO_COLOR_MAP`/`CAUSA_CATEGORIA_COLOR_MAP` en
`app.py` para usar esa paleta manteniendo el semáforo semántico (verde/rojo)
en los estados de error.

### Dependencias nuevas

`requirements.txt` ahora incluye `geopandas>=1.1`, `pyogrio>=0.13`,
`shapely>=2.1`, `pyproj>=3.7` (para leer `geofiles/*.gpkg`). La importación
de `geopandas` en `app.py` está en un `try/except`: si el paquete no está
disponible, la Vista 3 muestra una advertencia en vez de romper el resto del
dashboard.

### Verificación en vivo

Se recorrieron las 4 vistas con el navegador real después de cada cambio
(no solo la vista de accesibilidad): KPIs con texto, combo de evolución,
tabla de causas agrupada, gráfico de causas por provincia, popover de
variación, proporción de correcciones, filtros de provincia en Vista 2 y 4,
reordenamiento y mapa de coropletas (ambos modos) en Vista 3 — incluyendo el
ciclo completo de diagnóstico del bug de `px.choropleth` descrito arriba,
que solo se detectó al mirar el render real (la data y el código "se veían
bien" en el DOM/accesibilidad hasta que se inspeccionó visualmente).

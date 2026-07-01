# Consolidación y Auditoría de Envíos de MCP

## Objetivo

Se dispone de ocho archivos Excel, correspondientes a diferentes envíos del padrón de MCP. El objetivo es consolidarlos en un único DataFrame que permita:

* Identificar una única fila por MCP.
* Conservar el historial de cada envío.
* Detectar cambios de código RENIEC.
* Detectar cambios de distrito.
* Identificar posibles inconsistencias que requieran revisión manual.
* Construir una base maestra que pueda actualizarse con futuros envíos.

---

# Archivos utilizados

Los archivos analizados fueron:

1. REPORTE_MCP_24022026_01.xlsx
2. RESUMEN_17062026_1.xlsx
3. RESUMEN_3.xlsx
4. RESUMEN_4_5_6.xlsx
5. RESUMEN_SEGUNDO_GRUPO.xlsx
6. RESUMEN_SEGUNDO_GRUPO_18062026_2.xlsx
7. RESUMEN_TAYACAJA.xlsx
8. RESUMEN_TERCER_GRUPO.xlsx

Todos poseen una estructura prácticamente idéntica:

* ITEM
* COD_MCP_RENIEC
* DEPARTAMENTO
* PROVINCIA
* DISTRITO
* MCP
* CANTIDAD (o COUNT(1))

---

# Etapa 1. Revisión estructural

Inicialmente se revisó la estructura de todos los archivos mediante:

* `shape`
* `info()`
* `columns`
* tipos de datos

Con ello se verificó que todos poseen la misma estructura lógica y únicamente existe una diferencia menor:

* Un archivo utiliza `COUNT(1)` en lugar de `CANTIDAD`.

Esta diferencia fue normalizada.

---

# Etapa 2. Limpieza general

Se realizó una limpieza completa de los datos.

## COD_MCP_RENIEC

Se detectó que:

* estaba almacenado como `float`
* aparecía con `.0`
* algunos códigos tenían menos de nueve dígitos

Se aplicó la siguiente normalización:

* convertir a texto
* eliminar `.0`
* completar con ceros a la izquierda (`zfill(9)`)

Ejemplo:

| Original   | Resultado |
| ---------- | --------- |
| 80501001.0 | 080501001 |
| 50110100.0 | 050110100 |

---

## Variables de texto

Se normalizaron:

* DEPARTAMENTO
* PROVINCIA
* DISTRITO
* MCP

Aplicando:

* eliminación de espacios
* conversión a mayúsculas

Con ello se evitaron diferencias por formato.

---

# Etapa 3. Construcción del historial

En lugar de trabajar con ocho DataFrames independientes, se creó un único DataFrame denominado:

```text
df_long
```

Cada fila representa:

> una MCP en un envío determinado.

Se añadieron dos variables:

* ENVIO
* ARCHIVO

Con ello es posible reconstruir completamente cualquier envío original.

---

# Etapa 4. Auditoría de las llaves

Antes de consolidar la información se evaluó cuál podía utilizarse como identificador único.

## Hipótesis inicial

Inicialmente se pensó utilizar:

```text
DEPARTAMENTO
+
PROVINCIA
+
MCP
```

como llave.

Sin embargo, se decidió verificar si realmente era única.

---

## Revisión de homónimos

Se revisó si existían MCP con el mismo nombre en distintas provincias.

Resultado:

* **80 casos** de MCP homónimas.

Ejemplos:

* ICHOCA
* SAN ISIDRO
* PACCHA
* SANTA CRUZ
* LA COLPA

Estas corresponden a comunidades diferentes ubicadas en provincias distintas.

Conclusión:

El nombre de la MCP no puede utilizarse como identificador único.

---

## Revisión del código RENIEC

Posteriormente se evaluó si un mismo código aparecía asociado a distintos nombres.

Resultado:

* **29 códigos** presentan más de un nombre de MCP.

En la gran mayoría de casos se observó que correspondían a diferencias de escritura o normalización del nombre.

Ejemplos:

* SAN JOSE
* SAN JOSÉ

Existe un caso que requiere revisión manual:

```text
220201001

MAURE KALLACHIRI

MAURE KALLAPUMA
```

Este caso quedó registrado para posterior validación.

---

## Revisión de cambios de código

Se evaluó si una misma combinación:

```text
DEPARTAMENTO
+
PROVINCIA
+
MCP
```

presentaba distintos códigos RENIEC.

Resultado:

* **35 casos** presentan más de un código.

Esto sugiere cambios de código entre envíos.

---

# Conclusión de la auditoría

Se comprobó que:

* El nombre de la MCP no constituye una llave única.
* El código RENIEC tampoco constituye una llave completamente estable.
* Existen cambios reales de código entre envíos.
* Existen comunidades homónimas.

Por tanto, la consolidación debía incorporar mecanismos de auditoría.

---

# Etapa 5. Construcción del DataFrame consolidado

Se decidió construir un DataFrame denominado:

```text
df_consolidado
```

Cada fila representa una MCP.

La estructura general quedó definida como:

```text
DEPARTAMENTO
PROVINCIA
MCP

COD_01
DIST_01
CANT_01

COD_02
DIST_02
CANT_02

...

COD_08
DIST_08
CANT_08

COD_ACTUAL
DIST_ACTUAL

N_ENVIOS

CAMBIO_CODIGO

CAMBIO_DISTRITO
```

De esta forma se conserva el historial completo de todos los envíos.

---

# Variables de auditoría

Con el objetivo de identificar automáticamente casos especiales, se incorporaron variables adicionales.

## Homónimos

Número de provincias donde aparece el mismo nombre.

Variables:

* N_PROVINCIAS
* PROVINCIAS

---

## Cambios de código

Variables:

* N_CODIGOS
* CODIGOS

---

## Cambios de distrito

Se contabiliza el número de distritos distintos observados entre envíos.

---

# Clasificación automática

Se creó una variable:

```text
OBSERVACIONES
```

que resume los problemas detectados.

Puede contener una o varias etiquetas:

* OK
* HOMONIMO
* CAMBIO_CODIGO
* CAMBIO_DISTRITO

Una misma MCP puede presentar varias observaciones simultáneamente.

Ejemplo:

```text
HOMONIMO | CAMBIO_CODIGO
```

---

También se creó:

```text
REQUIERE_REVISION
```

que permite filtrar rápidamente todos los casos especiales.

---

# DataFrames generados

Actualmente el flujo produce tres DataFrames principales.

## 1. df_long

Historial completo.

Una fila por:

MCP × envío.

Conserva toda la información original.

---

## 2. df_consolidado

Base maestra.

Una fila por MCP.

Contiene toda la información consolidada y el historial por envío.

Será la base principal para análisis posteriores.

---

## 3. df_revision

Subconjunto de:

```text
df_consolidado
```

Contiene únicamente registros que requieren revisión manual.

Incluye casos de:

* homónimos
* cambios de código
* cambios de distrito

---

# Resultados obtenidos

Hasta el momento se obtuvo:

* ✔ Limpieza completa de los códigos RENIEC.
* ✔ Normalización de nombres.
* ✔ Construcción del historial (`df_long`).
* ✔ Construcción del DataFrame consolidado (`df_consolidado`).
* ✔ Identificación automática de homónimos.
* ✔ Identificación automática de cambios de código.
* ✔ Identificación automática de cambios de distrito.
* ✔ Generación de un DataFrame específico para revisión (`df_revision`).

---

# Trabajo pendiente

Antes de considerar finalizada la consolidación aún quedan algunas tareas.

## 1. Resolver manualmente los casos ambiguos

Especialmente:

* MAURE KALLACHIRI
* MAURE KALLAPUMA

y cualquier otro caso detectado durante la revisión.

---

## 2. Revisar los cambios de código

Determinar si corresponden a:

* actualización oficial del código RENIEC;
* error de digitación;
* duplicación de registros.

---

## 3. Validar los cambios de distrito

Confirmar cuáles representan:

* cambios administrativos reales;
* correcciones del padrón;
* errores de captura.

---

## 4. Generar un identificador permanente de MCP

Una vez resueltas las incidencias podrá construirse un identificador único y estable para cada MCP, independiente del código RENIEC utilizado en los distintos envíos.

---

# Estado actual del proyecto

Actualmente se dispone de una base consolidada que conserva el historial completo de los ocho envíos, incorpora mecanismos automáticos de auditoría y permite identificar de manera transparente todos los casos que requieren revisión manual.

La arquitectura implementada facilita la incorporación de futuros envíos, ya que el proceso de consolidación y validación puede ejecutarse nuevamente sin modificar la lógica desarrollada.

"""Módulo de validación: comprueba si un archivo es compatible con el esquema del modelo.

Las reglas NO están escritas aquí: se leen del esquema de entrada de los metadatos del modelo (`metadatos["entrada"]`).
Nunca corrige los datos: solo informa. Estados: compatible, compatible_con_advertencias o incompatible.
"""
import difflib
import numpy as np
import pandas as pd


# ==============================================================================
# Validación de compatibilidad
# ==============================================================================

COMPATIBLE = "compatible"
COMPATIBLE_CON_ADVERTENCIAS = "compatible_con_advertencias"
INCOMPATIBLE = "incompatible"
ERROR = "error"
ADVERTENCIA = "advertencia"

TIPOS_ETIQUETA = {"categorica": "texto", "fecha_hora": "fecha y hora", "numerico": "numérico"}
HISTORIA_MINIMA_POR_DEFECTO = 168


# ---------------------------------------------------------------- utilidades de apoyo
def _linea(indice) -> int:
    """Número de fila en el archivo (la 1 es el encabezado)."""
    return int(indice) + 2


def _ejemplos(indices, cantidad: int = 5) -> list:
    return [_linea(i) for i in list(indices)[:cantidad]]


def _hallazgo(id_regla, nivel, severidad, variable, mensaje, esperado=None, encontrado=None, indices=None, total=None):
    filas = None if indices is None else int(len(indices))
    return {"id": id_regla, "nivel": nivel, "severidad": severidad, "variable": variable, "mensaje": mensaje,
            "esperado": esperado, "encontrado": encontrado, "filas_afectadas": filas,
            "porcentaje_filas": round(filas / total * 100, 2) if filas is not None and total else None,
            "ejemplos_filas": _ejemplos(indices) if indices is not None else []}


def _vacios(serie: pd.Series) -> pd.Series:
    return serie.isna() | serie.fillna("").astype(str).str.strip().eq("")


def _a_numero(serie: pd.Series) -> pd.Series:
    return pd.to_numeric(serie, errors="coerce")


def _a_fecha(serie: pd.Series) -> pd.Series:
    fechas = pd.to_datetime(serie, format="ISO8601", errors="coerce")
    try:
        if fechas.dt.tz is not None:
            fechas = fechas.dt.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    return fechas


def _tipo_recibido(serie: pd.Series) -> str:
    valores = serie[~_vacios(serie)]
    if valores.empty:
        return "vacío"
    if _a_numero(valores).notna().mean() >= 0.98:
        return "numérico"
    if _a_fecha(valores).notna().mean() >= 0.98:
        return "fecha y hora"
    return "texto"


def _sugerir(nombre: str, presentes: list):
    normal = {c.lower().replace(" ", "_"): c for c in presentes}
    parecido = difflib.get_close_matches(nombre.lower(), list(normal), n=1, cutoff=0.7)
    return normal[parecido[0]] if parecido else None


def _limites_por_estacion(metadatos: dict) -> dict:
    """Rango físico plausible del nivel en cada estación: de 10 rangos intercuartílicos bajo el primer cuartil
    a 10 sobre el tercero (el mismo criterio con que se limpiaron los datos de entrenamiento)."""
    referencia = metadatos.get("referencia_percentil") or {}
    percentiles = np.array(referencia.get("percentiles", []), dtype=float)
    limites = {}
    for estacion, niveles in (referencia.get("niveles_m") or {}).items():
        q1, q3 = np.interp([25.0, 75.0], percentiles, np.array(niveles, dtype=float))
        rango = max(float(q3 - q1), 1e-6)
        limites[estacion] = (float(q1 - 10 * rango), float(q3 + 10 * rango))
    return limites


# ---------------------------------------------------------------- validación por columna
def _validar_categorica(serie, spec, total, hallazgos):
    nombre = spec["nombre"]
    vacios = _vacios(serie)
    if vacios.any() and not spec.get("admite_faltantes", False):
        hallazgos.append(_hallazgo("CON-001", "contenido", ERROR, nombre,
                                   f"'{nombre}' no puede tener valores vacíos y hay {int(vacios.sum())} filas sin dato.",
                                   "valor en todas las filas", "celdas vacías", serie.index[vacios], total))
    categorias = spec.get("categorias_validas")
    if categorias:
        limpia = serie.fillna("").astype(str).str.strip()
        desconocidas = ~vacios & ~limpia.isin(categorias)
        if desconocidas.any():
            valores = sorted(limpia[desconocidas].unique())
            pista = ""
            ancho = len(categorias[0])
            recuperables = [v for v in valores if v.zfill(ancho) in categorias]
            if recuperables:
                pista = " Parece que se perdieron ceros iniciales (por ejemplo al abrir el archivo en Excel)."
            hallazgos.append(_hallazgo(
                "CON-002", "contenido", ERROR, nombre,
                f"'{nombre}' trae valores que el modelo no conoce: {', '.join(valores[:5])}"
                f"{'...' if len(valores) > 5 else ''}.{pista} El sistema no los reemplaza ni los ignora.",
                f"uno de: {', '.join(categorias)}", ", ".join(valores[:10]), serie.index[desconocidas], total))


def _validar_fecha(serie, spec, total, hallazgos):
    nombre = spec["nombre"]
    vacios = _vacios(serie)
    if vacios.any() and not spec.get("admite_faltantes", False):
        hallazgos.append(_hallazgo("CON-001", "contenido", ERROR, nombre,
                                   f"'{nombre}' no puede tener valores vacíos y hay {int(vacios.sum())} filas sin dato.",
                                   "fecha y hora en todas las filas", "celdas vacías", serie.index[vacios], total))
    fechas = _a_fecha(serie)
    ilegibles = ~vacios & fechas.isna()
    if ilegibles.any():
        hallazgos.append(_hallazgo(
            "TIP-002", "tipo", ERROR, nombre,
            f"'{nombre}' debe ser una fecha y hora con formato AAAA-MM-DD HH:MM:SS; "
            f"{int(ilegibles.sum())} filas no se pudieron leer así.",
            "fecha y hora (AAAA-MM-DD HH:MM:SS)", ", ".join(serie[ilegibles].astype(str).unique()[:5]),
            serie.index[ilegibles], total))
    if spec.get("frecuencia", "").startswith("horaria"):
        fuera_de_hora = fechas.notna() & ((fechas.dt.minute != 0) | (fechas.dt.second != 0))
        if fuera_de_hora.any():
            hallazgos.append(_hallazgo(
                "DOM-001", "dominio", ERROR, nombre,
                f"El modelo trabaja con datos horarios y {int(fuera_de_hora.sum())} filas no están en una hora en punto. "
                "Agrupe las lecturas por hora antes de cargar el archivo.",
                "horas en punto (minutos y segundos en 0)", ", ".join(serie[fuera_de_hora].astype(str).unique()[:3]),
                serie.index[fuera_de_hora], total))
    return fechas


def _validar_numerica(serie, spec, total, hallazgos, estaciones=None, limites=None):
    nombre = spec["nombre"]
    vacios = _vacios(serie)
    numeros = _a_numero(serie)
    no_numericos = ~vacios & numeros.isna()
    if no_numericos.any():
        hallazgos.append(_hallazgo(
            "TIP-001", "tipo", ERROR, nombre,
            f"'{nombre}' debe ser un número y {int(no_numericos.sum())} filas traen texto u otro formato "
            "(los decimales van con punto).", "numérico", ", ".join(serie[no_numericos].astype(str).unique()[:5]),
            serie.index[no_numericos], total))
    if vacios.any():
        if spec.get("admite_faltantes", True):
            hallazgos.append(_hallazgo(
                "CON-003", "contenido", ADVERTENCIA, nombre,
                f"'{nombre}' tiene {int(vacios.sum())} valores vacíos ({vacios.mean() * 100:.1f} %). El modelo los trata "
                "como dato faltante, pero las predicciones de esas horas pueden ser menos confiables.",
                "valor en todas las filas", "celdas vacías", serie.index[vacios], total))
        else:
            hallazgos.append(_hallazgo("CON-001", "contenido", ERROR, nombre,
                                       f"'{nombre}' no puede tener valores vacíos.", "valor en todas las filas",
                                       "celdas vacías", serie.index[vacios], total))
    infinitos = numeros.notna() & ~np.isfinite(numeros.fillna(0))
    if infinitos.any():
        hallazgos.append(_hallazgo("TIP-001", "tipo", ERROR, nombre, f"'{nombre}' trae valores infinitos.",
                                   "número finito", "inf", serie.index[infinitos], total))
    validos = numeros.notna() & ~infinitos
    if "minimo_exclusivo" in spec:
        minimo = spec["minimo_exclusivo"]
        malos = validos & (numeros <= minimo)
        if malos.any():
            hallazgos.append(_hallazgo(
                "DOM-002", "dominio", ERROR, nombre,
                f"'{nombre}' debe ser mayor que {minimo} y {int(malos.sum())} filas no cumplen la regla.",
                f"mayor que {minimo}", f"valores desde {numeros[malos].min():g}", serie.index[malos], total))
    if "rango" in spec:
        minimo, maximo = spec["rango"]
        malos = validos & ((numeros < minimo) | (numeros > maximo))
        if malos.any():
            hallazgos.append(_hallazgo(
                "DOM-003", "dominio", ERROR, nombre,
                f"'{nombre}' debe estar entre {minimo:g} y {maximo:g} y {int(malos.sum())} filas quedan fuera de ese rango.",
                f"entre {minimo:g} y {maximo:g}", f"valores de {numeros[malos].min():g} a {numeros[malos].max():g}",
                serie.index[malos], total))
    if spec.get("rango_por_estacion") == "iqr10" and estaciones is not None and limites:
        fuera = pd.Series(False, index=serie.index)
        for estacion, (inferior, superior) in limites.items():
            de_la_estacion = estaciones == estacion
            fuera |= de_la_estacion & validos & ((numeros < inferior) | (numeros > superior))
        if fuera.any():
            hallazgos.append(_hallazgo(
                "DOM-004", "dominio", ERROR, nombre,
                f"{int(fuera.sum())} filas de '{nombre}' están fuera del rango físico plausible de su estación "
                "(más de 10 rangos intercuartílicos del nivel habitual). Revise si hay lecturas dañadas o cambio de unidades.",
                "dentro del rango plausible de cada estación", f"valores de {numeros[fuera].min():g} a {numeros[fuera].max():g}",
                serie.index[fuera], total))


# ---------------------------------------------------------------- reglas entre columnas
def _validar_tiempo_por_estacion(estaciones, fechas, spec_tiempo, historia_minima, total, hallazgos, exigir_historia=True):
    base = pd.DataFrame({"estacion": estaciones.astype(str).str.strip(), "fecha": fechas}).dropna()
    base = base[base["estacion"] != ""]
    if base.empty:
        return
    repetidas = base.duplicated(["estacion", "fecha"], keep=False)
    if repetidas.any():
        hallazgos.append(_hallazgo(
            "CON-004", "contenido", ERROR, spec_tiempo["nombre"],
            f"Hay {int(repetidas.sum())} filas con la misma estación y la misma hora. El sistema no decide cuál conservar.",
            "una fila por estación y hora", "estación y hora repetidas", base.index[repetidas], total))
    huecos, cortas = {}, []
    for estacion, grupo in base.drop_duplicates(["estacion", "fecha"]).groupby("estacion"):
        horas = (grupo["fecha"].max() - grupo["fecha"].min()).total_seconds() / 3600
        faltan = int(horas + 1 - len(grupo))
        if faltan > 0:
            huecos[estacion] = faltan
        if exigir_historia and horas < historia_minima:
            cortas.append((estacion, int(horas)))
    if huecos:
        detalle = ", ".join(f"{e}: {n} h" for e, n in sorted(huecos.items(), key=lambda x: -x[1])[:5])
        hallazgos.append(_hallazgo(
            "CON-005", "contenido", ADVERTENCIA, spec_tiempo["nombre"],
            f"Faltan {sum(huecos.values())} horas entre la primera y la última de cada estación ({detalle}). "
            "Se tratan como dato faltante.", "una fila por cada hora seguida", f"{sum(huecos.values())} horas ausentes"))
    if cortas:
        todas = len(cortas) == base["estacion"].nunique()
        detalle = ", ".join(f"{e}: {h} h" for e, h in cortas[:5])
        hallazgos.append(_hallazgo(
            "CON-006", "contenido", ERROR if todas else ADVERTENCIA, spec_tiempo["nombre"],
            f"El modelo necesita al menos {historia_minima} horas de historia por estación para predecir ({detalle}). "
            + ("Ninguna estación del archivo la tiene, así que no se puede predecir."
               if todas else "Esas estaciones no tendrán predicciones."),
            f"al menos {historia_minima} horas por estación", f"{min(h for _, h in cortas)} horas o menos"))


# ---------------------------------------------------------------- punto de entrada
def validar(tabla: pd.DataFrame, metadatos: dict, exigir_historia: bool = True) -> dict:
    """Valida una tabla (todo en texto) contra el esquema de entrada de los metadatos del modelo.

    `exigir_historia=False` se usa al actualizar el modelo: los datos nuevos se suman a los de entrenamiento y no
    necesitan traer por sí solos las horas de historia que sí exige una predicción.
    """
    entrada = metadatos["entrada"]
    especificaciones = entrada["columnas"]
    historia_minima = int(entrada.get("historia_minima_horas", HISTORIA_MINIMA_POR_DEFECTO))
    total = len(tabla)
    presentes = list(tabla.columns)
    requeridas = [s["nombre"] for s in especificaciones]
    hallazgos = []

    # 1. Esquema: columnas obligatorias y adicionales
    for nombre in requeridas:
        if nombre not in presentes:
            sugerencia = _sugerir(nombre, presentes)
            hallazgos.append(_hallazgo(
                "ESQ-001", "esquema", ERROR, nombre,
                f"Falta la columna obligatoria '{nombre}'." + (f" ¿Será '{sugerencia}'?" if sugerencia else ""),
                f"columna '{nombre}'", "no existe en el archivo"))
    extras = [c for c in presentes if c not in requeridas]
    if extras:
        hallazgos.append(_hallazgo(
            "ESQ-002", "esquema", ADVERTENCIA, ", ".join(extras[:5]),
            f"El archivo trae {len(extras)} columnas que el modelo no usa y se ignorarán: {', '.join(extras[:8])}.",
            "solo las columnas del esquema", ", ".join(extras[:8])))

    # 2. Tipo, contenido y dominio, columna por columna
    por_rol = {s.get("rol"): s for s in especificaciones if s.get("rol")}
    serie_estacion = tabla[por_rol["estacion"]["nombre"]] if "estacion" in por_rol and por_rol["estacion"]["nombre"] in presentes else None
    fechas = None
    limites = _limites_por_estacion(metadatos)
    estaciones_limpias = serie_estacion.fillna("").astype(str).str.strip() if serie_estacion is not None else None
    for spec in especificaciones:
        nombre = spec["nombre"]
        if nombre not in presentes:
            continue
        serie = tabla[nombre]
        if spec["tipo"] == "categorica":
            _validar_categorica(serie, spec, total, hallazgos)
        elif spec["tipo"] == "fecha_hora":
            fechas = _validar_fecha(serie, spec, total, hallazgos)
        elif spec["tipo"] == "numerico":
            _validar_numerica(serie, spec, total, hallazgos, estaciones_limpias, limites)

    # 3. Reglas que combinan columnas (duplicados, huecos, historia)
    if serie_estacion is not None and fechas is not None and "tiempo" in por_rol:
        _validar_tiempo_por_estacion(serie_estacion, fechas, por_rol["tiempo"], historia_minima, total, hallazgos, exigir_historia)

    errores = [h for h in hallazgos if h["severidad"] == ERROR]
    estado = INCOMPATIBLE if errores else COMPATIBLE_CON_ADVERTENCIAS if hallazgos else COMPATIBLE

    columnas = []
    for spec in especificaciones:
        nombre = spec["nombre"]
        presente = nombre in presentes
        vacios = int(_vacios(tabla[nombre]).sum()) if presente else None
        columnas.append({"nombre": nombre, "presente": presente, "tipo_esperado": TIPOS_ETIQUETA.get(spec["tipo"], spec["tipo"]),
                         "tipo_recibido": _tipo_recibido(tabla[nombre]) if presente else None,
                         "admite_faltantes": bool(spec.get("admite_faltantes", False)), "vacios": vacios,
                         "porcentaje_vacios": round(vacios / total * 100, 2) if presente and total else None})

    resumen = {"filas": total, "columnas_recibidas": len(presentes), "columnas_requeridas": len(requeridas),
               "columnas_ignoradas": extras, "errores": len(errores), "advertencias": len(hallazgos) - len(errores),
               "estaciones": sorted(estaciones_limpias[estaciones_limpias != ""].unique()) if estaciones_limpias is not None else [],
               "desde": str(fechas.min()) if fechas is not None and fechas.notna().any() else None,
               "hasta": str(fechas.max()) if fechas is not None and fechas.notna().any() else None}
    return {"estado": estado, "puede_predecir": estado != INCOMPATIBLE, "resumen": resumen,
            "columnas": columnas, "hallazgos": hallazgos}

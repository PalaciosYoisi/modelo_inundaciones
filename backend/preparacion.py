"""Prepara lo consultado a la API: limpia, pasa a horas y une el nivel de cada estación con la lluvia de sus pluviómetros.

Aplica el mismo criterio que preparar_datos.py (con el que se entrenó el modelo), pero sobre pocos días y sin
la historia completa: el rango válido de nivel y la escala de cada estación salen de las referencias del modelo,
y los picos de una sola hora se comparan con lo ya publicado. No lee ni escribe archivos.
"""
import warnings

import numpy as np
import pandas as pd

MAX_LLUVIA_DIA_MM = 500
HORAS_DE_CONTEXTO = 24


def referencias_de(metadatos: dict) -> dict:
    """Por estación: nivel mínimo y máximo plausibles (10 rangos intercuartílicos) y rango intercuartílico."""
    referencia = metadatos.get("referencia_percentil") or {}
    percentiles = np.array(referencia.get("percentiles", []), dtype=float)
    salida = {}
    for estacion, niveles in (referencia.get("niveles_m") or {}).items():
        q1, q3 = np.interp([25.0, 75.0], percentiles, np.array(niveles, dtype=float))
        rango = max(float(q3 - q1), 1e-6)
        salida[estacion] = {"minimo": float(q1 - 10 * rango), "maximo": float(q3 + 10 * rango), "rango": rango}
    return salida


def _leer(crudo: pd.DataFrame) -> pd.DataFrame:
    d = crudo.copy()
    d["fecha"] = pd.to_datetime(d["fechaobservacion"], errors="coerce")
    d["valor"] = pd.to_numeric(d["valorobservado"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    return d.dropna(subset=["fecha", "valor"])


def _sensor_principal(d: pd.DataFrame) -> pd.DataFrame:
    """Una estación puede tener varios sensores: se conserva el que más lecturas trae."""
    if d.empty:
        return d
    conteo = d.groupby(["codigoestacion", "codigosensor"]).size().reset_index(name="n")
    principal = conteo.sort_values("n").drop_duplicates("codigoestacion", keep="last")
    d = d.merge(principal[["codigoestacion", "codigosensor"]], on=["codigoestacion", "codigosensor"])
    return d.drop_duplicates(["codigoestacion", "fecha"])


def _picos_aislados(x: pd.Series, escala: float) -> pd.Series:
    """Horas cuyo nivel se aleja mucho de sus vecinas mientras las vecinas coinciden entre sí (falla del sensor)."""
    vecinas = np.column_stack([x.shift(k).values for k in (-3, -2, -1, 1, 2, 3)])
    validas = np.isfinite(vecinas).sum(axis=1)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # horas sin ninguna vecina con dato
        mediana = np.nanmedian(vecinas, axis=1)
        dispersion = np.nanmax(vecinas, axis=1) - np.nanmin(vecinas, axis=1)
    return pd.Series((validas >= 4) & (np.abs(x.values - mediana) > 3 * escala) & (dispersion < escala), index=x.index)


def _nivel_por_hora(crudo: pd.DataFrame, referencias: dict, descartes: dict) -> pd.DataFrame:
    n = _sensor_principal(_leer(crudo))
    antes = len(n)
    n = n[n["valor"] > 0]
    descartes["nivel_cero_o_negativo"] = antes - len(n)
    minimo = n["codigoestacion"].map(lambda c: referencias.get(c, {}).get("minimo", -np.inf))
    maximo = n["codigoestacion"].map(lambda c: referencias.get(c, {}).get("maximo", np.inf))
    fuera = (n["valor"] < minimo) | (n["valor"] > maximo)
    descartes["nivel_fuera_de_rango"] = int(fuera.sum())
    n = n[~fuera].assign(hora=lambda d: d["fecha"].dt.floor("h"))
    return n.groupby(["codigoestacion", "hora"])["valor"].mean().unstack(0)


def _lluvia_por_hora(crudo: pd.DataFrame, acumulados: set, descartes: dict) -> pd.DataFrame:
    l = _sensor_principal(_leer(crudo)).sort_values(["codigoestacion", "fecha"])
    acumulado = l["codigoestacion"].isin(acumulados)
    if acumulado.any():                                   # estos pluviómetros reportan un total que va subiendo: se pasa a lluvia de cada intervalo
        intervalo = l[acumulado].groupby("codigoestacion")["valor"].diff()
        reinicio = intervalo < 0
        intervalo[reinicio] = l.loc[acumulado, "valor"][reinicio]
        l.loc[acumulado, "valor"] = intervalo
        l = l.dropna(subset=["valor"])
    antes = len(l)
    l = l[l["valor"] >= 0]
    descartes["lluvia_negativa"] = antes - len(l)
    por_dia = l.groupby(["codigoestacion", l["fecha"].dt.normalize()])["valor"].transform("sum")
    descartes["lluvia_dias_de_mas_de_500_mm"] = int((por_dia > MAX_LLUVIA_DIA_MM).sum())
    l = l[por_dia <= MAX_LLUVIA_DIA_MM].assign(hora=lambda d: d["fecha"].dt.floor("h"))
    return l.groupby(["codigoestacion", "hora"])["valor"].sum().unstack(0)


def _solo_cambios(tabla: pd.DataFrame, publicado: pd.DataFrame):
    """Deja solo las horas que traen algo distinto de lo ya publicado: horas nuevas, o horas ya publicadas a las que llega un valor nuevo o distinto."""
    llaves = ["estacion", "fecha_hora"]
    previo = publicado.drop_duplicates(llaves).set_index(llaves)[["nivel_m", "lluvia_mm"]]
    nueva = tabla.set_index(llaves)
    existia = nueva.index.isin(previo.index)
    unido = nueva.join(previo, rsuffix="_pub")
    # Una hora publicada sin nivel se quedó así a propósito (sensor trabado, pico, falla): no se vuelve a llenar con lo que trae la API
    descartada = existia & unido["nivel_m_pub"].isna().values
    unido.loc[descartada, "nivel_m"] = np.nan
    tabla = tabla.assign(nivel_m=unido["nivel_m"].values)
    cambia = lambda nuevo, viejo, tolerancia: nuevo.notna() & (viejo.isna() | ((nuevo - viejo).abs() > tolerancia))
    hay = (cambia(unido["nivel_m"], unido["nivel_m_pub"], 1e-3) | cambia(unido["lluvia_mm"], unido["lluvia_mm_pub"], 1e-2)).values
    hay = hay & (existia | unido["nivel_m"].notna().values)      # una hora nueva solo con lluvia y sin nivel no sirve para el semáforo
    return tabla[hay].reset_index(drop=True), existia[hay]


def preparar(nivel: pd.DataFrame, lluvia: pd.DataFrame, pluviometros: dict, publicado: pd.DataFrame, referencias: dict, estaciones: list[str]):
    """Devuelve (tabla horaria con estacion, fecha_hora, nivel_m y lluvia_mm; resumen de lo que se hizo).

    nivel y lluvia: lo consultado, tal cual llegó. pluviometros: contenido de datos/pluviometros.json.
    publicado: lecturas ya publicadas (estacion, fecha_hora, nivel_m, lluvia_mm). Sirven de contexto para los picos
    y para dejar en la tabla solo lo que es nuevo o cambia.
    """
    descartes = {}
    nivel_h = _nivel_por_hora(nivel, referencias, descartes)
    lluvia_h = _lluvia_por_hora(lluvia, set(pluviometros.get("acumulados", [])), descartes)
    partes, sin_datos, picos_total = [], [], 0
    for codigo in estaciones:
        serie = nivel_h[codigo].dropna() if codigo in nivel_h else pd.Series(dtype=float)
        propios = [p for p in pluviometros["estaciones"].get(codigo, []) if p in lluvia_h.columns]
        lluv = lluvia_h[propios].mean(axis=1, skipna=True).dropna() if propios else pd.Series(dtype=float)
        con_dato = [s for s in (serie, lluv) if not s.empty]
        if not con_dato:
            sin_datos.append(codigo)
            continue
        horas = pd.date_range(min(s.index.min() for s in con_dato), max(s.index.max() for s in con_dato), freq="h")
        nivel_e = serie.reindex(horas)
        previo = publicado.loc[publicado["estacion"] == codigo].set_index("fecha_hora")["nivel_m"].dropna().sort_index()
        previo = previo[previo.index < horas[0]].tail(HORAS_DE_CONTEXTO)
        completa = pd.concat([previo, nivel_e])
        completa = completa.reindex(pd.date_range(completa.index.min(), completa.index.max(), freq="h"))
        picos = _picos_aislados(completa, referencias.get(codigo, {}).get("rango", 1e-6)).reindex(horas, fill_value=False)
        picos_total += int(picos.sum())
        partes.append(pd.DataFrame({"estacion": codigo, "fecha_hora": horas, "nivel_m": nivel_e.where(~picos).round(3).values,
                                    "lluvia_mm": lluv.reindex(horas).round(2).values}).dropna(subset=["nivel_m", "lluvia_mm"], how="all"))
    descartes["picos_de_una_hora"] = picos_total
    tabla = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=["estacion", "fecha_hora", "nivel_m", "lluvia_mm"])
    tabla, _ = _solo_cambios(tabla, publicado)
    resumen = {**resumir(tabla, publicado), "estaciones_sin_datos": sin_datos, "descartes": descartes}
    return tabla, resumen


def resumir(tabla: pd.DataFrame, publicado: pd.DataFrame) -> dict:
    """Cuántas horas trae la tabla, por estación: nuevas (no estaban publicadas) y actualizadas (ya estaban y reciben un valor)."""
    llaves = ["estacion", "fecha_hora"]
    existia = pd.Series(tabla.set_index(llaves).index.isin(publicado.set_index(llaves).index), index=tabla.index)
    por_estacion = [{"estacion": c, "horas": len(g), "horas_nuevas": int((~existia[g.index]).sum()), "horas_actualizadas": int(existia[g.index].sum()),
                     "desde": g["fecha_hora"].min(), "hasta": g["fecha_hora"].max(),
                     "horas_con_nivel": int(g["nivel_m"].notna().sum()), "horas_con_lluvia": int(g["lluvia_mm"].notna().sum())}
                    for c, g in tabla.groupby("estacion")]
    return {"filas": len(tabla), "desde": tabla["fecha_hora"].min() if len(tabla) else None, "hasta": tabla["fecha_hora"].max() if len(tabla) else None,
            "por_estacion": por_estacion}
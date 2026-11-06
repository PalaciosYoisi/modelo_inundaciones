"""Prepara lo consultado a la API: limpia, pasa a horas y une el nivel de cada estación con la lluvia de sus pluviómetros.

Aplica el mismo criterio que preparar_datos.py (con el que se entrenó el modelo), pero sobre pocos días y sin
la historia completa: el rango válido de nivel y la escala de cada estación salen de las referencias del modelo,
y los picos de una sola hora se comparan con lo ya publicado. No lee ni escribe archivos.

Nada se descarta en silencio: cada lectura que el sistema propone no usar queda como una «marca» con su motivo, el valor que llegó
y una justificación escrita. Los valores imposibles se excluyen siempre (se muestran y se explican); los picos de una hora, que son
posibles, los decide la persona que revisa el lote.
"""
import warnings

import numpy as np
import pandas as pd

MAX_LLUVIA_DIA_MM = 500
HORAS_DE_CONTEXTO = 24
MAX_MARCAS_GUARDADAS = 20_000        # si una consulta trae más, se guarda el conteo de todas y el detalle de las primeras

MOTIVOS = {                          # conservable: si quien revisa puede decidir quedarse con esas lecturas
    "nivel_cero_o_negativo": {"titulo": "Niveles en cero o negativos", "conservable": False,
                              "justificacion": "Un río no puede tener nivel cero ni negativo: el sensor no midió bien. Esas lecturas no sirven para el modelo."},
    "nivel_fuera_de_rango": {"titulo": "Niveles fuera de lo posible para la estación", "conservable": False,
                             "justificacion": "Están muy lejos de todo lo que esa estación ha registrado (más de 10 veces su variación habitual). "
                                              "El modelo solo puede usar valores dentro de ese rango."},
    "lluvia_negativa": {"titulo": "Lluvia negativa", "conservable": False,
                        "justificacion": "La lluvia no puede ser negativa: es una falla del pluviómetro."},
    "lluvia_dias_de_mas_de_500_mm": {"titulo": "Días con más de 500 mm en un pluviómetro", "conservable": False,
                                     "justificacion": "Un pluviómetro que suma más de 500 mm en un día supera lo razonable: es una falla del aparato. "
                                                      "Se excluye todo ese día de ese pluviómetro."},
    "picos_de_una_hora": {"titulo": "Picos de una sola hora", "conservable": True,
                          "justificacion": "El nivel de esa hora se aleja mucho de las horas vecinas, que coinciden entre sí. Un río no sube y baja así en una hora: "
                                           "parece una falla del sensor. Si usted sabe que fue real, puede conservarlas."},
}


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


def leer_fechas(serie: pd.Series) -> pd.Series:
    """Fechas de la API (2026-10-25T23:59:00.000) o de los CSV descargados (10/25/2026 11:59:00 PM). Lo que no se entiende queda vacío."""
    fechas = pd.to_datetime(serie, format="ISO8601", errors="coerce")
    for formato in ("%m/%d/%Y %I:%M:%S %p", "%Y %b %d %I:%M:%S %p"):
        faltan = fechas.isna() & serie.notna()
        if faltan.any():
            fechas[faltan] = pd.to_datetime(serie[faltan], format=formato, errors="coerce")
    return fechas


def _leer(crudo: pd.DataFrame) -> pd.DataFrame:
    d = crudo.copy()
    d["fecha"] = d["fechaobservacion"] if pd.api.types.is_datetime64_any_dtype(d["fechaobservacion"]) else leer_fechas(d["fechaobservacion"])
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


def _picos_aislados(x: pd.Series, escala: float) -> tuple[pd.Series, pd.Series]:
    """Horas cuyo nivel se aleja mucho de sus vecinas mientras las vecinas coinciden entre sí (falla del sensor), y el nivel al que están sus vecinas."""
    vecinas = np.column_stack([x.shift(k).values for k in (-3, -2, -1, 1, 2, 3)])
    validas = np.isfinite(vecinas).sum(axis=1)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # horas sin ninguna vecina con dato
        mediana = np.nanmedian(vecinas, axis=1)
        dispersion = np.nanmax(vecinas, axis=1) - np.nanmin(vecinas, axis=1)
    return pd.Series((validas >= 4) & (np.abs(x.values - mediana) > 3 * escala) & (dispersion < escala), index=x.index), pd.Series(mediana, index=x.index)


def _texto_de_hora(hora) -> str:
    return pd.Timestamp(hora).strftime("%Y-%m-%d %H:%M:%S")


def _marcas_por_hora(lecturas: pd.DataFrame, motivo: str, variable: str, detalle) -> list[dict]:
    """Una marca por sensor y hora (con cuántas lecturas había y su promedio, o su suma en la lluvia)."""
    if lecturas.empty:
        return []
    resumen = lecturas.assign(hora=lecturas["fecha"].dt.floor("h")).groupby(["codigoestacion", "hora"])["valor"].agg(
        valor="sum" if variable == "lluvia" else "mean", n="size").reset_index()
    return [{"motivo": motivo, "estacion": f.codigoestacion, "fecha_hora": _texto_de_hora(f.hora), "variable": variable, "valor": round(float(f.valor), 3),
             "conservar_valor": None, "lecturas": int(f.n), "detalle": detalle(f)} for f in resumen.itertuples(index=False)]


def _nivel_por_hora(crudo: pd.DataFrame, referencias: dict, descartes: dict, marcas: list) -> pd.DataFrame:
    n = _sensor_principal(_leer(crudo))
    antes = len(n)
    marcas += _marcas_por_hora(n[n["valor"] <= 0], "nivel_cero_o_negativo", "nivel", lambda f: "")
    n = n[n["valor"] > 0]
    descartes["nivel_cero_o_negativo"] = antes - len(n)
    minimo = n["codigoestacion"].map(lambda c: referencias.get(c, {}).get("minimo", -np.inf))
    maximo = n["codigoestacion"].map(lambda c: referencias.get(c, {}).get("maximo", np.inf))
    fuera = (n["valor"] < minimo) | (n["valor"] > maximo)
    descartes["nivel_fuera_de_rango"] = int(fuera.sum())
    marcas += _marcas_por_hora(n[fuera], "nivel_fuera_de_rango", "nivel",
                               lambda f: f"lo normal en esta estación va de {referencias[f.codigoestacion]['minimo']:.1f} a {referencias[f.codigoestacion]['maximo']:.1f} m")
    n = n[~fuera].assign(hora=lambda d: d["fecha"].dt.floor("h"))
    return n.groupby(["codigoestacion", "hora"])["valor"].mean().unstack(0)


def _lluvia_por_hora(crudo: pd.DataFrame, acumulados: set, descartes: dict, marcas: list) -> pd.DataFrame:
    l = _sensor_principal(_leer(crudo)).sort_values(["codigoestacion", "fecha"])
    acumulado = l["codigoestacion"].isin(acumulados)
    if acumulado.any():                                   # estos pluviómetros reportan un total que va subiendo: se pasa a lluvia de cada intervalo
        intervalo = l[acumulado].groupby("codigoestacion")["valor"].diff()
        reinicio = intervalo < 0
        intervalo[reinicio] = l.loc[acumulado, "valor"][reinicio]
        l.loc[acumulado, "valor"] = intervalo
        l = l.dropna(subset=["valor"])
    antes = len(l)
    marcas += _marcas_por_hora(l[l["valor"] < 0], "lluvia_negativa", "lluvia", lambda f: "")
    l = l[l["valor"] >= 0]
    descartes["lluvia_negativa"] = antes - len(l)
    por_dia = l.groupby(["codigoestacion", l["fecha"].dt.normalize()])["valor"].transform("sum")
    descartes["lluvia_dias_de_mas_de_500_mm"] = int((por_dia > MAX_LLUVIA_DIA_MM).sum())
    exceso = l[por_dia > MAX_LLUVIA_DIA_MM].assign(dia=lambda d: d["fecha"].dt.normalize())
    if not exceso.empty:
        diario = exceso.groupby(["codigoestacion", "dia"])["valor"].agg(total="sum", n="size").reset_index()
        marcas += [{"motivo": "lluvia_dias_de_mas_de_500_mm", "estacion": f.codigoestacion, "fecha_hora": _texto_de_hora(f.dia), "variable": "lluvia",
                    "valor": round(float(f.total), 1), "conservar_valor": None, "lecturas": int(f.n), "detalle": f"suma {f.total:,.0f} mm en el día".replace(",", ".")}
                   for f in diario.itertuples(index=False)]
    l = l[por_dia <= MAX_LLUVIA_DIA_MM].assign(hora=lambda d: d["fecha"].dt.floor("h"))
    return l.groupby(["codigoestacion", "hora"])["valor"].sum().unstack(0)


def solo_cambios(tabla: pd.DataFrame, publicado: pd.DataFrame):
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


def por_hora(nivel: pd.DataFrame, lluvia: pd.DataFrame, pluviometros: dict, publicado: pd.DataFrame, referencias: dict, estaciones: list[str]):
    """Devuelve (tabla horaria con estacion, fecha_hora, nivel_m y lluvia_mm; conteo de lo que se propone no usar; estaciones sin datos; marcas).

    nivel y lluvia: lo consultado o subido, tal cual llegó. pluviometros: contenido de datos/pluviometros.json.
    publicado: lecturas ya publicadas (estacion, fecha_hora, nivel_m), solo para dar contexto a los picos.
    """
    descartes, marcas = {}, []
    nivel_h = _nivel_por_hora(nivel, referencias, descartes, marcas)
    lluvia_h = _lluvia_por_hora(lluvia, set(pluviometros.get("acumulados", [])), descartes, marcas)
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
        picos, vecinas = _picos_aislados(completa, referencias.get(codigo, {}).get("rango", 1e-6))
        picos, vecinas = picos.reindex(horas, fill_value=False), vecinas.reindex(horas)
        picos_total += int(picos.sum())
        marcas += [{"motivo": "picos_de_una_hora", "estacion": codigo, "fecha_hora": _texto_de_hora(h), "variable": "nivel", "valor": round(float(nivel_e[h]), 3),
                    "conservar_valor": round(float(nivel_e[h]), 3), "lecturas": 1, "detalle": f"las horas vecinas están alrededor de {vecinas[h]:.2f} m"}
                   for h in horas[picos.values]]
        partes.append(pd.DataFrame({"estacion": codigo, "fecha_hora": horas, "nivel_m": nivel_e.where(~picos).round(3).values,
                                    "lluvia_mm": lluv.reindex(horas).round(2).values}).dropna(subset=["nivel_m", "lluvia_mm"], how="all"))
    descartes["picos_de_una_hora"] = picos_total
    tabla = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=["estacion", "fecha_hora", "nivel_m", "lluvia_mm"])
    return tabla, descartes, sin_datos, marcas


def resumir_marcas(marcas: list[dict]) -> list[dict]:
    """Por motivo: cuántas marcas y lecturas hay, con su título y su justificación (lo que se muestra antes de decidir)."""
    cuenta = {}
    for m in marcas:
        c = cuenta.setdefault(m["motivo"], {"marcas": 0, "lecturas": 0})
        c["marcas"] += 1
        c["lecturas"] += m.get("lecturas", 1)
    return [{"motivo": motivo, "titulo": MOTIVOS[motivo]["titulo"], "justificacion": MOTIVOS[motivo]["justificacion"], "conservable": MOTIVOS[motivo]["conservable"],
             **datos} for motivo, datos in cuenta.items() if motivo in MOTIVOS]


def conservar_lecturas(tabla: pd.DataFrame, marcas: list[dict], conservar: list[str]) -> pd.DataFrame:
    """Devuelve la tabla con los valores de los motivos que la persona decidió conservar. Solo los picos se pueden conservar."""
    restauradas = [m for m in marcas if m["motivo"] in conservar and MOTIVOS.get(m["motivo"], {}).get("conservable") and m.get("conservar_valor") is not None]
    if not restauradas:
        return tabla
    llaves = ["estacion", "fecha_hora"]
    nuevo = pd.DataFrame({"estacion": [m["estacion"] for m in restauradas], "fecha_hora": pd.to_datetime([m["fecha_hora"] for m in restauradas]),
                          "nivel_m": [m["conservar_valor"] for m in restauradas]}).set_index(llaves)
    nuevo = nuevo[~nuevo.index.duplicated(keep="last")]
    base = tabla.set_index(llaves)
    base = base.reindex(base.index.union(nuevo.index))            # las horas que quedaron sin fila vuelven a tenerla
    base.loc[nuevo.index, "nivel_m"] = nuevo["nivel_m"]
    return base.reset_index().sort_values(llaves).reset_index(drop=True)[list(tabla.columns)]


def decision_de_lecturas(marcas: list[dict], conservar: list[str]) -> dict:
    """Lo que queda registrado al confirmar: por cada motivo, cuántas lecturas se excluyeron o se conservaron y por qué."""
    decision = {}
    for r in resumir_marcas(marcas):
        queda = r["conservable"] and r["motivo"] in conservar
        decision[r["motivo"]] = {"marcas": r["marcas"], "lecturas": r["lecturas"], "decision": "conservadas" if queda else "excluidas",
                                 "justificacion": ("La persona que revisó el lote decidió conservarlas. " if queda else "") + r["justificacion"]}
    return decision

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
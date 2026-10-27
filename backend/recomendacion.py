"""Recomendación sobre un lote de datos nuevos: agregarlo, revisarlo con cuidado o rechazarlo, siempre con las razones.

Solo aconseja; quien decide es el administrador. Se calcula al consultar el lote, a partir de su resumen,
así que también sirve para lotes guardados antes. No lee ni escribe nada.
"""
from datetime import datetime

import pandas as pd

MAX_DESCARTE_ACEPTABLE = 0.02        
MAX_DESCARTE_GRAVE = 0.10            
MIN_COBERTURA_NIVEL = 0.80           
MIN_COBERTURA_GRAVE = 0.50          
POCAS_HORAS = 24
HORAS_RECIENTES = 24
DIAS_VIEJOS = 7
ORDEN = {"contra": 0, "cuidado": 1, "favor": 2, "nota": 3}
DECISIONES = {
    "rechazar": ("Se recomienda rechazar este lote", "La información es débil. Si lo rechaza, no llega al modelo y se mantiene lo que ya está publicado."),
    "revisar": ("Se recomienda revisarlo con cuidado", "Hay puntos de atención. Confirme el lote solo si los entiende y le parecen aceptables."),
    "confirmar": ("Se puede agregar directamente", "No se ven señales de problemas: la información es suficiente para pasar al modelo."),
}


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _lista(nombres: list[str]) -> str:
    return ", ".join(nombres[:-1]) + f" y {nombres[-1]}" if len(nombres) > 1 else nombres[0]


def recomendar(ficha: dict, nombres: dict, ahora: datetime | None = None) -> dict | None:
    """ficha: el lote tal como lo devuelve lotes.obtener. nombres: código de estación → nombre. None si el lote ya no espera decisión."""
    if ficha["estado"] not in ("pendiente", "con_errores"):
        return None
    ahora = ahora or datetime.now()
    r = ficha["resumen"]
    por_estacion = r.get("por_estacion", [])
    sin_datos = r.get("estaciones_sin_datos", [])
    nombre = lambda codigo: nombres.get(codigo, codigo)
    razones = []
    agregar = lambda tipo, texto: razones.append({"tipo": tipo, "texto": texto})

    errores = (r.get("validacion") or {}).get("errores", 0)
    if errores:
        agregar("contra", f"La revisión automática encontró {errores} error(es) que impiden usar estos datos.")
    if not ficha["filas"]:
        agregar("contra", "El lote no trae ninguna lectura.")

    esperadas, con_datos = len(por_estacion) + len(sin_datos), len(por_estacion)
    if esperadas:
        if con_datos / esperadas < 0.5:
            agregar("contra", f"Solo {con_datos} de {esperadas} estaciones traen datos.")
        elif sin_datos:
            agregar("cuidado", f"No hay datos nuevos de {_lista([nombre(c) for c in sin_datos])}.")
        else:
            agregar("favor", f"Trae datos de {'la única estación' if esperadas == 1 else f'las {esperadas} estaciones'} del modelo.")

    consulta = r.get("consulta") or {}
    lecturas = consulta.get("lecturas_de_nivel", 0) + consulta.get("lecturas_de_lluvia", 0)
    descartadas = sum((r.get("descartes") or {}).values())
    parte = descartadas / lecturas if lecturas else 0
    if parte > MAX_DESCARTE_GRAVE:
        agregar("contra", f"Se descartó el {parte:.0%} de las lecturas por fallas del sensor o valores imposibles.")
    elif parte > MAX_DESCARTE_ACEPTABLE:
        agregar("cuidado", f"Se descartó el {parte:.0%} de las lecturas por fallas del sensor o valores imposibles.")
    elif descartadas:
        agregar("favor", f"Casi no hubo lecturas descartadas por fallas del sensor ({_miles(descartadas)} de {_miles(lecturas)}).")
    else:
        agregar("favor", "No hubo lecturas descartadas por fallas del sensor.")

    horas = sum(e["horas"] for e in por_estacion)
    if horas:
        cobertura = sum(e["horas_con_nivel"] for e in por_estacion) / horas
        if cobertura < MIN_COBERTURA_GRAVE:
            agregar("contra", f"Menos de la mitad de las horas tiene nivel del río ({cobertura:.0%}).")
        elif cobertura < MIN_COBERTURA_NIVEL:
            agregar("cuidado", f"Solo el {cobertura:.0%} de las horas tiene nivel del río.")
        elif cobertura < 1:
            agregar("nota", f"El {1 - cobertura:.0%} de las horas no tiene nivel del río (el sensor no reportó). El modelo las trata como dato faltante.")
    for e in por_estacion:
        if e["horas"] < POCAS_HORAS:
            agregar("nota", f"{nombre(e['estacion'])} trae solo {e['horas']} horas: su sensor parece haber dejado de reportar.")

    nuevas, actualizadas = sum(e["horas_nuevas"] for e in por_estacion), sum(e["horas_actualizadas"] for e in por_estacion)
    if nuevas:
        agregar("favor", f"Agrega {_miles(nuevas)} horas nuevas a lo publicado.")
    elif actualizadas:
        agregar("favor", f"No agrega horas nuevas, pero completa {_miles(actualizadas)} horas ya publicadas con datos que faltaban (por ejemplo, lluvia).")

    if r.get("hasta"):
        atraso = ahora - pd.Timestamp(r["hasta"]).to_pydatetime()
        if atraso.days >= DIAS_VIEJOS:
            agregar("cuidado", f"Los datos más recientes tienen {atraso.days} días de antigüedad.")
        elif atraso.total_seconds() > HORAS_RECIENTES * 3600:
            agregar("nota", "Lo más reciente tiene más de un día: es lo último que ha publicado el IDEAM.")
    if r.get("combina"):
        agregar("nota", "Combina datos de varias consultas; la comparación está más abajo.")

    razones.sort(key=lambda x: ORDEN[x["tipo"]])
    accion = "rechazar" if any(x["tipo"] == "contra" for x in razones) else "revisar" if any(x["tipo"] == "cuidado" for x in razones) else "confirmar"
    titulo, texto = DECISIONES[accion]
    return {"accion": accion, "titulo": titulo, "texto": texto, "razones": razones}

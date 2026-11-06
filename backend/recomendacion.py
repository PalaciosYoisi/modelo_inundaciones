"""Recomendación sobre un lote de datos nuevos: agregarlo, revisarlo con cuidado o rechazarlo, siempre con las razones.

Solo aconseja; quien decide es el administrador. Se calcula al consultar el lote, a partir de su resumen,
así que también sirve para lotes guardados antes. No lee ni escribe nada.
"""
from datetime import datetime

import pandas as pd

from hora import hora_actual

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
    ahora = ahora or hora_actual()
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


# ------------------------------------------------------------------ nueva versión del modelo
DIFERENCIA_PUNTAJE = 0.02            # cuánto tiene que cambiar el puntaje del rojo para que no sea casualidad
DIFERENCIA_CRECIDAS = 0.03           # lo mismo para la parte de crecidas avisadas (3 de cada 100)
CAIDA_EN_DATOS_NUEVOS = 0.10         # cuánto puede bajar la versión en uso con datos recientes antes de considerarla en problemas
POCAS_HORAS_NUEVAS = 24 * 30
DECISIONES_VERSION = {
    "poner_en_uso": ("Se recomienda poner la versión nueva en uso", "Mejora lo que más importa, avisar las crecidas, y no empeora nada importante."),
    "revisar": ("Conviene decidir con cuidado", "Hay mejoras, pero también puntos de atención. Si tiene dudas, mantenga la versión en uso y vuelva a actualizar cuando haya más datos."),
    "mantener": ("Se recomienda mantener la versión en uso", "La versión nueva no mejora lo que más importa. Puede descartarla o dejarla guardada para comparar más adelante."),
}


def _metricas(metadatos: dict) -> dict:
    return (metadatos.get("metricas_validacion") or {}).get("Bosque aleatorio") or {}


def recomendar_version(candidata: dict, en_uso: dict) -> dict:
    """Qué aconseja el sistema sobre una versión candidata frente a la que está en uso, con las razones. candidata y en_uso: sus metadatos.

    Pesa más avisar las crecidas que evitar falsas alarmas: una crecida que no se avisa cuesta más que una alarma de más.
    Solo aconseja; quien decide es el administrador.
    """
    nueva, actual = _metricas(candidata), _metricas(en_uso)
    razones, mejoras = [], []                                # mejoras: lo que la versión nueva hace mejor por sí misma

    def agregar(tipo: str, texto: str, mejora: bool = False) -> None:
        razones.append({"tipo": tipo, "texto": texto})
        if mejora:
            mejoras.append(texto)

    cien = lambda valor: round(valor * 100)
    coma = lambda valor: f"{valor:.2f}".replace(".", ",")

    avisadas_ahora, avisadas_antes = nueva.get("Rojos detectados"), actual.get("Rojos detectados")
    if avisadas_ahora is not None and avisadas_antes is not None:
        cambio = avisadas_ahora - avisadas_antes
        if cambio <= -DIFERENCIA_CRECIDAS:
            agregar("contra", f"Avisa menos crecidas: de cada 100, avisa {cien(avisadas_ahora)} y la versión en uso avisa {cien(avisadas_antes)}.")
        elif cambio >= DIFERENCIA_CRECIDAS:
            agregar("favor", f"Avisa más crecidas: de cada 100, avisa {cien(avisadas_ahora)} y la versión en uso avisa {cien(avisadas_antes)}.", True)
        else:
            agregar("nota", f"Avisa casi las mismas crecidas que la versión en uso (unas {cien(avisadas_ahora)} de cada 100).")

    puntaje_ahora, puntaje_antes = nueva.get("F1 del rojo"), actual.get("F1 del rojo")
    if puntaje_ahora is not None and puntaje_antes is not None:
        cambio = puntaje_ahora - puntaje_antes
        if cambio <= -DIFERENCIA_PUNTAJE:
            agregar("contra", f"El puntaje del rojo baja de {coma(puntaje_antes)} a {coma(puntaje_ahora)}.")
        elif cambio >= DIFERENCIA_PUNTAJE:
            agregar("favor", f"El puntaje del rojo sube de {coma(puntaje_antes)} a {coma(puntaje_ahora)}.", True)
        else:
            agregar("nota", f"El puntaje del rojo casi no cambia ({coma(puntaje_antes)} contra {coma(puntaje_ahora)}).")

    falsas_ahora, falsas_antes = nueva.get("Falsas alarmas"), actual.get("Falsas alarmas")
    if falsas_ahora is not None and falsas_antes:
        if falsas_ahora > falsas_antes * 1.2:
            agregar("cuidado", f"Da más falsas alarmas: {round(falsas_ahora)} contra {round(falsas_antes)} de la versión en uso.")
        elif falsas_ahora < falsas_antes * 0.8:
            agregar("favor", f"Da menos falsas alarmas: {round(falsas_ahora)} contra {round(falsas_antes)} de la versión en uso.", True)

    datos = candidata.get("reentrenamiento") or {}
    horas = datos.get("horas_nuevas_con_nivel")
    if horas is not None and horas < POCAS_HORAS_NUEVAS:
        agregar("nota", f"Solo hay {horas} horas de datos nuevos (unos {max(1, round(horas / 24))} días). Con tan poca información, una diferencia pequeña puede ser casualidad.")

    if "modelo_anterior_en_datos_nuevos" in datos:
        reciente = datos["modelo_anterior_en_datos_nuevos"]
        if reciente is None:
            agregar("nota", "No hubo suficientes datos nuevos para medir cómo le va a la versión en uso con lo más reciente.")
        elif not reciente.get("con_rojo"):
            agregar("nota", "En los datos nuevos no hubo crecidas, así que no se puede comprobar si el modelo las avisa.")
        elif puntaje_antes is not None and reciente["F1 del rojo"] < puntaje_antes - CAIDA_EN_DATOS_NUEVOS:
            agregar("favor", f"La versión en uso está fallando con los datos recientes (puntaje {coma(reciente['F1 del rojo'])} contra {coma(puntaje_antes)} cuando se entrenó): conviene renovarla.")
        else:
            agregar("nota", f"La versión en uso sigue funcionando con los datos recientes (puntaje {coma(reciente['F1 del rojo'])}).")

    agregar("nota", "Las dos versiones se probaron con periodos distintos (la nueva incluye los datos más recientes), así que la comparación es una guía y no una prueba exacta.")
    razones.sort(key=lambda x: ORDEN[x["tipo"]])
    tipos = {x["tipo"] for x in razones}
    if "contra" in tipos:
        accion = "mantener"
    elif mejoras:
        accion = "revisar" if "cuidado" in tipos else "poner_en_uso"
    else:
        accion = "revisar" if "favor" in tipos else "mantener"                   # sin mejoras propias, solo dudas sobre la versión en uso
    titulo, texto = DECISIONES_VERSION[accion]
    return {"accion": accion, "titulo": titulo, "texto": texto, "razones": razones}

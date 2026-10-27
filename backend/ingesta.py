"""Unifica datos nuevos (de la API de datos.gov.co o de archivos subidos) y los deja como un lote pendiente de revisión.

Es el único camino por el que llegan datos nuevos: consulta diaria y archivos pasan por aquí, con la misma limpieza,
la misma revisión y la misma recomendación. No publica nada: confirmar un lote es decisión de un administrador.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import lotes
from preparacion import por_hora, referencias_de, resumir, solo_cambios
from publicacion import HORAS_A_CONSERVAR
from validacion import validar

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_MODELOS = Path(os.environ.get("SEMAFORO_MODELOS", RAIZ / "modelos"))
PUBLICADO_ANTERIOR = RAIZ / "datos" / "publicado" / "lecturas.csv"       # lo publicado en versiones anteriores, que guardaban un archivo
DATOS_INICIALES = RAIZ / "datos" / "datos_horarios.csv"
PLUVIOMETROS = RAIZ / "datos" / "pluviometros.json"
LLAVES = ["estacion", "fecha_hora"]
COLUMNAS_CRUDAS = ["codigoestacion", "codigosensor", "fechaobservacion", "valorobservado"]


class ErrorDeIngesta(Exception):
    """Falta algo para poder preparar los datos. El mensaje es apto para mostrarlo."""


def contexto(pipeline, publicacion) -> SimpleNamespace:
    """Lo que hace falta para preparar un lote: el modelo, los pluviómetros de cada estación y lo ya publicado."""
    if not PLUVIOMETROS.exists():
        raise ErrorDeIngesta("Falta datos/pluviometros.json. Se crea al correr preparar_datos.py.")
    codigos = pipeline.codigos
    mapa = json.loads(PLUVIOMETROS.read_text(encoding="utf-8"))
    return SimpleNamespace(pipeline=pipeline, mapa=mapa, publicado=publicacion.tabla(codigos), codigos=codigos,
                           pluviometros=sorted({p for c in codigos for p in mapa["estaciones"].get(c, [])}))


def _sin_antiguas(tabla: pd.DataFrame, publicado: pd.DataFrame, descartes: dict) -> pd.DataFrame:
    """Lo publicado solo conserva los últimos 120 días de cada estación; lo más viejo no tiene sentido llevarlo al lote."""
    ultima = pd.concat([tabla[LLAVES], publicado[LLAVES]]).groupby("estacion")["fecha_hora"].max()
    limite = tabla["estacion"].map(ultima) - pd.Timedelta(hours=HORAS_A_CONSERVAR)
    vigente = tabla["fecha_hora"] >= limite
    descartes["lecturas_de_mas_de_120_dias"] = int((~vigente).sum())
    return tabla[vigente].reset_index(drop=True)


def crear_lote(origen: str, nivel: pd.DataFrame, lluvia: pd.DataFrame, combinado: pd.DataFrame | None, ctx: SimpleNamespace, extra: dict | None = None) -> dict:
    """Prepara lo recibido y lo guarda como lote. nivel y lluvia: lecturas tal como llegaron (pueden estar vacías); combinado: tabla ya horaria.

    Devuelve {estado, mensaje, ...}: lote_pendiente, lote_con_errores o sin_cambios. Si ya había un lote pendiente, se combinan y se deja constancia.
    """
    pipeline = ctx.pipeline
    tabla, descartes, sin_datos = por_hora(nivel, lluvia, ctx.mapa, ctx.publicado, referencias_de(pipeline.metadatos), ctx.codigos)
    if combinado is not None and len(combinado):
        tabla = tabla.set_index(LLAVES)[["nivel_m", "lluvia_mm"]].combine_first(combinado.set_index(LLAVES)[["nivel_m", "lluvia_mm"]]).reset_index()
        sin_datos = [c for c in sin_datos if c not in set(tabla["estacion"])]
    if tabla.empty:
        return {"estado": "sin_cambios", "mensaje": "Los datos recibidos no traen lecturas de las estaciones del modelo."}
    tabla = _sin_antiguas(tabla.sort_values(LLAVES), ctx.publicado, descartes)
    tabla, _ = solo_cambios(tabla, ctx.publicado)
    resumen = {**resumir(tabla, ctx.publicado), "estaciones_sin_datos": sin_datos, "descartes": descartes, **(extra or {})}
    resumen["consulta"] = {**resumen.get("consulta", {}), "modelo_version": pipeline.version}
    if tabla.empty:
        return {"estado": "sin_cambios", "mensaje": "No hay nada nuevo: todo lo recibido ya está publicado."}
    diagnostico = validar(tabla.assign(fecha_hora=tabla["fecha_hora"].dt.strftime("%Y-%m-%d %H:%M:%S")), pipeline.metadatos, exigir_historia=False)
    resumen["validacion"] = {"estado": diagnostico["estado"], **diagnostico["resumen"],
                             "hallazgos": [{k: h.get(k) for k in ("id", "severidad", "variable", "mensaje")} for h in diagnostico["hallazgos"]]}
    estado = "con_errores" if diagnostico["resumen"]["errores"] else "pendiente"
    anteriores = lotes.pendientes_con_datos() if estado == "pendiente" else []
    if anteriores:                        # ya había datos esperando revisión: se combinan, dejando a la vista que son varias entregas
        tabla, resumen["combina"] = lotes.unificar(tabla, anteriores)
        resumen.update(resumir(tabla, ctx.publicado))
    id_lote = lotes.crear(origen, estado, tabla, resumen, unifica=[a["id"] for a in anteriores])
    mensaje = "Hay un lote nuevo esperando revisión." if estado == "pendiente" else "Los datos nuevos traen errores y no se pueden confirmar."
    if anteriores:
        mensaje = f"Los datos nuevos se combinaron con el lote pendiente anterior ({', '.join(str(a['id']) for a in anteriores)}). Revise la comparación."
    return {"estado": f"lote_{estado}", "lote": id_lote, "filas": len(tabla), "mensaje": mensaje, "unifica": [a["id"] for a in anteriores]}

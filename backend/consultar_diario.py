"""Consulta diaria: trae de datos.gov.co lo nuevo y lo deja como un lote pendiente para que un administrador lo revise.

No publica nada y no depende de que la API esté encendida. Si algo falla, lo ya publicado no cambia.
Uso:  python consultar_diario.py

Para que corra solo cada día (Programador de tareas de Windows), vea el README.
"""
import sys
from datetime import datetime, timedelta

import pandas as pd

import auditoria
import ingesta
from base_datos import ErrorBaseDatos
from consulta import ErrorDeConsulta, consultar
from pipeline import PipelineSemaforo, version_activa
from publicacion import Publicacion

SOLAPE = timedelta(hours=48)              # se vuelve a pedir un poco de lo ya publicado, por si el IDEAM corrigió lecturas
SIN_HISTORIA = timedelta(days=7)


def _auditar(accion: str, detalle: dict) -> None:
    try:
        auditoria.registrar("sistema", accion, detalle)
    except ErrorBaseDatos:
        pass                              # la consulta no debe fallar por no poder anotar


def _desde(publicado: pd.DataFrame, columna: str, codigos: list[str], ahora: datetime) -> pd.Timestamp:
    ultima = publicado.dropna(subset=[columna]).groupby("estacion")["fecha_hora"].max()
    return pd.Timestamp(min(ultima.get(c, ahora - SIN_HISTORIA) - SOLAPE for c in codigos)).floor("h")


def ejecutar(ahora: datetime | None = None) -> dict:
    """Hace la consulta completa. Devuelve {estado, mensaje, ...}: lote_pendiente, lote_con_errores, sin_cambios o error."""
    ahora = ahora or datetime.now()
    try:
        pipeline = PipelineSemaforo.cargar(ingesta.CARPETA_MODELOS / version_activa(ingesta.CARPETA_MODELOS))
        ctx = ingesta.contexto(pipeline, Publicacion(ingesta.DATOS_INICIALES, ingesta.PUBLICADO_ANTERIOR))
        desde_nivel, desde_lluvia = _desde(ctx.publicado, "nivel_m", ctx.codigos, ahora), _desde(ctx.publicado, "lluvia_mm", ctx.codigos, ahora)
        nivel = consultar("nivel", ctx.codigos, desde_nivel)
        lluvia = consultar("lluvia", ctx.pluviometros, desde_lluvia)
        resultado = ingesta.crear_lote("api", nivel, lluvia, None, ctx, extra={"consulta": {
            "lecturas_de_nivel": len(nivel), "lecturas_de_lluvia": len(lluvia), "nivel_desde": desde_nivel, "lluvia_desde": desde_lluvia}})
        _auditar("consulta_diaria", {"resultado": resultado["estado"].removeprefix("lote_"), "lote": resultado.get("lote"), "filas": resultado.get("filas"),
                                     "unifica": resultado.get("unifica", [])})
        return resultado
    except (ErrorDeConsulta, ErrorBaseDatos, ingesta.ErrorDeIngesta) as error:
        _auditar("consulta_diaria", {"resultado": "error", "motivo": str(error)})
        return {"estado": "error", "mensaje": str(error)}
    except Exception as error:            # cualquier falla inesperada se informa sin tocar lo publicado
        _auditar("consulta_diaria", {"resultado": "error", "motivo": type(error).__name__})
        return {"estado": "error", "mensaje": "La consulta falló por un problema inesperado. Lo publicado no cambió."}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    resultado = ejecutar()
    print(f"[{resultado['estado']}] {resultado['mensaje']}" + (f" (lote {resultado['lote']}, {resultado['filas']} filas)" if "lote" in resultado else ""))

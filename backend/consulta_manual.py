"""Consulta manual: trae de datos.gov.co lo nuevo y lo deja como un lote pendiente para que un administrador lo revise.

La inicia un administrador con el botón «Consultar ahora» del panel; nunca corre sola. No publica nada: si algo falla, lo ya publicado no cambia.
"""
from datetime import datetime, timedelta

import pandas as pd

import ingesta
import versiones
from base_datos import ErrorBaseDatos
from consulta import ErrorDeConsulta, consultar
from hora import hora_actual
from publicacion import Publicacion

SOLAPE = timedelta(hours=48)              # se vuelve a pedir un poco de lo ya publicado, por si el IDEAM corrigió lecturas
SIN_HISTORIA = timedelta(days=7)


def _desde(publicado: pd.DataFrame, columna: str, codigos: list[str], ahora: datetime) -> pd.Timestamp:
    ultima = publicado.dropna(subset=[columna]).groupby("estacion")["fecha_hora"].max()
    return pd.Timestamp(min(ultima.get(c, ahora - SIN_HISTORIA) - SOLAPE for c in codigos)).floor("h")


def ejecutar(ahora: datetime | None = None) -> dict:
    """Hace la consulta completa. Devuelve {estado, mensaje, ...}: lote_pendiente, lote_con_errores, sin_cambios o error."""
    ahora = ahora or hora_actual()
    try:
        pipeline = versiones.cargar(versiones.activa(ingesta.CARPETA_MODELOS), ingesta.CARPETA_MODELOS)
        ctx = ingesta.contexto(pipeline, Publicacion(ingesta.DATOS_INICIALES, ingesta.PUBLICADO_ANTERIOR))
        desde_nivel, desde_lluvia = _desde(ctx.publicado, "nivel_m", ctx.codigos, ahora), _desde(ctx.publicado, "lluvia_mm", ctx.codigos, ahora)
        nivel = consultar("nivel", ctx.codigos, desde_nivel)
        lluvia = consultar("lluvia", ctx.pluviometros, desde_lluvia)
        return ingesta.crear_lote("api", nivel, lluvia, None, ctx, extra={"consulta": {
            "lecturas_de_nivel": len(nivel), "lecturas_de_lluvia": len(lluvia), "nivel_desde": desde_nivel, "lluvia_desde": desde_lluvia}})
    except (ErrorDeConsulta, ErrorBaseDatos, ingesta.ErrorDeIngesta) as error:
        return {"estado": "error", "mensaje": str(error)}
    except Exception:                     # cualquier falla inesperada se informa sin tocar lo publicado
        return {"estado": "error", "mensaje": "La consulta falló por un problema inesperado. Lo publicado no cambió."}
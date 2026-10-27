"""Consulta diaria: trae de datos.gov.co lo nuevo, lo prepara y deja un lote pendiente para que un administrador lo revise.

No publica nada y no depende de que la API esté encendida. Si algo falla, lo ya publicado no cambia.
Uso:  python consultar_diario.py

Para que corra solo cada día (Programador de tareas de Windows), en una terminal:
    schtasks /Create /SC DAILY /ST 07:00 /TN "Consulta diaria semaforo" /TR "python C:\\ruta\\al\\proyecto\\backend\\consultar_diario.py"
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import auditoria
import lotes
from base_datos import ErrorBaseDatos
from consulta import ErrorDeConsulta, consultar
from pipeline import PipelineSemaforo, version_activa
from preparacion import preparar, referencias_de, resumir
from publicacion import Publicacion
from validacion import validar

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_MODELOS = Path(os.environ.get("SEMAFORO_MODELOS", RAIZ / "modelos"))
CARPETA_PUBLICADO = Path(os.environ.get("SEMAFORO_PUBLICADO", RAIZ / "datos" / "publicado"))
DATOS_INICIALES = RAIZ / "datos" / "datos_horarios.csv"
PLUVIOMETROS = RAIZ / "datos" / "pluviometros.json"
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
        pipeline = PipelineSemaforo.cargar(CARPETA_MODELOS / version_activa(CARPETA_MODELOS))
        if not PLUVIOMETROS.exists():
            return {"estado": "error", "mensaje": "Falta datos/pluviometros.json. Se crea al correr preparar_datos.py."}
        mapa = json.loads(PLUVIOMETROS.read_text(encoding="utf-8"))
        codigos = pipeline.codigos
        publicado = Publicacion(CARPETA_PUBLICADO, DATOS_INICIALES).tabla(codigos)
        desde_nivel, desde_lluvia = _desde(publicado, "nivel_m", codigos, ahora), _desde(publicado, "lluvia_mm", codigos, ahora)
        pluviometros = sorted({p for c in codigos for p in mapa["estaciones"].get(c, [])})
        nivel = consultar("nivel", codigos, desde_nivel)
        lluvia = consultar("lluvia", pluviometros, desde_lluvia)
        tabla, resumen = preparar(nivel, lluvia, mapa, publicado, referencias_de(pipeline.metadatos), codigos)
        resumen["consulta"] = {"lecturas_de_nivel": len(nivel), "lecturas_de_lluvia": len(lluvia), "nivel_desde": desde_nivel, "lluvia_desde": desde_lluvia,
                               "modelo_version": pipeline.version}
        if tabla.empty:
            _auditar("consulta_diaria", {"resultado": "sin_cambios", **resumen["consulta"]})
            return {"estado": "sin_cambios", "mensaje": "No hay datos nuevos en datos.gov.co desde la última publicación."}
        diagnostico = validar(tabla.assign(fecha_hora=tabla["fecha_hora"].dt.strftime("%Y-%m-%d %H:%M:%S")), pipeline.metadatos, exigir_historia=False)
        resumen["validacion"] = {"estado": diagnostico["estado"], **diagnostico["resumen"],
                                 "hallazgos": [{k: h.get(k) for k in ("id", "severidad", "variable", "mensaje")} for h in diagnostico["hallazgos"]]}
        estado = "con_errores" if diagnostico["resumen"]["errores"] else "pendiente"
        anteriores = lotes.pendientes_con_datos() if estado == "pendiente" else []
        if anteriores:                    # ya había datos esperando revisión: se combinan con los nuevos, dejando a la vista que son varias consultas
            tabla, resumen["combina"] = lotes.unificar(tabla, anteriores)
            resumen.update(resumir(tabla, publicado))
        id_lote = lotes.crear("api", estado, tabla, resumen, unifica=[a["id"] for a in anteriores])
        _auditar("consulta_diaria", {"resultado": estado, "lote": id_lote, "filas": len(tabla), "unifica": [a["id"] for a in anteriores]})
        mensaje = ("Hay un lote nuevo esperando revisión." if estado == "pendiente" else "Los datos nuevos traen errores y no se pueden confirmar.")
        if anteriores:
            mensaje = f"Los datos nuevos se combinaron con el lote pendiente anterior ({', '.join(str(a['id']) for a in anteriores)}). Revise la comparación."
        return {"estado": f"lote_{estado}", "lote": id_lote, "filas": len(tabla), "mensaje": mensaje}
    except (ErrorDeConsulta, ErrorBaseDatos) as error:
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

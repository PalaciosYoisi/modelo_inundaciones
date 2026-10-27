"""Lotes: grupos de lecturas nuevas que esperan la decisión de un administrador antes de llegar al modelo.

Estados: pendiente (esperando decisión), con_errores (no se puede confirmar), confirmado, rechazado y
reemplazado (llegó un lote más nuevo que lo incluye). La ficha del lote vive en MySQL; sus lecturas, en datos/lotes/.
Este módulo no publica nada: confirmar un lote (pasarlo a las lecturas publicadas) lo decide la API.
"""
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from base_datos import conexion

RAIZ = Path(__file__).resolve().parents[1]
CARPETA = Path(os.environ.get("SEMAFORO_LOTES", RAIZ / "datos" / "lotes"))
ESTADOS_FINALES = ("confirmado", "rechazado")


def crear(origen: str, estado: str, tabla: pd.DataFrame, resumen: dict) -> int:
    """Guarda un lote nuevo. Un lote pendiente anterior queda reemplazado, porque el nuevo parte de la misma última hora publicada."""
    CARPETA.mkdir(parents=True, exist_ok=True)
    with conexion() as cursor:
        if estado == "pendiente":
            cursor.execute("UPDATE lotes SET estado='reemplazado' WHERE estado='pendiente'")
        cursor.execute("INSERT INTO lotes (creado, origen, estado, desde, hasta, filas, resumen, archivo) VALUES (%s, %s, %s, %s, %s, %s, %s, '')",
                       (datetime.now(), origen, estado, resumen.get("desde"), resumen.get("hasta"), len(tabla), json.dumps(resumen, ensure_ascii=False, default=str)))
        id_lote = cursor.lastrowid
        archivo = f"lote_{id_lote}.csv"
        tabla.to_csv(CARPETA / archivo, index=False)
        cursor.execute("UPDATE lotes SET archivo=%s WHERE id=%s", (archivo, id_lote))
    return id_lote


def _ficha(fila: dict) -> dict:
    return {**fila, "resumen": json.loads(fila["resumen"] or "{}")}


def listar(estado: str | None = None, limite: int = 20) -> list[dict]:
    with conexion() as cursor:
        cursor.execute(f"SELECT id, creado, origen, estado, desde, hasta, filas, decidido_por, decidido_en FROM lotes {'WHERE estado=%s' if estado else ''} "
                       "ORDER BY id DESC LIMIT %s", (estado, limite) if estado else (limite,))
        return cursor.fetchall()


def obtener(id_lote: int) -> dict | None:
    with conexion() as cursor:
        cursor.execute("SELECT * FROM lotes WHERE id=%s", (id_lote,))
        fila = cursor.fetchone()
    return None if fila is None else _ficha(fila)


def datos(id_lote: int) -> pd.DataFrame:
    ficha = obtener(id_lote)
    if ficha is None or not (CARPETA / ficha["archivo"]).exists():
        raise FileNotFoundError(id_lote)
    return pd.read_csv(CARPETA / ficha["archivo"], dtype={"estacion": str}, parse_dates=["fecha_hora"])


def pendientes() -> int:
    with conexion() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM lotes WHERE estado='pendiente'")
        return cursor.fetchone()["n"]


def diario(tabla: pd.DataFrame) -> list[dict]:
    """Resumen por estación y día (nivel mínimo y máximo, lluvia total) para las gráficas y la tabla de revisión."""
    d = tabla.assign(dia=tabla["fecha_hora"].dt.strftime("%Y-%m-%d"))
    g = d.groupby(["estacion", "dia"]).agg(nivel_min=("nivel_m", "min"), nivel_max=("nivel_m", "max"), lluvia_mm=("lluvia_mm", "sum"), horas=("fecha_hora", "size")).reset_index()
    return g.round(3).to_dict(orient="records")


def reabrir(id_lote: int) -> None:
    """Devuelve a pendiente un lote que se confirmó pero cuya publicación falló."""
    with conexion() as cursor:
        cursor.execute("UPDATE lotes SET estado='pendiente', decidido_por=NULL, decidido_en=NULL WHERE id=%s AND estado='confirmado'", (id_lote,))


def decidir(id_lote: int, estado: str, usuario: str) -> bool:
    """Confirma o rechaza un lote. Solo se puede decidir sobre uno pendiente; devuelve False si ya no lo estaba."""
    if estado not in ESTADOS_FINALES:
        raise ValueError(estado)
    with conexion() as cursor:
        cursor.execute("UPDATE lotes SET estado=%s, decidido_por=%s, decidido_en=%s WHERE id=%s AND estado='pendiente'", (estado, usuario, datetime.now(), id_lote))
        return cursor.rowcount == 1

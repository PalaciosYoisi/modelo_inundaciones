"""Lotes: grupos de lecturas nuevas que esperan la decisión de un administrador antes de llegar al modelo.

Estados: pendiente (esperando decisión), con_errores (no se puede confirmar), confirmado, rechazado y
unificado (sus datos se combinaron con los de una consulta más nueva, que quedó como lote pendiente). La ficha del lote y sus lecturas viven en MySQL.
Este módulo no publica nada: confirmar un lote (pasarlo a las lecturas publicadas) lo decide la API.
"""
import io
import json
import os
from pathlib import Path

import pandas as pd
import pymysql

from base_datos import ERROR_COLUMNA_REPETIDA, conexion
from hora import hora_actual
from preparacion import MAX_MARCAS_GUARDADAS

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_ANTERIOR = Path(os.environ.get("SEMAFORO_LOTES", RAIZ / "datos" / "lotes"))      # lotes guardados como archivo en versiones anteriores
ESTADOS_FINALES = ("confirmado", "rechazado")
_columna_lista = False                                                # la columna de las marcas ya se comprobó en este proceso


def _preparar(cursor) -> None:
    """Agrega la columna de las marcas a los lotes guardados antes de que existiera."""
    global _columna_lista
    if not _columna_lista:
        try:
            cursor.execute("ALTER TABLE lotes ADD COLUMN marcas LONGTEXT NULL")
        except pymysql.err.OperationalError as error:
            if error.args[0] != ERROR_COLUMNA_REPETIDA:
                raise
        _columna_lista = True


def crear(origen: str, estado: str, tabla: pd.DataFrame, resumen: dict, unifica: list[int] = (), marcas: list[dict] | None = None) -> int:
    """Guarda un lote nuevo. Los lotes pendientes que se combinaron en él (unifica) quedan como unificados y apuntan al nuevo.

    marcas: las lecturas que el sistema propone no usar (con su motivo y justificación). Se guardan completas hasta cierto límite;
    el conteo de todas queda siempre en el resumen.
    """
    marcas = marcas or []
    with conexion() as cursor:
        _preparar(cursor)
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 AS siguiente FROM lotes")      # numeración corrida (Lote 1, Lote 2...), sin los saltos que hace la base en la nube
        id_lote = cursor.fetchone()["siguiente"]
        cursor.execute("INSERT INTO lotes (id, creado, origen, estado, desde, hasta, filas, resumen, archivo, datos, marcas) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '', %s, %s)",
                       (id_lote, hora_actual(), origen, estado, resumen.get("desde"), resumen.get("hasta"), len(tabla), json.dumps(resumen, ensure_ascii=False, default=str),
                        tabla.to_csv(index=False), json.dumps(marcas[:MAX_MARCAS_GUARDADAS], ensure_ascii=False, default=str)))
        for anterior in unifica:
            cursor.execute("SELECT resumen FROM lotes WHERE id=%s", (anterior,))
            ficha = json.loads(cursor.fetchone()["resumen"] or "{}")
            cursor.execute("UPDATE lotes SET estado='unificado', resumen=%s WHERE id=%s AND estado='pendiente'",
                           (json.dumps({**ficha, "unificado_en": id_lote}, ensure_ascii=False, default=str), anterior))
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
        cursor.execute("SELECT id, creado, origen, estado, desde, hasta, filas, resumen, decidido_por, decidido_en FROM lotes WHERE id=%s", (id_lote,))
        fila = cursor.fetchone()
    return None if fila is None else _ficha(fila)


def datos(id_lote: int) -> pd.DataFrame:
    with conexion() as cursor:
        cursor.execute("SELECT datos, archivo FROM lotes WHERE id=%s", (id_lote,))
        fila = cursor.fetchone()
    if fila is None:
        raise FileNotFoundError(id_lote)
    if fila["datos"]:
        origen = io.StringIO(fila["datos"])
    elif fila["archivo"] and (CARPETA_ANTERIOR / fila["archivo"]).exists():          # lote guardado como archivo en una versión anterior
        origen = CARPETA_ANTERIOR / fila["archivo"]
    else:
        raise FileNotFoundError(id_lote)
    return pd.read_csv(origen, dtype={"estacion": str}, parse_dates=["fecha_hora"])


def marcas_de(id_lote: int) -> list[dict]:
    """Las lecturas que el sistema marcó en ese lote. Los lotes anteriores a las marcas devuelven una lista vacía."""
    with conexion() as cursor:
        _preparar(cursor)
        cursor.execute("SELECT marcas FROM lotes WHERE id=%s", (id_lote,))
        fila = cursor.fetchone()
    return json.loads(fila["marcas"]) if fila and fila["marcas"] else []


def unir_marcas(nuevas: list[dict], anteriores: list[dict]) -> list[dict]:
    """Marcas de la consulta nueva más las de los lotes pendientes que se combinan con ella. Si una hora está en las dos, vale la más nueva."""
    clave = lambda m: (m["motivo"], m["estacion"], m["fecha_hora"], m["variable"])
    unidas = {clave(m): m for a in sorted(anteriores, key=lambda x: x["id"]) for m in a.get("marcas", [])}
    unidas.update({clave(m): m for m in nuevas})
    return list(unidas.values())


def guardar_datos(id_lote: int, tabla: pd.DataFrame) -> None:
    """Deja en el lote la tabla tal como se publicó (por ejemplo, con los picos que la persona decidió conservar)."""
    with conexion() as cursor:
        cursor.execute("UPDATE lotes SET datos=%s, filas=%s WHERE id=%s", (tabla.to_csv(index=False), len(tabla), id_lote))


def guardar_decision(id_lote: int, decision: dict) -> None:
    """Anota en la ficha del lote qué se hizo con cada grupo de lecturas marcadas y por qué."""
    with conexion() as cursor:
        cursor.execute("SELECT resumen FROM lotes WHERE id=%s", (id_lote,))
        ficha = json.loads(cursor.fetchone()["resumen"] or "{}")
        cursor.execute("UPDATE lotes SET resumen=%s WHERE id=%s", (json.dumps({**ficha, "decision_de_lecturas": decision}, ensure_ascii=False, default=str), id_lote))


def pendientes() -> int:
    with conexion() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM lotes WHERE estado='pendiente'")
        return cursor.fetchone()["n"]


def pendientes_con_datos() -> list[dict]:
    """Los lotes pendientes con su tabla de lecturas (para combinarlos con una consulta nueva)."""
    with conexion() as cursor:
        _preparar(cursor)
        cursor.execute("SELECT id, creado, marcas FROM lotes WHERE estado='pendiente' ORDER BY id")
        fichas = cursor.fetchall()
    resultado = []
    for ficha in fichas:
        try:
            resultado.append({"id": ficha["id"], "creado": ficha["creado"], "marcas": json.loads(ficha["marcas"]) if ficha["marcas"] else [], "tabla": datos(ficha["id"])})
        except FileNotFoundError:
            continue
    return resultado


def unificar(nueva: pd.DataFrame, anteriores: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    """Combina la consulta nueva con los lotes pendientes. Si una hora aparece en ambos, vale la consulta más nueva
    (y si la nueva no trae un valor, se conserva el del lote anterior). Devuelve la tabla combinada y, por cada lote
    anterior, la comparación: cuántas horas coinciden, cuántas traen un valor distinto y cuántas están solo en un lado."""
    llaves = ["estacion", "fecha_hora"]
    distinto = lambda a, b, tolerancia: (a.isna() != b.isna()) | ((a - b).abs() > tolerancia)
    combinada, comparacion = nueva, []
    for anterior in sorted(anteriores, key=lambda x: x["id"]):
        previo = anterior["tabla"]
        cruce = previo.merge(combinada, on=llaves, how="outer", suffixes=("_ant", "_nue"), indicator=True)
        ambos = cruce[cruce["_merge"] == "both"]
        cambian = (distinto(ambos["nivel_m_ant"], ambos["nivel_m_nue"], 1e-3) | distinto(ambos["lluvia_mm_ant"], ambos["lluvia_mm_nue"], 1e-2)).sum()
        comparacion.append({"lote": anterior["id"], "consultado": anterior["creado"], "horas": len(previo), "iguales": int(len(ambos) - cambian),
                            "distintas": int(cambian), "solo_en_anterior": int((cruce["_merge"] == "left_only").sum()),
                            "solo_en_nueva": int((cruce["_merge"] == "right_only").sum())})
        combinada = combinada.set_index(llaves).combine_first(previo.set_index(llaves)).reset_index().sort_values(llaves).reset_index(drop=True)
    return combinada, comparacion


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
        cursor.execute("UPDATE lotes SET estado=%s, decidido_por=%s, decidido_en=%s WHERE id=%s AND estado='pendiente'", (estado, usuario, hora_actual(), id_lote))
        return cursor.rowcount == 1

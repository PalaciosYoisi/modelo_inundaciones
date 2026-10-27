"""Auditoría: quién hizo qué y cuándo. Solo se agregan registros y se consultan; nunca se editan ni se borran desde la API."""
import json
from datetime import datetime

from base_datos import conexion


def registrar(usuario: str, accion: str, detalle: dict | None = None, ip: str | None = None) -> None:
    with conexion() as cursor:
        cursor.execute("INSERT INTO auditoria (fecha, usuario, accion, detalle, ip) VALUES (%s, %s, %s, %s, %s)",
                       (datetime.now(), (usuario or "?")[:30], accion[:40], json.dumps(detalle or {}, ensure_ascii=False, default=str), ip))


def listar(usuario: str | None = None, accion: str | None = None, limite: int = 100) -> dict:
    filtros, valores = [], []
    for columna, valor in (("usuario", usuario), ("accion", accion)):
        if valor:
            filtros.append(f"{columna}=%s")
            valores.append(valor)
    donde = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    with conexion() as cursor:
        cursor.execute(f"SELECT fecha, usuario, accion, detalle, ip FROM auditoria {donde} ORDER BY id DESC LIMIT %s", (*valores, limite))
        registros = cursor.fetchall()
        cursor.execute("SELECT DISTINCT usuario FROM auditoria ORDER BY usuario")
        usuarios = [f["usuario"] for f in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT accion FROM auditoria ORDER BY accion")
        acciones = [f["accion"] for f in cursor.fetchall()]
    for r in registros:
        r["detalle"] = json.loads(r["detalle"] or "{}")
    return {"registros": registros, "usuarios": usuarios, "acciones": acciones}

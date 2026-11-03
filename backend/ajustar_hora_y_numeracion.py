"""Arregla lo que ya está guardado: renumera los lotes (1, 2, 3...) y corrige la hora de lo creado en el servidor, que quedó en UTC.

Por defecto solo simula: muestra qué haría y no cambia nada. Con --aplicar guarda primero copias de las tablas lotes, auditoria y
usuarios (lotes_respaldo, auditoria_respaldo, usuarios_respaldo) y después hace los cambios. Si ya existen esas copias, se detiene.

Uso (en la carpeta backend):
    python ajustar_hora_y_numeracion.py                                                        base local (XAMPP): solo renumera
    python ajustar_hora_y_numeracion.py --base DESTINO_BD --restar-5h-desde "2026-10-27 08:00"        base en la nube, simulación
    python ajustar_hora_y_numeracion.py --base DESTINO_BD --restar-5h-desde "2026-10-27 08:00" --aplicar

--restar-5h-desde: desde cuándo los registros de la base los creó el servidor en la nube (en UTC) y no el computador. Es el momento en que
se copió la base local a la nube; lo anterior se dejó como está. La simulación muestra los registros a ambos lados de esa hora para comprobarla.
La base local no necesita corregir la hora: el computador ya estaba en hora de Colombia.
"""
import argparse
from contextlib import contextmanager
import json
import sys
from datetime import datetime, timedelta

import pymysql

from base_datos import ErrorBaseDatos, _conectar

RESPALDADAS = ("lotes", "auditoria", "usuarios")
HORAS_A_CORREGIR = (("lotes", "creado"), ("lotes", "decidido_en"), ("auditoria", "fecha"), ("usuarios", "creado"))
CINCO_HORAS = timedelta(hours=5)
CREADAS = []                                      # copias que va creando esta corrida: si algo falla se borran, para poder repetir


@contextmanager
def conexion(prefijo: str):
    """Como la de base_datos, pero dice el error real de la base (aquí lo corre quien administra el sistema)."""
    con = _conectar(prefijo)
    try:
        with con.cursor() as cursor:
            yield cursor
        con.commit()
    except pymysql.err.Error as error:
        con.rollback()
        with con.cursor() as cursor:
            for copia in CREADAS:
                cursor.execute(f"DROP TABLE IF EXISTS {copia}")
        sys.exit(f"La base respondió con un error y no se cambió nada: {error}")
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _convertir(valor, clave, numeros: dict, corregir):
    """Recorre un JSON: cambia los números de lote que aparecen como referencia y corrige las horas de las consultas combinadas."""
    if isinstance(valor, dict):
        return {k: _convertir(v, k, numeros, corregir) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_convertir(v, clave, numeros, corregir) for v in valor]
    if clave in ("lote", "unificado_en", "unifica") and isinstance(valor, int) and not isinstance(valor, bool):
        return numeros.get(valor, valor)
    if clave == "consultado" and isinstance(valor, str):
        return corregir(valor)
    return valor


def _corrector(desde: datetime | None):
    def corregir(texto: str) -> str:
        try:
            momento = datetime.fromisoformat(texto)
        except ValueError:
            return texto
        return str(momento - CINCO_HORAS) if desde and momento >= desde else texto
    return corregir


def _json_actualizado(texto: str | None, numeros: dict, corregir) -> str | None:
    if not texto:
        return texto
    nuevo = json.dumps(_convertir(json.loads(texto), None, numeros, corregir), ensure_ascii=False)
    return nuevo if json.loads(nuevo) != json.loads(texto) else texto


def ejecutar(prefijo: str, desde: datetime | None, aplicar: bool) -> None:
    with conexion(prefijo) as cursor:
        cursor.execute("SELECT id FROM lotes ORDER BY id")
        numeros = {fila["id"]: i for i, fila in enumerate(cursor.fetchall(), start=1)}
        cambian = {viejo: nuevo for viejo, nuevo in numeros.items() if viejo != nuevo}
        print(f"Lotes: {len(numeros)}. Cambian de número: {len(cambian)}")
        for viejo, nuevo in cambian.items():
            print(f"   Lote {viejo} -> Lote {nuevo}")
        if desde:
            for tabla, columna in HORAS_A_CORREGIR:
                cursor.execute(f"SELECT COUNT(*) AS n FROM {tabla} WHERE {columna} >= %s", (desde,))
                print(f"Hora: {tabla}.{columna}, se restan 5 horas a {cursor.fetchone()['n']} registros")
            print("\nComprobación de la hora de corte, registros de la auditoría a cada lado:")
            cursor.execute("SELECT fecha, usuario, accion FROM auditoria WHERE fecha < %s ORDER BY fecha DESC LIMIT 3", (desde,))
            for f in reversed(cursor.fetchall()):
                print(f"   antes   {f['fecha']}  {f['usuario']}  {f['accion']}")
            cursor.execute("SELECT fecha, usuario, accion FROM auditoria WHERE fecha >= %s ORDER BY fecha LIMIT 3", (desde,))
            for f in cursor.fetchall():
                print(f"   desde   {f['fecha']}  {f['usuario']}  {f['accion']}")
        existentes = []
        for tabla in RESPALDADAS:
            cursor.execute("SHOW TABLES LIKE %s", (f"{tabla}_respaldo",))
            if cursor.fetchone():
                existentes.append(f"{tabla}_respaldo")
        if not aplicar:
            print("\nCopias que ya existen:", ", ".join(existentes) or "ninguna")
            print("Simulación: no se cambió nada. Para aplicar, agregue --aplicar.")
            return
        if existentes:
            sys.exit(f"Ya existen copias ({', '.join(existentes)}). Revíselas y bórrelas si quiere repetir el proceso.")

        for tabla in RESPALDADAS:
            cursor.execute(f"CREATE TABLE {tabla}_respaldo LIKE {tabla}")        # TiDB no admite CREATE TABLE ... AS SELECT
            CREADAS.append(f"{tabla}_respaldo")
            cursor.execute(f"INSERT INTO {tabla}_respaldo SELECT * FROM {tabla}")
        corregir = _corrector(desde)
        for tabla, llave, columna in (("lotes", "id", "resumen"), ("auditoria", "id", "detalle")):
            cursor.execute(f"SELECT {llave}, {columna} FROM {tabla}")
            for fila in cursor.fetchall():
                nuevo = _json_actualizado(fila[columna], numeros, corregir)
                if nuevo != fila[columna]:
                    cursor.execute(f"UPDATE {tabla} SET {columna}=%s WHERE {llave}=%s", (nuevo, fila[llave]))
        if desde:
            for tabla, columna in HORAS_A_CORREGIR:
                cursor.execute(f"UPDATE {tabla} SET {columna} = {columna} - INTERVAL 5 HOUR WHERE {columna} >= %s", (desde,))
        for viejo, nuevo in sorted(cambian.items()):         # de menor a mayor: el número nuevo nunca coincide con uno que aún no se movió
            cursor.execute("UPDATE lotes SET id=%s WHERE id=%s", (nuevo, viejo))
        print("\nListo. Las copias quedaron en lotes_respaldo, auditoria_respaldo y usuarios_respaldo.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ayuda = argparse.ArgumentParser(description="Renumera los lotes y corrige la hora de lo guardado en la nube.")
    ayuda.add_argument("--base", default="SEMAFORO_BD", help="SEMAFORO_BD (local, por defecto) o DESTINO_BD (nube)")
    ayuda.add_argument("--restar-5h-desde", dest="desde", type=datetime.fromisoformat, help='AAAA-MM-DD HH:MM, por ejemplo "2026-10-27 08:00"')
    ayuda.add_argument("--aplicar", action="store_true", help="hacer los cambios (sin esto solo se simula)")
    argumentos = ayuda.parse_args()
    try:
        ejecutar(argumentos.base, argumentos.desde, argumentos.aplicar)
    except ErrorBaseDatos as error:
        sys.exit(f"No se pudo completar: {error}")

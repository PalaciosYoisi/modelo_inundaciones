"""Conexión a MySQL (XAMPP/MariaDB en el computador; TiDB, Aiven u otro en la nube) y creación de las tablas del sistema.

Guarda todo lo que debe permanecer: usuarios, sesiones, auditoría, lotes de datos nuevos y las lecturas publicadas del río.

Datos de conexión (variables de entorno o archivo backend/.env, una línea «NOMBRE=valor»):
    SEMAFORO_BD_HOST (127.0.0.1)   SEMAFORO_BD_PUERTO (3307)   SEMAFORO_BD_USUARIO (root)
    SEMAFORO_BD_CLAVE (vacía)      SEMAFORO_BD_NOMBRE (semaforo_alerta)
    SEMAFORO_BD_SSL (no)           poner «si» para bases en la nube, que exigen conexión segura

Crear la base y las tablas:   python base_datos.py
"""
import os
import ssl
from contextlib import contextmanager
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

TABLAS = [
    """CREATE TABLE IF NOT EXISTS usuarios (
        usuario VARCHAR(30) PRIMARY KEY,
        nombre VARCHAR(80) NOT NULL,
        clave_hash CHAR(64) NOT NULL,
        sal CHAR(32) NOT NULL,
        debe_cambiar_clave TINYINT(1) NOT NULL DEFAULT 1,
        intentos_fallidos INT NOT NULL DEFAULT 0,
        bloqueado_hasta DATETIME NULL,
        creado DATETIME NOT NULL
    ) DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS sesiones (
        token_hash CHAR(64) PRIMARY KEY,
        usuario VARCHAR(30) NOT NULL,
        expira DATETIME NOT NULL,
        INDEX (usuario)
    ) DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS lotes (
        id INT AUTO_INCREMENT PRIMARY KEY,
        creado DATETIME NOT NULL,
        origen VARCHAR(20) NOT NULL,
        estado VARCHAR(15) NOT NULL,
        desde DATETIME NULL,
        hasta DATETIME NULL,
        filas INT NOT NULL,
        resumen LONGTEXT NULL,
        archivo VARCHAR(80) NOT NULL DEFAULT '',
        datos LONGTEXT NULL,
        decidido_por VARCHAR(30) NULL,
        decidido_en DATETIME NULL,
        INDEX (estado)
    ) DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS lecturas (
        estacion VARCHAR(10) NOT NULL,
        fecha_hora DATETIME NOT NULL,
        nivel_m DOUBLE NULL,
        lluvia_mm DOUBLE NULL,
        actualizado DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (estacion, fecha_hora)
    ) DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS auditoria (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        fecha DATETIME NOT NULL,
        usuario VARCHAR(30) NOT NULL,
        accion VARCHAR(40) NOT NULL,
        detalle TEXT NULL,
        ip VARCHAR(45) NULL,
        INDEX (fecha), INDEX (usuario), INDEX (accion)
    ) DEFAULT CHARSET=utf8mb4""",
]
COLUMNA_QUE_FALTABA_EN_LOTES = "ALTER TABLE lotes ADD COLUMN datos LONGTEXT NULL"      # bases creadas antes de guardar los datos del lote aquí
ERROR_COLUMNA_REPETIDA = 1060


class ErrorBaseDatos(Exception):
    """La base no responde. El mensaje es apto para mostrarlo a una persona."""


def configuracion(prefijo: str = "SEMAFORO_BD") -> dict:
    archivo = Path(__file__).with_name(".env")
    guardados = {}
    if archivo.exists():
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                nombre, valor = linea.split("=", 1)
                guardados[nombre.strip()] = valor.strip()
    dato = lambda nombre, defecto: os.environ.get(f"{prefijo}_{nombre}", guardados.get(f"{prefijo}_{nombre}", defecto))
    datos = {"host": dato("HOST", "127.0.0.1"), "port": int(dato("PUERTO", "3307")), "user": dato("USUARIO", "root"),
             "password": dato("CLAVE", ""), "database": dato("NOMBRE", "semaforo_alerta")}
    if dato("SSL", "no").lower() in ("si", "sí", "true", "1", "yes"):
        datos["ssl"] = ssl.create_default_context()
    return datos


def _conectar(prefijo: str, con_base: bool = True):
    datos = configuracion(prefijo)
    if not con_base:
        datos.pop("database")
    try:
        return pymysql.connect(**datos, charset="utf8mb4", cursorclass=DictCursor, connect_timeout=15)
    except pymysql.err.Error:
        raise ErrorBaseDatos("No se pudo conectar con la base de datos. Avise a quien administra el sistema.") from None


@contextmanager
def conexion(prefijo: str = "SEMAFORO_BD"):
    """Abre una conexión; confirma los cambios al salir y los deshace si hubo un error."""
    con = _conectar(prefijo)
    try:
        with con.cursor() as cursor:
            yield cursor
        con.commit()
    except pymysql.err.Error:
        con.rollback()
        raise ErrorBaseDatos("La base de datos no pudo completar la operación. Intente de nuevo.") from None
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def crear_tablas(prefijo: str = "SEMAFORO_BD") -> str:
    nombre = configuracion(prefijo)["database"]
    try:                                              # en algunos servicios la base ya viene creada y no se permite crear otra
        con = _conectar(prefijo, con_base=False)
        try:
            with con.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{nombre}` CHARACTER SET utf8mb4")
            con.commit()
        finally:
            con.close()
    except (pymysql.err.Error, ErrorBaseDatos):
        pass
    with conexion(prefijo) as cursor:
        for sentencia in TABLAS:
            cursor.execute(sentencia)
        try:
            cursor.execute(COLUMNA_QUE_FALTABA_EN_LOTES)
        except pymysql.err.OperationalError as error:
            if error.args[0] != ERROR_COLUMNA_REPETIDA:
                raise
    return nombre


if __name__ == "__main__":
    print(f"Base «{crear_tablas()}» lista: tablas usuarios, sesiones, lotes, lecturas y auditoria.")

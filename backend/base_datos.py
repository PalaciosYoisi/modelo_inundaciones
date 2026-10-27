"""Conexión a MySQL (XAMPP/MariaDB sirve) y creación de las tablas del sistema.

Guarda solo lo necesario: usuarios, sesiones y auditoría. Las lecturas del río siguen en archivos CSV.
Si la base no está disponible, solo fallan el ingreso y la auditoría; lo que ve el público no depende de ella.

Datos de conexión (variables de entorno o archivo backend/.env, una línea «NOMBRE=valor»):
    SEMAFORO_BD_HOST (127.0.0.1)   SEMAFORO_BD_PUERTO (3307)   SEMAFORO_BD_USUARIO (root)
    SEMAFORO_BD_CLAVE (vacía)      SEMAFORO_BD_NOMBRE (semaforo_alerta)

Crear la base y las tablas:   python base_datos.py
"""
import os
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
        FOREIGN KEY (usuario) REFERENCES usuarios(usuario) ON DELETE CASCADE
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


class ErrorBaseDatos(Exception):
    """La base no responde. El mensaje es apto para mostrarlo a una persona."""


def _configuracion() -> dict:
    archivo = Path(__file__).with_name(".env")
    guardados = {}
    if archivo.exists():
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                nombre, valor = linea.split("=", 1)
                guardados[nombre.strip()] = valor.strip()
    dato = lambda nombre, defecto: os.environ.get(nombre, guardados.get(nombre, defecto))
    return {"host": dato("SEMAFORO_BD_HOST", "127.0.0.1"), "port": int(dato("SEMAFORO_BD_PUERTO", "3307")),
            "user": dato("SEMAFORO_BD_USUARIO", "root"), "password": dato("SEMAFORO_BD_CLAVE", ""),
            "database": dato("SEMAFORO_BD_NOMBRE", "semaforo_alerta")}


def _conectar(con_base: bool = True):
    datos = _configuracion()
    if not con_base:
        datos.pop("database")
    try:
        return pymysql.connect(**datos, charset="utf8mb4", cursorclass=DictCursor, connect_timeout=5)
    except pymysql.err.Error:
        raise ErrorBaseDatos("No se pudo conectar con la base de datos. Avise a quien administra el sistema.") from None


@contextmanager
def conexion():
    """Abre una conexión; confirma los cambios al salir y los deshace si hubo un error."""
    con = _conectar()
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


def crear_tablas() -> str:
    nombre = _configuracion()["database"]
    con = _conectar(con_base=False)
    try:
        with con.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{nombre}` CHARACTER SET utf8mb4")
        con.commit()
    finally:
        con.close()
    with conexion() as cursor:
        for sentencia in TABLAS:
            cursor.execute(sentencia)
    return nombre


if __name__ == "__main__":
    print(f"Base «{crear_tablas()}» lista: tablas usuarios, sesiones y auditoria.")

"""Copia la base local (XAMPP) a la base en la nube: usuarios, auditoría, lotes y lecturas publicadas.

La de origen es la que usa el proyecto normalmente (variables SEMAFORO_BD_*). La de destino se indica con las mismas
variables pero con el prefijo DESTINO_BD_ (HOST, PUERTO, USUARIO, CLAVE, NOMBRE y SSL=si). Se puede repetir sin problema:
lo que ya estaba en el destino se reemplaza. Las sesiones abiertas no se copian: cada persona vuelve a ingresar con su clave.

Uso:  python migrar_base.py
"""
import os
import sys

import pandas as pd

import lotes
from base_datos import ErrorBaseDatos, conexion, crear_tablas
from ingesta import CARPETA_MODELOS, DATOS_INICIALES, PUBLICADO_ANTERIOR
from pipeline import cargar_metadatos, version_activa
from publicacion import Publicacion

ORIGEN, DESTINO = "SEMAFORO_BD", "DESTINO_BD"
TABLAS = ["usuarios", "auditoria", "lotes", "lecturas"]
POR_ENVIO = 500


def _copiar(tabla: str) -> int:
    with conexion(ORIGEN) as cursor:
        cursor.execute(f"SELECT * FROM {tabla}")
        filas = cursor.fetchall()
    if tabla == "lotes":                              # un lote guardado como archivo en una versión anterior pasa a la base
        for fila in filas:
            if not fila.get("datos"):
                try:
                    fila["datos"] = lotes.datos(fila["id"]).to_csv(index=False)
                except FileNotFoundError:
                    pass
    if not filas:
        return 0
    columnas = list(filas[0])
    sentencia = f"REPLACE INTO {tabla} ({', '.join(columnas)}) VALUES ({', '.join(['%s'] * len(columnas))})"
    with conexion(DESTINO) as cursor:
        for i in range(0, len(filas), POR_ENVIO):
            cursor.executemany(sentencia, [tuple(f[c] for c in columnas) for f in filas[i:i + POR_ENVIO]])
    return len(filas)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not os.environ.get(f"{DESTINO}_HOST"):
        sys.exit("Falta indicar la base de destino: defina DESTINO_BD_HOST, DESTINO_BD_PUERTO, DESTINO_BD_USUARIO, DESTINO_BD_CLAVE, DESTINO_BD_NOMBRE y DESTINO_BD_SSL.")
    try:
        crear_tablas(ORIGEN)                          # por si la local es de una versión anterior
        codigos = list(cargar_metadatos(CARPETA_MODELOS / version_activa(CARPETA_MODELOS))["estaciones"])
        Publicacion(DATOS_INICIALES, PUBLICADO_ANTERIOR).tabla(codigos)         # llena las lecturas locales con lo que ya estaba publicado
        print(f"Base de destino «{crear_tablas(DESTINO)}» lista.")
        for tabla in TABLAS:
            print(f"  {tabla}: {_copiar(tabla)} filas copiadas")
        print("Listo.")
    except ErrorBaseDatos as error:
        sys.exit(f"No se pudo completar: {error}")

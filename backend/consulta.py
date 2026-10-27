"""Consulta a datos.gov.co (datos abiertos de Colombia): nivel instantáneo del río y lluvia del IDEAM.

Solo pide lo que hace falta (unas estaciones y desde una fecha). No limpia ni guarda nada: eso lo hacen
preparacion.py y lotes.py. Si el servicio no responde, lanza ErrorDeConsulta con un mensaje claro.
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime

import pandas as pd

DIRECCION = "https://www.datos.gov.co/resource"
CONJUNTOS = {"nivel": "bdmn-sqnh", "lluvia": "s54a-sgyg"}      # nivel instantáneo y precipitación (cada 10 minutos)
COLUMNAS = ["codigoestacion", "codigosensor", "fechaobservacion", "valorobservado"]
POR_PAGINA = 50_000
INTENTOS = 3


class ErrorDeConsulta(Exception):
    """datos.gov.co no respondió o respondió algo inesperado. El mensaje es apto para mostrarlo."""


def _pedir(direccion: str) -> list:
    for intento in range(1, INTENTOS + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(direccion, headers={"Accept": "application/json"}), timeout=60) as respuesta:
                return json.load(respuesta)
        except Exception:
            if intento == INTENTOS:
                raise ErrorDeConsulta("No se pudo consultar datos.gov.co. Puede estar fuera de servicio; se volverá a intentar en la próxima consulta.") from None
            time.sleep(2 * intento)


def consultar(conjunto: str, estaciones: list[str], desde: datetime) -> pd.DataFrame:
    """Lecturas de las estaciones dadas desde la fecha indicada. conjunto: «nivel» o «lluvia»."""
    if not all(str(e).isdigit() for e in estaciones):
        raise ErrorDeConsulta("Un código de estación no es válido.")
    if not estaciones:
        return pd.DataFrame(columns=COLUMNAS)
    codigos = ",".join(f"'{e}'" for e in estaciones)
    filtro = f"codigoestacion in ({codigos}) AND fechaobservacion >= '{desde:%Y-%m-%dT%H:%M:%S}'"
    filas, inicio = [], 0
    while True:
        parametros = urllib.parse.urlencode({"$select": ",".join(COLUMNAS), "$where": filtro, "$order": "fechaobservacion,codigoestacion",
                                             "$limit": POR_PAGINA, "$offset": inicio})
        pagina = _pedir(f"{DIRECCION}/{CONJUNTOS[conjunto]}.json?{parametros}")
        if not isinstance(pagina, list):
            raise ErrorDeConsulta("datos.gov.co respondió algo que no se esperaba.")
        filas += pagina
        if len(pagina) < POR_PAGINA:
            break
        inicio += POR_PAGINA
    return pd.DataFrame(filas, columns=COLUMNAS)

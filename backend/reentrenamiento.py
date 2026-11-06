"""Reentrenamiento: entrena una versión nueva del modelo con los datos acumulados, la valida por bloques de tiempo y la compara con la activa.

No toca la versión activa ni publica nada: el resultado es una versión candidata que alguien decide si reemplaza a la actual.
Calcula las variables con el mismo código que usa el sitio (pipeline.py), así la versión nueva ve los datos igual que la que está en uso.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

from pipeline import GRILLA, ParametrosModelo, columna_estacion, variables_estacion

COLUMNAS = ["estacion", "fecha_hora", "nivel_m", "lluvia_mm"]
LLAVES = ["estacion", "fecha_hora"]
MINIMO_NIVELES = 100              # con menos lecturas de nivel no se puede fijar la regla del percentil de una estación


class ErrorDeReentrenamiento(Exception):
    """Falta algo para poder entrenar. El mensaje es apto para mostrarlo a una persona."""


# ------------------------------------------------------------------ datos
def leer_datos(ruta: Path, codigos: list[str]) -> pd.DataFrame:
    """Tabla horaria (estación, hora, nivel y lluvia) de las estaciones del modelo, leída de un CSV."""
    tabla = pd.read_csv(ruta, usecols=COLUMNAS, dtype={"estacion": str}, parse_dates=["fecha_hora"])
    return tabla[tabla["estacion"].isin(codigos)].sort_values(LLAVES).reset_index(drop=True)


def combinar(*partes: pd.DataFrame) -> pd.DataFrame:
    """Une tablas horarias. Si una hora está en varias, vale el valor de la primera que lo traiga; uno vacío nunca borra uno existente."""
    combinada = partes[0].set_index(LLAVES)[["nivel_m", "lluvia_mm"]]
    for parte in partes[1:]:
        combinada = combinada.combine_first(parte.set_index(LLAVES)[["nivel_m", "lluvia_mm"]])
    return combinada.reset_index().sort_values(LLAVES).reset_index(drop=True)


# ------------------------------------------------------------------ variables y modelo
def referencia_desde(niveles) -> np.ndarray:
    """Regla para pasar un nivel (m) a percentil: 1001 niveles equivalentes a los percentiles 0, 0.1, ..., 100."""
    return np.quantile(np.asarray(niveles, dtype=float), GRILLA / 100)


def escala_de(niveles) -> float:
    """Rango intercuartílico de la estación: sirve para comparar cambios de nivel entre ríos."""
    return max(float(np.quantile(niveles, 0.75) - np.quantile(niveles, 0.25)), 1e-6)


def referencias_y_escalas(tabla: pd.DataFrame) -> tuple[dict, dict]:
    """Regla del percentil y escala de cada estación con suficientes lecturas de nivel en la tabla."""
    referencias, escalas = {}, {}
    for estacion, grupo in tabla.groupby("estacion"):
        niveles = grupo["nivel_m"].dropna().values
        if len(niveles) >= MINIMO_NIVELES:
            referencias[estacion], escalas[estacion] = referencia_desde(niveles), escala_de(niveles)
    return referencias, escalas


def color_de_percentil(percentil, parametros: ParametrosModelo) -> np.ndarray:
    """0 verde, 1 amarillo, 2 rojo."""
    return np.where(percentil >= parametros.umbral_rojo, 2, np.where(percentil >= parametros.umbral_amarillo, 1, 0))


def construir(tabla: pd.DataFrame, referencias: dict, escalas: dict, codigos: list[str], parametros: ParametrosModelo) -> pd.DataFrame:
    """Variables de cada hora de cada estación y el color que de verdad tuvo el nivel en las 24 horas siguientes."""
    partes = []
    for estacion, grupo in tabla.groupby("estacion"):
        if estacion in referencias:
            f = variables_estacion(grupo, referencias[estacion], escalas[estacion], parametros)
            for codigo in codigos:
                f[columna_estacion(codigo)] = int(codigo == estacion)
            partes.append(f)
    if not partes:
        raise ErrorDeReentrenamiento("Ninguna estación del modelo tiene suficientes lecturas de nivel.")
    t = pd.concat(partes)
    t["color_objetivo"] = color_de_percentil(t["objetivo_percentil"], parametros)
    return t


def crear_modelo(configuracion: dict) -> RandomForestClassifier:
    """El bosque, con la misma configuración que tiene la versión activa."""
    return RandomForestClassifier(n_estimators=configuracion["n_estimators"], max_depth=configuracion["max_depth"],
                                  min_samples_leaf=configuracion["min_samples_leaf"], class_weight=configuracion["class_weight"],
                                  random_state=configuracion["random_state"], n_jobs=-1)


def medir(real, predicho) -> dict:
    """Métricas del semáforo, con el rojo como lo que más importa."""
    real, predicho = np.asarray(real), np.asarray(predicho)
    r, p = real == 2, predicho == 2
    return {
        "Color acertado": float(accuracy_score(real, predicho)),
        "Rojos detectados": float(recall_score(r, p, zero_division=0)),
        "Alertas rojas correctas": float(precision_score(r, p, zero_division=0)),
        "F1 del rojo": float(f1_score(r, p, zero_division=0)),
        "Rojos no detectados": int((r & ~p).sum()),
        "Falsas alarmas": int((~r & p).sum()),
    }

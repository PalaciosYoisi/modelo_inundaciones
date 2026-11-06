"""Reentrenamiento: entrena una versión nueva del modelo con los datos acumulados, la valida por bloques de tiempo y la compara con la activa.

No toca la versión activa ni publica nada: el resultado es una versión candidata que alguien decide si reemplaza a la actual.
Calcula las variables con el mismo código que usa el sitio (pipeline.py), así la versión nueva ve los datos igual que la que está en uso.
Uso:  python reentrenamiento.py     entrena con datos/datos_horarios.csv y guarda la versión candidata en modelos/
"""

import json
import sys
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

from hora import hora_actual
from pipeline import (GRILLA, NOMBRE_METADATOS, ParametrosModelo, cargar_metadatos, cargar_modelo, columna_estacion, probabilidades,
                      semaforo_desde, variables_estacion, version_activa)

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_MODELOS = RAIZ / "modelos"
DATOS_INICIALES = RAIZ / "datos" / "datos_horarios.csv"
COLUMNAS = ["estacion", "fecha_hora", "nivel_m", "lluvia_mm"]
LLAVES = ["estacion", "fecha_hora"]
MINIMO_NIVELES = 100              # con menos lecturas de nivel no se puede fijar la regla del percentil de una estación
MINIMO_MOMENTOS = 24              # con menos horas nuevas no tiene sentido medir al modelo en uso
REGLAS = ["Regla: nivel de hoy", "Regla: máximo de 24 h"]
BOSQUE = "Bosque aleatorio"
METRICAS = ["Color acertado", "Rojos detectados", "Alertas rojas correctas", "F1 del rojo", "Rojos no detectados", "Falsas alarmas"]
Avance = Callable[[str, float], None]         # recibe la etapa en curso y qué parte va hecha (de 0 a 1)


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


# ------------------------------------------------------------------ validación
def _avisar(avance: Avance | None, etapa: str, fraccion: float) -> None:
    if avance:
        avance(etapa, fraccion)


def validar_por_bloques(datos: pd.DataFrame, metadatos: dict, avance: Avance | None = None) -> dict:
    """Divide el tiempo en 6 bloques y, para cada uno de los últimos cuatro, entrena con todo lo anterior y prueba con ese bloque.

    La regla del percentil y las escalas se calculan solo con el pasado y se deja un margen de 24 horas entre el entrenamiento y la prueba,
    para que el modelo no vea horas casi idénticas a la que debe adivinar. Compara el bosque con dos reglas simples que no aprenden nada.
    """
    parametros = ParametrosModelo.desde_metadatos(metadatos)
    codigos, columnas = list(metadatos["estaciones"]), metadatos["variables_modelo"]
    horas = np.sort(datos.loc[datos["nivel_m"].notna(), "fecha_hora"].unique())
    cortes = [pd.Timestamp(horas[int(len(horas) * q)]) for q in (2 / 6, 3 / 6, 4 / 6, 5 / 6)]
    cortes.append(datos["fecha_hora"].max() + pd.Timedelta(hours=1))
    evaluaciones, bloques = [], []
    for k in range(4):
        _avisar(avance, "validación por bloques de tiempo", k / 4)
        inicio, fin = cortes[k], cortes[k + 1]
        referencias, escalas = referencias_y_escalas(datos[datos["fecha_hora"] < inicio])
        if not referencias:
            continue
        t = construir(datos, referencias, escalas, codigos, parametros).dropna(subset=["nivel_percentil", "objetivo_percentil"])
        t = t[t.index.hour % parametros.paso_horas == 0]
        entrenamiento = t[t.index < inicio - pd.Timedelta(hours=parametros.horizonte_h)]
        prueba = t[(t.index >= inicio) & (t.index < fin)]
        if len(prueba) == 0 or len(entrenamiento) < 200:
            continue
        modelo = crear_modelo(metadatos["configuracion"]).fit(entrenamiento[columnas], entrenamiento["color_objetivo"])
        proba = probabilidades(modelo, prueba[columnas])
        evaluaciones.append(pd.DataFrame({
            "real": prueba["color_objetivo"].values,
            REGLAS[0]: color_de_percentil(prueba["nivel_percentil"].values, parametros),
            REGLAS[1]: color_de_percentil(prueba["max_24h"].values, parametros),
            BOSQUE: semaforo_desde(proba, parametros.umbral_prob_rojo)}))
        bloques.append({"bloque": k + 1, "desde": str(inicio), "hasta": str(fin), "momentos": len(prueba)})
    if not evaluaciones:
        raise ErrorDeReentrenamiento("Todavía no hay datos suficientes para validar el modelo por bloques de tiempo.")
    todas = pd.concat(evaluaciones, ignore_index=True)
    metricas = pd.DataFrame({nombre: medir(todas["real"], todas[nombre]) for nombre in REGLAS + [BOSQUE]}).T
    _avisar(avance, "validación por bloques de tiempo", 1.0)
    return {"momentos": len(todas), "con_rojo": int((todas["real"] == 2).sum()), "bloques": bloques,
            "metricas": metricas.round(3).to_dict(orient="index")}


def evaluar_modelo_actual(datos: pd.DataFrame, metadatos: dict, modelo, desde: pd.Timestamp) -> dict | None:
    """Cómo le va al modelo en uso con las horas posteriores a `desde`, que nunca vio al entrenarse. None si son muy pocas."""
    parametros = ParametrosModelo.desde_metadatos(metadatos)
    referencias = {e: np.array(v) for e, v in metadatos["referencia_percentil"]["niveles_m"].items()}
    escalas = {e: v["escala_nivel_m"] for e, v in metadatos["estaciones"].items()}
    t = construir(datos, referencias, escalas, list(metadatos["estaciones"]), parametros).dropna(subset=["nivel_percentil", "objetivo_percentil"])
    t = t[t.index > desde]
    if len(t) < MINIMO_MOMENTOS:
        return None
    predicho = semaforo_desde(probabilidades(modelo, t[metadatos["variables_modelo"]]), parametros.umbral_prob_rojo)
    return {"momentos": len(t), "con_rojo": int((t["color_objetivo"] == 2).sum()), **medir(t["color_objetivo"], predicho)}


# ------------------------------------------------------------------ versión nueva
def siguiente_version(carpeta_modelos: Path) -> str:
    """El nombre de la próxima versión: v4 si la mayor que existe es v3."""
    numeros = [int(c.name[1:]) for c in Path(carpeta_modelos).glob("v*") if c.name[1:].isdigit()]
    return f"v{max(numeros, default=0) + 1}"


def entrenar_version(datos: pd.DataFrame, metadatos_anterior: dict, modelo_anterior, version: str, avance: Avance | None = None) -> dict:
    """Entrena la versión `version` con todos los datos, la valida y la compara con la anterior. No guarda nada en disco.

    datos: tabla horaria con estación, hora, nivel y lluvia. Todo lo que no cambia entre versiones (estaciones, variables, parámetros
    del bosque, umbral) se toma de los metadatos de la versión anterior. Devuelve {version, modelo, metadatos, validacion, comparacion,
    modelo_actual_en_datos_nuevos}; la versión queda como candidata.
    """
    parametros = ParametrosModelo.desde_metadatos(metadatos_anterior)
    codigos, columnas = list(metadatos_anterior["estaciones"]), metadatos_anterior["variables_modelo"]
    fin_anterior = pd.Timestamp(metadatos_anterior["periodo_datos"][1])
    horas_nuevas = int(((datos["fecha_hora"] > fin_anterior) & datos["nivel_m"].notna()).sum())

    _avisar(avance, "revisión del modelo actual con los datos nuevos", 0.0)
    actual = evaluar_modelo_actual(datos, metadatos_anterior, modelo_anterior, fin_anterior) if modelo_anterior is not None else None
    validacion = validar_por_bloques(datos, metadatos_anterior, avance)

    _avisar(avance, "entrenamiento con todos los datos", 0.0)
    referencias, escalas = referencias_y_escalas(datos)
    sin_lecturas = [e for e in codigos if e not in referencias]
    if sin_lecturas:
        raise ErrorDeReentrenamiento(f"Estas estaciones tienen muy pocas lecturas de nivel para entrenar: {', '.join(sin_lecturas)}.")
    tabla = construir(datos, referencias, escalas, codigos, parametros).dropna(subset=["nivel_percentil", "objetivo_percentil"])
    entrenamiento = tabla[tabla.index.hour % parametros.paso_horas == 0]
    modelo = crear_modelo(metadatos_anterior["configuracion"]).fit(entrenamiento[columnas], entrenamiento["color_objetivo"])

    metricas = validacion["metricas"]
    metadatos = json.loads(json.dumps(metadatos_anterior))                 # copia: lo que no cambia se hereda
    metadatos.update({
        "version": version,
        "fecha_entrenamiento": hora_actual().isoformat(timespec="seconds"),
        "estado": "candidato",
        "registros_entrenamiento": int(len(entrenamiento)),
        "periodo_datos": [str(datos["fecha_hora"].min()), str(datos["fecha_hora"].max())],
        "metrica_principal": {"nombre": metadatos_anterior["metrica_principal"]["nombre"], "valor_modelo": metricas[BOSQUE]["F1 del rojo"],
                              "valor_reglas_simples": {nombre: metricas[nombre]["F1 del rojo"] for nombre in REGLAS}},
        "metricas_validacion": metricas,
        "archivo_modelo": f"modelo_semaforo_{version}.joblib",
        "reentrenamiento": {"version_anterior": metadatos_anterior["version"], "datos_nuevos_desde": str(fin_anterior), "horas_nuevas_con_nivel": horas_nuevas},
    })
    for estacion in codigos:
        metadatos["estaciones"][estacion]["escala_nivel_m"] = round(escalas[estacion], 4)
    metadatos["referencia_percentil"] = {"percentiles": GRILLA.tolist(), "niveles_m": {e: np.round(v, 4).tolist() for e, v in referencias.items()}}

    anterior = metadatos_anterior.get("metricas_validacion", {}).get(BOSQUE) or {}
    comparacion = {"version_anterior": metadatos_anterior["version"], "version_nueva": version,
                   "metricas": {m: {"anterior": anterior.get(m), "nueva": metricas[BOSQUE][m]} for m in METRICAS}}
    valor_anterior = metadatos_anterior["metrica_principal"].get("valor_modelo")
    comparacion["diferencia_f1"] = None if valor_anterior is None else round(metricas[BOSQUE]["F1 del rojo"] - valor_anterior, 3)
    _avisar(avance, "entrenamiento con todos los datos", 1.0)
    return {"version": version, "modelo": modelo, "metadatos": metadatos, "validacion": validacion, "comparacion": comparacion,
            "modelo_actual_en_datos_nuevos": actual}


def guardar_version(carpeta_modelos: Path, resultado: dict) -> Path:
    """Guarda la versión en modelos/<versión>/ (el bosque y sus metadatos). No cambia cuál es la versión activa ni sobrescribe una existente."""
    carpeta = Path(carpeta_modelos) / resultado["version"]
    if carpeta.exists():
        raise ErrorDeReentrenamiento(f"Ya existe la versión {resultado['version']}.")
    carpeta.mkdir(parents=True)
    joblib.dump(resultado["modelo"], carpeta / resultado["metadatos"]["archivo_modelo"])
    (carpeta / NOMBRE_METADATOS).write_text(json.dumps(resultado["metadatos"], ensure_ascii=False, indent=1), encoding="utf-8")
    return carpeta


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        anterior = version_activa(CARPETA_MODELOS)
        metadatos = cargar_metadatos(CARPETA_MODELOS / anterior)
        resultado = entrenar_version(leer_datos(DATOS_INICIALES, list(metadatos["estaciones"])), metadatos,
                                     cargar_modelo(CARPETA_MODELOS / anterior, metadatos), siguiente_version(CARPETA_MODELOS),
                                     avance=lambda etapa, parte: print(f"  {etapa}: {parte:.0%}"))
        carpeta = guardar_version(CARPETA_MODELOS, resultado)
        comparacion = resultado["comparacion"]
        print(f"Versión {resultado['version']} guardada en {carpeta} como candidata (sigue en uso {anterior}).")
        print(f"F1 del rojo: {comparacion['metricas']['F1 del rojo']['anterior']} -> {comparacion['metricas']['F1 del rojo']['nueva']}")
    except ErrorDeReentrenamiento as error:
        sys.exit(f"No se pudo completar: {error}")

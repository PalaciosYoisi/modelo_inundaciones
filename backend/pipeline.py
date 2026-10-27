"""Pipeline predictivo: el mismo preprocesamiento del entrenamiento aplicado a datos nuevos.

Una versión del modelo son dos archivos (bosque + metadatos). El pipeline los une: toma la regla del percentil y las
escalas guardadas en los metadatos (nunca las recalcula con los datos que se predicen), construye las variables y usa
el bosque cargado. No entrena nada.
"""
import joblib
import json
import numpy as np
import pandas as pd
from dataclasses import dataclass
from pathlib import Path


# ==============================================================================
# Parámetros del modelo
# ==============================================================================

@dataclass(frozen=True)
class ParametrosModelo:
    horizonte_h: int = 24            # se predice el nivel máximo de las próximas 24 horas
    umbral_amarillo: float = 75.0    # percentil desde el que el semáforo es amarillo
    umbral_rojo: float = 95.0        # percentil desde el que el semáforo es rojo
    umbral_prob_rojo: float = 0.40   # probabilidad de rojo desde la cual se avisa rojo
    paso_horas: int = 3              # una hora de cada 3 como momento de entrenamiento
    historia_minima_h: int = 168     # horas de historia necesarias para poder predecir
    n_arboles: int = 300
    semilla: int = 42

    @classmethod
    def desde_metadatos(cls, metadatos: dict) -> "ParametrosModelo":
        config = metadatos.get("configuracion", {})
        umbrales = metadatos.get("variable_objetivo", {}).get("umbrales_percentil", {})
        por_defecto = cls()
        return cls(
            horizonte_h=int(config.get("horizonte_horas", por_defecto.horizonte_h)),
            umbral_amarillo=float(umbrales.get("amarillo", por_defecto.umbral_amarillo)),
            umbral_rojo=float(umbrales.get("rojo", por_defecto.umbral_rojo)),
            umbral_prob_rojo=float(config.get("umbral_probabilidad_rojo", por_defecto.umbral_prob_rojo)),
            paso_horas=int(config.get("paso_horas_entrenamiento", por_defecto.paso_horas)),
            historia_minima_h=int(metadatos.get("entrada", {}).get("historia_minima_horas", por_defecto.historia_minima_h)),
            n_arboles=int(config.get("n_estimators", por_defecto.n_arboles)),
            semilla=int(config.get("random_state", por_defecto.semilla)),
        )


# ==============================================================================
# Clasificador
# ==============================================================================

ETIQUETAS = ["Verde", "Amarillo", "Rojo"]


def probabilidades(modelo, X: pd.DataFrame) -> pd.DataFrame:
    """Probabilidad de verde, amarillo y rojo (columnas 0, 1 y 2)."""
    return (pd.DataFrame(modelo.predict_proba(X), columns=modelo.classes_, index=X.index)
            .reindex(columns=[0, 1, 2], fill_value=0.0))


def semaforo_desde(proba: pd.DataFrame, umbral_rojo: float) -> np.ndarray:
    """0 verde, 1 amarillo, 2 rojo: rojo si la probabilidad de rojo llega al umbral; si no, el más probable de los otros."""
    return np.where(proba[2] >= umbral_rojo, 2, np.where(proba[1] >= proba[0], 1, 0))


# ==============================================================================
# Variables del modelo
# ==============================================================================

GRILLA = np.linspace(0, 100, 1001)    # la regla del percentil se guarda como 1001 puntos por estación


def columna_estacion(codigo: str) -> str:
    return "est_" + codigo


def a_percentil(nivel, referencia) -> np.ndarray:
    return np.interp(np.asarray(nivel, dtype=float), referencia, GRILLA)


def normalizar_tabla(tabla: pd.DataFrame, deduplicar: bool = True) -> pd.DataFrame:
    """Deja la tabla con los tipos que espera el modelo (sin corregir valores) y ordenada por estación y hora."""
    t = tabla[["estacion", "fecha_hora", "nivel_m", "lluvia_mm"]].copy()
    t["estacion"] = t["estacion"].astype(str).str.strip()
    t["fecha_hora"] = pd.to_datetime(t["fecha_hora"], errors="coerce")
    t["nivel_m"] = pd.to_numeric(t["nivel_m"], errors="coerce")
    t["lluvia_mm"] = pd.to_numeric(t["lluvia_mm"], errors="coerce")
    t = t.dropna(subset=["fecha_hora"])
    if deduplicar:
        t = t.drop_duplicates(["estacion", "fecha_hora"], keep="last")
    return t.sort_values(["estacion", "fecha_hora"]).reset_index(drop=True) if deduplicar else t.sort_values(["estacion", "fecha_hora"])


def variables_estacion(tabla: pd.DataFrame, referencia, escala: float, parametros: ParametrosModelo,
                       con_objetivo: bool = True) -> pd.DataFrame:
    """Variables de una estación para cada hora (una fila por hora, sin saltos)."""
    t = tabla.set_index("fecha_hora").sort_index()
    t = t[~t.index.duplicated(keep="last")]
    t = t.reindex(pd.date_range(t.index.min(), t.index.max(), freq="h"))
    nivel, lluvia = t["nivel_m"], t["lluvia_mm"]
    p = pd.Series(a_percentil(nivel, referencia), index=t.index)
    f = pd.DataFrame(index=t.index)
    f["nivel_percentil"] = p
    for h in (1, 3, 6, 12, 24, 48):
        f[f"cambio_{h}h"] = p - p.shift(h)
    f["max_24h"] = p.rolling(24, min_periods=12).max()
    f["max_72h"] = p.rolling(72, min_periods=36).max()
    f["max_7d"] = p.rolling(168, min_periods=72).max()
    f["media_7d"] = p.rolling(168, min_periods=72).mean()
    f["min_24h"] = p.rolling(24, min_periods=12).min()
    for h in (1, 3, 6, 12, 24):
        f[f"subida_{h}h"] = (nivel - nivel.shift(h)) / escala
    f["anomalia_30d"] = (nivel - nivel.rolling(720, min_periods=240).median()) / escala
    f["anomalia_90d"] = (nivel - nivel.rolling(2160, min_periods=720).median()) / escala
    f["rango_24h"] = (nivel.rolling(24, min_periods=12).max() - nivel.rolling(24, min_periods=12).min()) / escala
    for h in (1, 3, 6, 12, 24, 48, 72, 168, 336, 720):
        f[f"lluvia_{h}h"] = lluvia.rolling(h, min_periods=max(1, h // 2)).sum()
    f["lluvia_max_1h_24h"] = lluvia.rolling(24, min_periods=12).max()
    f["hora_sin"], f["hora_cos"] = np.sin(2 * np.pi * t.index.hour / 24), np.cos(2 * np.pi * t.index.hour / 24)
    f["mes_sin"], f["mes_cos"] = np.sin(2 * np.pi * t.index.month / 12), np.cos(2 * np.pi * t.index.month / 12)
    if con_objetivo:
        # Lo que se quiere adivinar: el nivel máximo (percentil) de las horas siguientes
        f["objetivo_percentil"] = (p[::-1].rolling(parametros.horizonte_h, min_periods=int(parametros.horizonte_h * 0.75))
                                   .max()[::-1].shift(-1))
    f["estacion"] = tabla["estacion"].iloc[0]
    return f


# ==============================================================================
# Carga de una versión
# ==============================================================================

NOMBRE_METADATOS = "metadatos.json"


def cargar_metadatos(carpeta) -> dict:
    ruta = Path(carpeta) / NOMBRE_METADATOS
    if not ruta.exists():
        raise FileNotFoundError(f"No hay un modelo en la carpeta {carpeta}.")
    return json.loads(ruta.read_text(encoding="utf-8"))


def cargar_modelo(carpeta, metadatos: dict):
    ruta = Path(carpeta) / metadatos["archivo_modelo"]
    if not ruta.exists():
        raise FileNotFoundError(f"Falta el archivo del modelo ({ruta.name}).")
    return joblib.load(ruta)


# ==============================================================================
# Pipeline
# ==============================================================================

ESTADO_PREDICHO = "predicho"
ESTADO_SIN_HISTORIA = "sin_historia_suficiente"
ESTADO_SIN_NIVEL = "sin_nivel"
ESTADO_ESTACION_DESCONOCIDA = "estacion_desconocida"


class PipelineSemaforo:
    def __init__(self, modelo, metadatos: dict):
        self.modelo = modelo
        self.metadatos = metadatos
        self.parametros = ParametrosModelo.desde_metadatos(metadatos)
        self.version = metadatos["version"]
        self.codigos = list(metadatos["estaciones"])
        self.columnas_x = list(metadatos["variables_modelo"])
        self.escalas = {e: float(v["escala_nivel_m"]) for e, v in metadatos["estaciones"].items()}
        self.referencias = {e: np.array(v, dtype=float) for e, v in metadatos["referencia_percentil"]["niveles_m"].items()}

    @classmethod
    def cargar(cls, carpeta: Path) -> "PipelineSemaforo":
        metadatos = cargar_metadatos(carpeta)
        return cls(cargar_modelo(carpeta, metadatos), metadatos)

    # ------------------------------------------------------------------ predicción
    def _variables(self, grupo: pd.DataFrame, estacion: str, con_objetivo: bool = False) -> pd.DataFrame:
        f = variables_estacion(grupo, self.referencias[estacion], self.escalas[estacion], self.parametros, con_objetivo)
        for codigo in self.codigos:
            f[columna_estacion(codigo)] = int(codigo == estacion)
        return f

    def predecir(self, tabla: pd.DataFrame, solo_ultima_hora: bool = False) -> pd.DataFrame:
        """Una fila por cada fila de la tabla de entrada, con su probabilidad por color y su semáforo.

        Las horas que no se pueden predecir no se descartan: quedan con un estado que explica por qué
        (sin_historia_suficiente, sin_nivel, estacion_desconocida).
        """
        entrada = tabla.copy()
        entrada["indice_entrada"] = entrada.index
        t = normalizar_tabla(entrada.assign(), deduplicar=False)
        t["indice_entrada"] = entrada.loc[t.index, "indice_entrada"].values
        calculos = []
        for estacion, grupo in t.groupby("estacion"):
            if estacion not in self.referencias:
                continue
            f = self._variables(grupo, estacion)
            historia = (f.index - f.index.min()).total_seconds() / 3600
            elegible = f["nivel_percentil"].notna() & (historia >= self.parametros.historia_minima_h)
            salida = pd.DataFrame({"estacion": estacion, "fecha_hora": f.index, "nivel_percentil": f["nivel_percentil"].values,
                                   "historia_h": historia, "prob_verde": np.nan, "prob_amarillo": np.nan, "prob_rojo": np.nan,
                                   "semaforo_num": np.nan})
            if elegible.any():
                proba = probabilidades(self.modelo, f.loc[elegible, self.columnas_x])
                salida.loc[elegible.values, ["prob_verde", "prob_amarillo", "prob_rojo"]] = proba.values
                salida.loc[elegible.values, "semaforo_num"] = semaforo_desde(proba, self.parametros.umbral_prob_rojo)
            calculos.append(salida)

        resultado = t[["indice_entrada", "estacion", "fecha_hora", "nivel_m", "lluvia_mm"]].copy()
        if calculos:
            resultado = resultado.merge(pd.concat(calculos), on=["estacion", "fecha_hora"], how="left")
        else:
            for columna in ["nivel_percentil", "historia_h", "prob_verde", "prob_amarillo", "prob_rojo", "semaforo_num"]:
                resultado[columna] = np.nan
        resultado["semaforo"] = resultado["semaforo_num"].map(lambda v: ETIQUETAS[int(v)] if pd.notna(v) else None)
        resultado["estado"] = np.select(
            [~resultado["estacion"].isin(self.referencias), resultado["nivel_m"].isna(), resultado["semaforo_num"].isna()],
            [ESTADO_ESTACION_DESCONOCIDA, ESTADO_SIN_NIVEL, ESTADO_SIN_HISTORIA], default=ESTADO_PREDICHO)
        resultado = resultado.drop(columns=["semaforo_num"]).sort_values(["estacion", "fecha_hora"]).reset_index(drop=True)
        if solo_ultima_hora:
            resultado = resultado[resultado["nivel_m"].notna()].groupby("estacion").tail(1).reset_index(drop=True)
        return resultado

    def resumir_actual(self, predicciones: pd.DataFrame, max_horas_sin_dato: int) -> dict:
        """Último semáforo de cada estación y semáforo por municipio (solo estaciones con datos recientes)."""
        con_nivel = predicciones[predicciones["nivel_m"].notna()].sort_values("fecha_hora")
        fin = predicciones["fecha_hora"].max()
        estaciones = []
        for ultima in con_nivel.groupby("estacion").tail(1).itertuples():
            info = self.metadatos["estaciones"].get(ultima.estacion, {})
            horas_sin_dato = int(round((fin - ultima.fecha_hora).total_seconds() / 3600))
            estaciones.append({
                "estacion": ultima.estacion, "municipio": info.get("municipio"), "nombre": info.get("nombre"),
                "ultima_hora": ultima.fecha_hora, "horas_sin_dato": horas_sin_dato, "vigente": horas_sin_dato <= max_horas_sin_dato,
                "nivel_m": ultima.nivel_m, "nivel_percentil": ultima.nivel_percentil, "prob_verde": ultima.prob_verde,
                "prob_amarillo": ultima.prob_amarillo, "prob_rojo": ultima.prob_rojo, "semaforo": ultima.semaforo, "estado": ultima.estado})
        municipios = []
        for municipio in sorted({e["municipio"] for e in estaciones if e["municipio"]}):
            propias = [e for e in estaciones if e["municipio"] == municipio]
            vigentes = [e["semaforo"] for e in propias if e["vigente"] and e["semaforo"]]
            semaforo = max(vigentes, key=ETIQUETAS.index) if vigentes else "Sin datos recientes"
            municipios.append({"municipio": municipio, "semaforo": semaforo, "estaciones": [e["estacion"] for e in propias]})
        return {"fin_datos": fin, "max_horas_sin_dato": max_horas_sin_dato, "estaciones": estaciones, "municipios": municipios}


    # ------------------------------------------------------------------ presentación de resultados
    def resumen_modelo(self) -> dict:
        """Metadatos del modelo sin las tablas de referencia del percentil, que son muy largas."""
        return {k: v for k, v in self.metadatos.items() if k != "referencia_percentil"}

    def tabla_de_salida(self, predicciones: pd.DataFrame) -> pd.DataFrame:
        """Predicciones listas para entregar, con la fila del archivo de origen para relacionar cada una con su registro."""
        salida = predicciones.assign(fila_archivo=predicciones["indice_entrada"] + 2)[
            ["fila_archivo", "estacion", "fecha_hora", "nivel_m", "lluvia_mm", "nivel_percentil", "prob_verde", "prob_amarillo",
             "prob_rojo", "semaforo", "estado"]].copy()
        salida["fecha_hora"] = salida["fecha_hora"].dt.strftime("%Y-%m-%d %H:%M:%S")
        salida["nivel_percentil"] = salida["nivel_percentil"].round(1)
        for columna in ("prob_verde", "prob_amarillo", "prob_rojo"):
            salida[columna] = salida[columna].round(4)
        return salida

    def resumen(self, predicciones: pd.DataFrame, max_horas_sin_dato: int) -> dict:
        """Cuántas horas se predijeron, cómo se reparten los colores y el semáforo actual por estación y municipio."""
        por_estado = predicciones["estado"].value_counts()
        return {"predicciones": int(por_estado.get(ESTADO_PREDICHO, 0)),
                "sin_historia_suficiente": int(por_estado.get(ESTADO_SIN_HISTORIA, 0)),
                "sin_nivel": int(por_estado.get(ESTADO_SIN_NIVEL, 0)),
                "distribucion": {color: int((predicciones["semaforo"] == color).sum()) for color in ETIQUETAS},
                "pregunta": f"¿El río llegará a nivel rojo en las próximas {self.parametros.horizonte_h} horas?",
                "umbral_probabilidad_rojo": self.parametros.umbral_prob_rojo,
                "actual": self.resumir_actual(predicciones, max_horas_sin_dato)}

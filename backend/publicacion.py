"""Lecturas publicadas: la serie con la que se calculan los semáforos que ve el público.

La primera vez se crea a partir de datos/datos_horarios.csv. Después crece cuando el administrador publica archivos
nuevos: las horas nuevas se agregan y, si una hora ya existía, el valor nuevo la reemplaza (un valor vacío nunca borra
un dato que ya estaba). Solo se conservan los últimos 120 días de cada estación, que es lo que el modelo necesita.
"""
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd

HORAS_A_CONSERVAR = 24 * 120
MAX_HORAS_SIN_DATO = 48           # una lectura más vieja se considera desactualizada
COLUMNAS = ["estacion", "fecha_hora", "nivel_m", "lluvia_mm"]
LLAVES = ["estacion", "fecha_hora"]
_cerrojo = threading.RLock()


class Publicacion:
    def __init__(self, carpeta: Path, origen_inicial: Path):
        self.archivo = Path(carpeta) / "lecturas.csv"
        self.origen = Path(origen_inicial)
        self._calculo = None                          # (marca, predicciones): se recalcula solo si algo cambió

    # ------------------------------------------------------------------ lecturas guardadas
    @staticmethod
    def _recortar(tabla: pd.DataFrame) -> pd.DataFrame:
        limite = tabla.groupby("estacion")["fecha_hora"].transform("max") - pd.Timedelta(hours=HORAS_A_CONSERVAR)
        return tabla[tabla["fecha_hora"] >= limite]

    def _guardar(self, tabla: pd.DataFrame) -> None:
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        temporal = self.archivo.with_suffix(".tmp")
        tabla.to_csv(temporal, index=False)
        temporal.replace(self.archivo)

    def tabla(self, codigos) -> pd.DataFrame:
        with _cerrojo:
            if not self.archivo.exists():
                inicial = pd.read_csv(self.origen, usecols=COLUMNAS, dtype={"estacion": str}, parse_dates=["fecha_hora"])
                self._guardar(self._recortar(inicial[inicial["estacion"].isin(codigos)]))
            return pd.read_csv(self.archivo, dtype={"estacion": str}, parse_dates=["fecha_hora"])

    def integrar(self, nuevas: pd.DataFrame, codigos) -> dict:
        """Suma las lecturas nuevas (ya normalizadas: estación, hora, nivel y lluvia) a las publicadas."""
        with _cerrojo:
            actuales = self.tabla(codigos).set_index(LLAVES)
            nuevas = nuevas[COLUMNAS].drop_duplicates(LLAVES, keep="last").set_index(LLAVES)
            ya_existian = nuevas.index.isin(actuales.index)
            combinadas = self._recortar(nuevas.combine_first(actuales).reset_index().sort_values(LLAVES))
            conservadas = nuevas.index.isin(pd.MultiIndex.from_frame(combinadas[LLAVES]))      # las muy antiguas no se guardan
            self._guardar(combinadas)
            return {"filas_recibidas": len(nuevas), "agregadas": int((conservadas & ~ya_existian).sum()),
                    "actualizadas": int((conservadas & ya_existian).sum()), "descartadas_por_antiguas": int((~conservadas).sum()),
                    "estaciones": sorted(nuevas.reset_index()["estacion"].unique())}

    # ------------------------------------------------------------------ lo que ve el público
    def predicciones(self, pipeline) -> pd.DataFrame:
        with _cerrojo:
            tabla = self.tabla(pipeline.codigos)
            marca = (pipeline.version, self.archivo.stat().st_mtime_ns)
            if self._calculo is None or self._calculo[0] != marca:
                self._calculo = (marca, pipeline.predecir(tabla))
            return self._calculo[1]

    def estaciones(self, pipeline, ahora: datetime | None = None) -> dict:
        """Semáforo más reciente de cada estación, con los datos que necesita el mapa."""
        ahora = ahora or datetime.now()
        predicciones = self.predicciones(pipeline)
        ultimas = (predicciones[predicciones["nivel_m"].notna()].sort_values("fecha_hora").groupby("estacion").tail(1).set_index("estacion"))
        lista = []
        for codigo, info in pipeline.metadatos["estaciones"].items():
            base = {"codigo": codigo, "nombre": info["nombre"], "municipio": info["municipio"],
                    "latitud": info.get("latitud"), "longitud": info.get("longitud")}
            if codigo not in ultimas.index:
                lista.append({**base, "semaforo": None, "estado": "sin_datos"})
                continue
            u = ultimas.loc[codigo]
            horas = (ahora - u["fecha_hora"]).total_seconds() / 3600
            lista.append({**base, "semaforo": u["semaforo"], "estado": u["estado"], "prob_verde": u["prob_verde"],
                          "prob_amarillo": u["prob_amarillo"], "prob_rojo": u["prob_rojo"], "nivel_m": u["nivel_m"],
                          "ultima_lectura": u["fecha_hora"], "horas_desde_ultima_lectura": round(horas),
                          "desactualizada": horas > MAX_HORAS_SIN_DATO})
        return {"modelo_version": pipeline.version, "horizonte_horas": pipeline.parametros.horizonte_h,
                "umbral_aviso_rojo": pipeline.parametros.umbral_prob_rojo,
                "ultimo_dato": predicciones["fecha_hora"].max(), "estaciones": lista}

    def detalle(self, pipeline, codigo: str, horas: int = 168, ahora: datetime | None = None) -> dict:
        """Lo mismo que `estaciones`, para una sola estación, más sus últimas horas de nivel y riesgo."""
        resumen = next((e for e in self.estaciones(pipeline, ahora)["estaciones"] if e["codigo"] == codigo), None)
        if resumen is None:
            raise KeyError(codigo)
        predicciones = self.predicciones(pipeline)
        propias = predicciones[(predicciones["estacion"] == codigo) & predicciones["nivel_m"].notna()].sort_values("fecha_hora").tail(horas)
        return {**resumen, "horizonte_horas": pipeline.parametros.horizonte_h,
                "historial": propias[["fecha_hora", "nivel_m", "prob_rojo", "semaforo"]]}

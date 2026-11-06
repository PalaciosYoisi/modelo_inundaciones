"""Lecturas publicadas: la serie con la que se calculan los semáforos que ve el público. Viven en la tabla `lecturas` de MySQL.

La primera vez la tabla se llena con lo que ya había publicado (datos/publicado/lecturas.csv, de versiones anteriores) o, si
no existe, con datos/datos_horarios.csv. Después crece cuando un administrador confirma un lote: las horas nuevas se agregan
y, si una hora ya existía, el valor nuevo la reemplaza (un valor vacío nunca borra un dato que ya estaba).
Todo se conserva. Para calcular el semáforo solo se leen los últimos 120 días de cada estación, que es lo que el modelo necesita;
el reentrenamiento, en cambio, lee todo.
"""
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd

from base_datos import ErrorBaseDatos, conexion
from hora import hora_actual

HORAS_DE_VENTANA = 24 * 120         # el semáforo se calcula con los últimos 120 días de cada estación; se guarda todo
MAX_HORAS_SIN_DATO = 48           # una lectura más vieja se considera desactualizada
COLUMNAS = ["estacion", "fecha_hora", "nivel_m", "lluvia_mm"]
LLAVES = ["estacion", "fecha_hora"]
LOTE = 1000                       # filas por envío a la base
_cerrojo = threading.RLock()


def _a_python(tabla: pd.DataFrame) -> list[tuple]:
    """Filas listas para la base: los vacíos (NaN) pasan a None."""
    return list(tabla.astype(object).where(tabla.notna(), None).itertuples(index=False, name=None))


class Publicacion:
    def __init__(self, origen_inicial: Path, origen_publicado_anterior: Path | None = None):
        self.origen = Path(origen_inicial)
        self.anterior = Path(origen_publicado_anterior) if origen_publicado_anterior else None
        self._calculo = None                          # (marca, predicciones): se recalcula solo si algo cambió

    # ------------------------------------------------------------------ lecturas guardadas
    @staticmethod
    def _subir(cursor, tabla: pd.DataFrame) -> None:
        """Agrega o actualiza lecturas. Un valor vacío no borra el que ya estaba."""
        sentencia = ("INSERT INTO lecturas (estacion, fecha_hora, nivel_m, lluvia_mm, actualizado) VALUES (%s, %s, %s, %s, %s) "
                     "ON DUPLICATE KEY UPDATE nivel_m = COALESCE(VALUES(nivel_m), nivel_m), lluvia_mm = COALESCE(VALUES(lluvia_mm), lluvia_mm), actualizado = VALUES(actualizado)")
        momento = hora_actual()
        filas = [fila + (momento,) for fila in _a_python(tabla[COLUMNAS])]
        for i in range(0, len(filas), LOTE):
            cursor.executemany(sentencia, filas[i:i + LOTE])

    def _sembrar(self, codigos) -> None:
        """Si la tabla está vacía (primer arranque), la llena con lo que ya había."""
        with conexion() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM lecturas")
            if cursor.fetchone()["n"]:
                return
            leer = lambda ruta: pd.read_csv(ruta, usecols=COLUMNAS, dtype={"estacion": str}, parse_dates=["fecha_hora"])
            inicial = leer(self.origen)
            if self.anterior and self.anterior.exists():          # lo que se publicó en versiones anteriores es más reciente que el archivo inicial
                inicial = pd.concat([inicial, leer(self.anterior)]).drop_duplicates(LLAVES, keep="last")
            self._subir(cursor, inicial[inicial["estacion"].isin(codigos)])

    def tabla(self, codigos, completa: bool = False) -> pd.DataFrame:
        """Las lecturas guardadas. Por defecto, solo la ventana reciente de cada estación (lo que se usa para el semáforo); completa=True trae todo."""
        with _cerrojo:
            self._sembrar(codigos)
            with conexion() as cursor:
                if completa:
                    cursor.execute("SELECT estacion, fecha_hora, nivel_m, lluvia_mm FROM lecturas ORDER BY estacion, fecha_hora")
                else:
                    cursor.execute("SELECT l.estacion, l.fecha_hora, l.nivel_m, l.lluvia_mm FROM lecturas l "
                                   "JOIN (SELECT estacion, MAX(fecha_hora) AS ultima FROM lecturas GROUP BY estacion) u ON u.estacion = l.estacion "
                                   "WHERE l.fecha_hora >= u.ultima - INTERVAL %s HOUR ORDER BY l.estacion, l.fecha_hora", (HORAS_DE_VENTANA,))
                filas = cursor.fetchall()
            tabla = pd.DataFrame(filas, columns=COLUMNAS)
            tabla["fecha_hora"] = pd.to_datetime(tabla["fecha_hora"])
            return tabla.astype({"nivel_m": float, "lluvia_mm": float})

    def integrar(self, nuevas: pd.DataFrame, codigos) -> dict:
        """Suma las lecturas nuevas (ya normalizadas: estación, hora, nivel y lluvia) a las guardadas. Nada se recorta ni se borra."""
        with _cerrojo:
            guardadas = self.tabla(codigos, completa=True).set_index(LLAVES)
            nuevas = nuevas[COLUMNAS].drop_duplicates(LLAVES, keep="last").set_index(LLAVES)
            ya_existian = nuevas.index.isin(guardadas.index)
            with conexion() as cursor:
                self._subir(cursor, nuevas.reset_index())
            return {"filas_recibidas": len(nuevas), "agregadas": int((~ya_existian).sum()), "actualizadas": int(ya_existian.sum()),
                    "estaciones": sorted(nuevas.reset_index()["estacion"].unique())}

    # ------------------------------------------------------------------ lo que ve el público
    def predicciones(self, pipeline) -> pd.DataFrame:
        with _cerrojo:
            try:
                with conexion() as cursor:
                    cursor.execute("SELECT COUNT(*) AS n, MAX(actualizado) AS ultima FROM lecturas")
                    estado = cursor.fetchone()
                marca = (pipeline.version, estado["n"], estado["ultima"])
                if self._calculo is None or self._calculo[0] != marca:
                    self._calculo = (marca, pipeline.predecir(self.tabla(pipeline.codigos)))
            except ErrorBaseDatos:
                if self._calculo is None:             # sin base y sin nada calculado antes: no hay qué mostrar
                    raise                             # si ya había algo, se sigue mostrando lo último
            return self._calculo[1]

    def estaciones(self, pipeline, ahora: datetime | None = None) -> dict:
        """Semáforo más reciente de cada estación, con los datos que necesita el mapa."""
        ahora = ahora or hora_actual()
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

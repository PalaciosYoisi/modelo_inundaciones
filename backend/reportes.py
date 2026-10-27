"""Archivos para descargar con los datos ya combinados: nivel del río, lluvia y semáforo de cada hora.

No lee ni escribe archivos: recibe las predicciones de lo publicado y devuelve una tabla o los bytes de un CSV.
"""
import re
import unicodedata

import pandas as pd

TEXTO_SEMAFORO = {"Verde": "Nivel normal", "Amarillo": "Nivel alto", "Rojo": "Peligro de crecida"}     # las mismas palabras de la página pública


def datos_combinados(predicciones: pd.DataFrame, estaciones: dict, codigo: str | None = None, dias: int | None = None) -> pd.DataFrame:
    """Una fila por estación y hora, con nombres de columna claros. codigo: una sola estación. dias: solo los últimos días con datos."""
    tabla = predicciones[["estacion", "fecha_hora", "nivel_m", "lluvia_mm", "prob_rojo", "semaforo"]]
    ultima = tabla["fecha_hora"].max()
    if codigo:
        tabla = tabla[tabla["estacion"] == codigo]
    if dias:
        tabla = tabla[tabla["fecha_hora"] > ultima - pd.Timedelta(days=dias)]
    tabla = tabla[tabla["nivel_m"].notna() | tabla["lluvia_mm"].notna()].sort_values(["estacion", "fecha_hora"])
    dato = lambda c, campo: estaciones.get(c, {}).get(campo, c if campo == "nombre" else "")
    return pd.DataFrame({
        "Código de estación": tabla["estacion"].values,
        "Estación": [dato(c, "nombre") for c in tabla["estacion"]],
        "Municipio": [dato(c, "municipio") for c in tabla["estacion"]],
        "Fecha y hora": tabla["fecha_hora"].dt.strftime("%Y-%m-%d %H:%M").values,
        "Nivel del río (m)": tabla["nivel_m"].round(3).values,
        "Lluvia (mm)": tabla["lluvia_mm"].round(2).values,
        "Posibilidad de peligro (%)": (tabla["prob_rojo"] * 100).round(1).values,
        "Semáforo": tabla["semaforo"].map(TEXTO_SEMAFORO).values,
    })


def a_csv(tabla: pd.DataFrame) -> bytes:
    """CSV con codificación que Excel abre bien (UTF-8 con marca)."""
    return tabla.to_csv(index=False).encode("utf-8-sig")


def nombre_de_archivo(estaciones: dict, codigo: str | None = None, dias: int | None = None) -> str:
    base = "datos_" + (estaciones.get(codigo, {}).get("nombre", codigo) if codigo else "todas_las_estaciones")
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_") + (f"_{dias}_dias" if dias else "") + ".csv"

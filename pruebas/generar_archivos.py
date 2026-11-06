"""Genera los archivos con los que se demuestran los escenarios de prueba obligatorios (carpeta pruebas/archivos/).

Se corre desde la raíz del proyecto:
    python pruebas/generar_archivos.py          # los escenarios 1 a 7 (sin internet)
    python pruebas/generar_archivos.py --nuevos # además, el 8: datos reales nuevos, consultados a datos.gov.co

Los archivos 1 a 7 salen de datos reales del repositorio (la tabla horaria de la estación Quibdó y la tabla de emergencias de la UNGRD),
alterados a propósito de una sola manera cada uno. Nada se inventa: lo que se altera queda dicho en cada función.
"""
import argparse
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "pruebas" / "archivos"
HORARIOS = RAIZ / "datos" / "datos_horarios.csv"
ESTACION_BASE, DESDE_BASE, HORAS_BASE = "0011047020", "2026-03-20 00:00:00", 1200     # Quibdó: 1.200 horas seguidas, con nivel y lluvia en todas
EMERGENCIAS = RAIZ / "datos" / "Emergencias_UNGRD_V2_20261016.csv"
SEMILLA = 7


def guardar(tabla: pd.DataFrame, nombre: str) -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(SALIDA / nombre, index=False, encoding="utf-8")
    print(f"  {nombre:<42} {len(tabla):>5} filas, {len(tabla.columns)} columnas")


def base() -> pd.DataFrame:
    """Un tramo real de la tabla horaria, sin ninguna hora vacía."""
    t = pd.read_csv(HORARIOS, dtype=str)
    t = t[(t["estacion"] == ESTACION_BASE) & (t["fecha_hora"] >= DESDE_BASE)].sort_values("fecha_hora").head(HORAS_BASE)
    assert len(t) == HORAS_BASE and t[["nivel_m", "lluvia_mm"]].notna().all().all()
    return t[["estacion", "fecha_hora", "nivel_m", "lluvia_mm"]].reset_index(drop=True)


def escenario_1() -> None:
    """Compatible: el tramo real tal como está (estación 0011047020, 1.200 horas seguidas, sin huecos)."""
    guardar(base(), "01_compatible.csv")


def escenario_2() -> None:
    """Falta una variable obligatoria: se quita la columna lluvia_mm."""
    guardar(base().drop(columns=["lluvia_mm"]), "02_falta_variable_lluvia_mm.csv")


def escenario_3() -> None:
    """Tipos incompatibles: texto en nivel_m (30 filas) y fechas ilegibles (5 filas)."""
    t = base()
    azar = np.random.default_rng(SEMILLA)
    filas = azar.choice(len(t), size=30, replace=False)
    t.loc[filas, "nivel_m"] = azar.choice(["alto", "bajo", "N/D", "sin dato", "desbordado"], size=30)
    for i, f in enumerate(azar.choice(np.setdiff1d(np.arange(len(t)), filas), size=5, replace=False)):
        t.loc[f, "fecha_hora"] = ["03/06/2026 4pm", "ayer", "2026-13-45 99:00:00", "junio 5", "??"][i]
    guardar(t, "03_tipos_incompatibles.csv")


def escenario_4() -> None:
    """Valores faltantes en nivel_m, la variable necesaria para predecir: se vacía el 30 % de las horas, en bloques de 12 horas seguidas."""
    t = base()
    azar = np.random.default_rng(SEMILLA)
    bloques = azar.choice(np.arange(0, len(t) - 12, 12), size=int(len(t) * 0.30 / 12), replace=False)
    for b in bloques:
        t.loc[b:b + 11, "nivel_m"] = ""
    guardar(t, "04_valores_faltantes_en_nivel.csv")


def escenario_5() -> None:
    """Categoría no contemplada: 120 filas con una estación que el modelo nunca vio (0099999999)."""
    t = base()
    t.loc[600:719, "estacion"] = "0099999999"
    guardar(t, "05_estacion_desconocida.csv")


def escenario_6() -> None:
    """Columnas adicionales que el modelo no usa: observacion, operador y municipio."""
    t = base()
    t["municipio"] = "Quibdó"
    t["operador"] = "IDEAM"
    t["observacion"] = ""
    guardar(t, "06_columnas_adicionales.csv")


def escenario_7() -> None:
    """No sirve para predecir pero sí para el EDA: emergencias reales de la UNGRD (otro tema, otras columnas), con 8 de sus 61 columnas y los números ya sin separador de miles."""
    e = pd.read_csv(EMERGENCIAS, dtype=str)
    elegidas = ["FECHA", "MUNICIPIO", "EVENTO", "FALLECIDOS", "HERIDOS", "PERSONAS", "FAMILIAS", "VIVIENDAS DESTRUIDAS"]
    t = e[elegidas].copy()
    t.columns = ["fecha", "municipio", "evento", "fallecidos", "heridos", "personas", "familias", "viviendas_destruidas"]
    t["fecha"] = pd.to_datetime(t["fecha"], format="%Y %b %d %I:%M:%S %p", errors="coerce").dt.strftime("%Y-%m-%d")
    for c in ["fallecidos", "heridos", "personas", "familias", "viviendas_destruidas"]:
        t[c] = pd.to_numeric(t[c].str.replace(",", "", regex=False), errors="coerce")
    guardar(t, "07_solo_para_eda_emergencias.csv")


def escenario_8() -> None:
    """Datos nuevos y reales (desde el 25 de octubre de 2026, lo que el modelo v3 no conoce), consultados a datos.gov.co y preparados por la misma
    lógica del sistema (backend/preparacion.py). Quedan en el formato combinado: estacion, fecha_hora, nivel_m, lluvia_mm."""
    sys.path.insert(0, str(RAIZ / "backend"))
    import json

    from consulta import consultar
    from pipeline import PipelineSemaforo
    from preparacion import por_hora, referencias_de, solo_cambios

    pipeline = PipelineSemaforo.cargar(RAIZ / "modelos" / "v3")
    mapa = json.loads((RAIZ / "datos" / "pluviometros.json").read_text(encoding="utf-8"))
    codigos = pipeline.codigos
    pluviometros = sorted({p for c in codigos for p in mapa["estaciones"].get(c, [])})
    semilla = pd.read_csv(RAIZ / "datos" / "datos_horarios.csv", dtype={"estacion": str}, parse_dates=["fecha_hora"])
    publicado = semilla[semilla["estacion"].isin(codigos)][["estacion", "fecha_hora", "nivel_m", "lluvia_mm"]].reset_index(drop=True)
    desde = datetime(2026, 10, 24, 12)
    print("  consultando datos.gov.co…")
    nivel, lluvia = consultar("nivel", codigos, desde), consultar("lluvia", pluviometros, desde)
    tabla, _descartes, _sin_datos, _marcas = por_hora(nivel, lluvia, mapa, publicado, referencias_de(pipeline.metadatos), codigos)
    tabla, _ = solo_cambios(tabla.sort_values(["estacion", "fecha_hora"]).reset_index(drop=True), publicado)
    tabla = tabla[tabla["fecha_hora"] > publicado["fecha_hora"].max()].copy()
    tabla["fecha_hora"] = tabla["fecha_hora"].dt.strftime("%Y-%m-%d %H:%M:%S")
    guardar(tabla[["estacion", "fecha_hora", "nivel_m", "lluvia_mm"]], "08_nuevos_datos_reales.csv")


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    entrada = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    entrada.add_argument("--nuevos", action="store_true", help="Genera también el escenario 8 (necesita internet).")
    args = entrada.parse_args()
    print(f"Archivos en {SALIDA}")
    for f in (escenario_1, escenario_2, escenario_3, escenario_4, escenario_5, escenario_6, escenario_7):
        f()
    if args.nuevos:
        escenario_8()

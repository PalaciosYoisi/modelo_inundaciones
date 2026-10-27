"""Prepara la tabla horaria (estación-hora) a partir de los CSV de datos.gov.co / IDEAM.

Cada fila es una hora de una estación de nivel del río, con:
  * nivel_m:  nivel promedio de la hora (metros).
  * lluvia_mm: lluvia de la hora, promediada entre los pluviómetros más cercanos (hasta 3, a menos de 25 km).
  * n_pluviometros: cuántos pluviómetros aportaron dato esa hora.

Limpieza: sensor principal por estación, niveles imposibles (<= 0 o fuera de 10 veces el rango intercuartílico),
días con el nivel pegado en el mismo valor 5 o más días seguidos, picos aislados de una hora, lluvia negativa, días de más de 500 mm en un
pluviómetro y pluviómetros con promedios diarios imposibles.

Uso:  python preparar_datos.py
"""
import json
import re
import sys
import unicodedata
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RUTA = Path(__file__).parent / "datos"
ARCHIVOS_LLUVIA = ["Precipitación_20261026_2022.csv", "Precipitación_20261016.csv", "Precipitación_20261026.csv"]
ARCHIVOS_NIVEL = ["Nivel_Instantáneo_del_Rio_20261026_2022.csv", "Nivel_Instantáneo_del_Rio_20261016.csv",
                  "Nivel_Instantáneo_del_Rio_20261026.csv"]
SALIDA = RUTA / "datos_horarios.csv"
SALIDA_PLUVIOMETROS = RUTA / "pluviometros.json"      # qué pluviómetros usa cada estación; lo lee backend/preparacion.py

RADIO_LLUVIA_KM = 25        # distancia máxima entre la estación de nivel y un pluviómetro que se usa para ella
MAX_PLUVIOMETROS = 3        # cuántos pluviómetros cercanos se promedian
MIN_HORAS_NIVEL = 500       # una estación con menos horas de nivel no se incluye
ALIAS_MUNICIPIOS = {
    "EL LITORAL DEL SAN JUAN": "LITORAL DEL SAN JUAN", "LITORAL DE SAN JUAN": "LITORAL DEL SAN JUAN",
    "EL CARMEN DE ATRATO": "CARMEN DE ATRATO", "EL CANTON DEL SAN PABLO": "CANTON DE SAN PABLO",
    "EL CANTON DE SAN PABLO": "CANTON DE SAN PABLO", "CANTON DEL SAN PABLO": "CANTON DE SAN PABLO",
    "SAN JOSE DE PALMAR": "SAN JOSE DEL PALMAR", "QUIBDO (SAN FRANCISCO DE QUIBDO)": "QUIBDO",
    "EL CARMEN": "CARMEN DE ATRATO",
}
COLUMNAS_IDEAM = ["CodigoEstacion", "CodigoSensor", "FechaObservacion", "ValorObservado", "NombreEstacion",
                  "Municipio", "Latitud", "Longitud"]


def limpiar_texto(texto):
    if pd.isna(texto):
        return ""
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    return " ".join(texto.upper().split())


def normalizar_municipio(nombre):
    nombre = ALIAS_MUNICIPIOS.get(limpiar_texto(nombre), limpiar_texto(nombre))
    nombre = re.sub(r"\(.*?\)", "", nombre).strip()
    return ALIAS_MUNICIPIOS.get(nombre, nombre)


def nombre_estacion(nombre):
    nombre = re.sub(r"\[.*?\]", "", limpiar_texto(nombre))
    return re.sub(r"\s*-\s*AUT\b", "", nombre).strip()


def limpiar_columna(serie, funcion=limpiar_texto):
    valores = {v: funcion(v) for v in serie.dropna().unique()}
    return serie.map(valores).fillna("")


def leer_csv(ruta, **opciones):
    for codificacion in ["utf-8", "latin-1"]:
        try:
            return pd.read_csv(ruta, encoding=codificacion, low_memory=False, **opciones)
        except UnicodeDecodeError:
            continue


def a_numero(serie):
    return pd.to_numeric(serie.astype(str).str.replace(",", ".", regex=False), errors="coerce")


def convertir_fecha_ideam(serie):
    fechas = pd.to_datetime(serie, format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
    for formato in ["%Y %b %d %I:%M:%S %p", "mixed"]:
        faltan = fechas.isna()
        if faltan.any():
            fechas[faltan] = pd.to_datetime(serie[faltan], format=formato, errors="coerce")
    return fechas


def cargar_ideam(archivos, nombre):
    partes = []
    for archivo in archivos:
        df = leer_csv(RUTA / archivo, usecols=COLUMNAS_IDEAM,
                      dtype={"CodigoEstacion": str, "CodigoSensor": str, "ValorObservado": str})
        df["fecha_hora"] = convertir_fecha_ideam(df["FechaObservacion"])
        df["valor"] = a_numero(df["ValorObservado"])
        df["lat"], df["lon"] = a_numero(df["Latitud"]), a_numero(df["Longitud"])
        df["municipio"] = limpiar_columna(df["Municipio"], normalizar_municipio)
        df = df.dropna(subset=["fecha_hora", "valor"])
        print(f"  {archivo}: {len(df):,} filas válidas ({df['fecha_hora'].min().date()} a {df['fecha_hora'].max().date()})")
        partes.append(df[["CodigoEstacion", "CodigoSensor", "NombreEstacion", "municipio", "fecha_hora", "valor", "lat", "lon"]])
    df = pd.concat(partes, ignore_index=True)
    antes = len(df)
    df = df.drop_duplicates(subset=["CodigoEstacion", "CodigoSensor", "fecha_hora"])
    print(f"{nombre}: {len(df):,} filas ({antes - len(df):,} duplicadas entre archivos)")
    return df


def sensor_principal(df):
    """Una estación puede tener varios sensores: se conserva el que más registros tiene."""
    conteo = df.groupby(["CodigoEstacion", "CodigoSensor"]).size().reset_index(name="n")
    principal = conteo.sort_values("n").drop_duplicates("CodigoEstacion", keep="last")
    df = df.merge(principal[["CodigoEstacion", "CodigoSensor"]], on=["CodigoEstacion", "CodigoSensor"])
    return df.drop_duplicates(subset=["CodigoEstacion", "fecha_hora"])


def es_acumulado(valores):
    cambios = valores.diff().dropna()
    cambios = cambios[cambios != 0]
    return len(cambios) >= 20 and (cambios > 0).mean() > 0.9 and valores.max() > 100


def acumulado_a_intervalo(valores):
    intervalo = valores.diff()
    reinicio = intervalo < 0
    intervalo[reinicio] = valores[reinicio]
    return intervalo.fillna(0)


def limpiar_nivel(nivel):
    nivel = sensor_principal(nivel)
    nivel = nivel[nivel["valor"] > 0]
    q1 = nivel.groupby("CodigoEstacion")["valor"].transform(lambda x: x.quantile(0.25))
    q3 = nivel.groupby("CodigoEstacion")["valor"].transform(lambda x: x.quantile(0.75))
    rango = q3 - q1
    nivel = nivel[(nivel["valor"] >= q1 - 10 * rango) & (nivel["valor"] <= q3 + 10 * rango)].copy()
    # Días con el nivel máximo pegado en el mismo valor 5 o más días seguidos: sensor trabado
    nivel["fecha"] = nivel["fecha_hora"].dt.normalize()
    diario = nivel.groupby(["CodigoEstacion", "fecha"])["valor"].max().reset_index().sort_values(["CodigoEstacion", "fecha"])
    cambio = diario.groupby("CodigoEstacion")["valor"].diff() != 0
    racha = diario.groupby(cambio.cumsum())["valor"].transform("size")
    malos = diario[racha >= 5][["CodigoEstacion", "fecha"]].assign(malo=True)
    print("Días de nivel pegado 5+ días seguidos (se eliminan):", len(malos))
    nivel = nivel.merge(malos, on=["CodigoEstacion", "fecha"], how="left")
    return nivel[nivel["malo"].isna()].drop(columns=["malo", "fecha"])


def limpiar_lluvia(lluvia):
    lluvia = sensor_principal(lluvia).sort_values(["CodigoEstacion", "fecha_hora"]).reset_index(drop=True)
    acumuladas = [c for c, v in lluvia.groupby("CodigoEstacion")["valor"] if es_acumulado(v)]
    es_acum = lluvia["CodigoEstacion"].isin(acumuladas)
    lluvia.loc[es_acum, "valor"] = lluvia[es_acum].groupby("CodigoEstacion")["valor"].transform(acumulado_a_intervalo)
    lluvia = lluvia[lluvia["valor"] >= 0].copy()
    lluvia["fecha"] = lluvia["fecha_hora"].dt.normalize()
    diaria = lluvia.groupby(["CodigoEstacion", "fecha"])["valor"].sum().reset_index()
    dias_absurdos = diaria[diaria["valor"] > 500][["CodigoEstacion", "fecha"]].assign(malo=True)
    diaria = diaria[diaria["valor"] <= 500]
    promedio = diaria.groupby("CodigoEstacion")["valor"].mean()
    imposibles = promedio[promedio > 60].index
    print(f"Lluvia: {len(dias_absurdos)} días de más de 500 mm descartados | {len(imposibles)} pluviómetros con promedio imposible")
    lluvia = lluvia.merge(dias_absurdos, on=["CodigoEstacion", "fecha"], how="left")
    return lluvia[lluvia["malo"].isna() & ~lluvia["CodigoEstacion"].isin(imposibles)].drop(columns=["malo", "fecha"]), acumuladas


def picos_aislados(serie_horaria):
    """Horas cuyo nivel se aleja mucho de sus vecinas, mientras las vecinas coinciden entre sí (falla del sensor).

    Una crecida real dura varias horas, así que sus vecinas también están altas y no se marca.
    """
    x = serie_horaria
    escala = max(x.quantile(0.75) - x.quantile(0.25), 1e-6)
    vecinas = np.column_stack([x.shift(k).values for k in (-3, -2, -1, 1, 2, 3)])
    validas = np.isfinite(vecinas).sum(axis=1)
    with np.errstate(all="ignore"):
        mediana = np.nanmedian(vecinas, axis=1)
        dispersion = np.nanmax(vecinas, axis=1) - np.nanmin(vecinas, axis=1)
    return pd.Series((validas >= 4) & (np.abs(x.values - mediana) > 3 * escala) & (dispersion < escala), index=x.index)


def distancia_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def main():
    print("== Lluvia");  lluvia = cargar_ideam(ARCHIVOS_LLUVIA, "Lluvia")
    print("== Nivel");   nivel = cargar_ideam(ARCHIVOS_NIVEL, "Nivel")
    nivel = limpiar_nivel(nivel)
    lluvia, acumuladas = limpiar_lluvia(lluvia)

    coordenadas = pd.concat([nivel, lluvia]).groupby("CodigoEstacion")[["lat", "lon"]].median()
    nivel["h"] = nivel["fecha_hora"].dt.floor("h")
    lluvia["h"] = lluvia["fecha_hora"].dt.floor("h")
    nivel_h = nivel.groupby(["CodigoEstacion", "h"])["valor"].mean().unstack(0)
    lluvia_h = lluvia.groupby(["CodigoEstacion", "h"])["valor"].sum().unstack(0)

    # Una misma estación puede cambiar de nombre con los años: se identifica por su código
    info = nivel.groupby("CodigoEstacion").agg(municipio=("municipio", lambda x: x.mode()[0]))
    info["nombre_estacion"] = nivel.assign(n=nivel["NombreEstacion"].map(nombre_estacion)).groupby("CodigoEstacion")["n"].agg(lambda x: x.mode()[0])
    info["horas_nivel"] = nivel_h.notna().sum()
    info = info[info["horas_nivel"] >= MIN_HORAS_NIVEL]
    repetido = info.duplicated(["municipio", "nombre_estacion"], keep=False)
    info.loc[repetido, "nombre_estacion"] += " " + info.index[repetido]

    partes, resumen, pluviometros = [], [], {}
    for codigo, e in info.iterrows():
        cerca = sorted((distancia_km(*coordenadas.loc[codigo, ["lat", "lon"]], *coordenadas.loc[r, ["lat", "lon"]]), r)
                       for r in lluvia_h.columns)
        pluv = [r for d, r in cerca if d <= RADIO_LLUVIA_KM][:MAX_PLUVIOMETROS]
        pluviometros[codigo] = pluv
        serie_nivel = nivel_h[codigo].dropna()
        horas = pd.date_range(serie_nivel.index.min(), serie_nivel.index.max(), freq="h")
        d = pd.DataFrame({"fecha_hora": horas})
        nivel_estacion = nivel_h[codigo].reindex(horas)
        picos = picos_aislados(nivel_estacion)
        print(f"  {codigo}: {int(picos.sum())} picos aislados eliminados")
        d["nivel_m"] = nivel_estacion.where(~picos).values
        if pluv:
            d["lluvia_mm"] = lluvia_h[pluv].reindex(horas).mean(axis=1, skipna=True).values
            d["n_pluviometros"] = lluvia_h[pluv].reindex(horas).notna().sum(axis=1).values
        else:
            d["lluvia_mm"], d["n_pluviometros"] = np.nan, 0
        d.insert(0, "estacion", codigo)
        d.insert(1, "nombre_estacion", e["nombre_estacion"])
        d.insert(2, "municipio", e["municipio"])
        partes.append(d)
        resumen.append(dict(estacion=codigo, municipio=e["municipio"], nombre=e["nombre_estacion"], horas_nivel=int(e["horas_nivel"]),
                            desde=horas[0].date(), hasta=horas[-1].date(), horas_con_lluvia=int(d["lluvia_mm"].notna().sum()),
                            pluviometros=len(pluv)))
    df = pd.concat(partes, ignore_index=True)
    print("\nEstaciones de nivel incluidas:")
    print(pd.DataFrame(resumen).to_string(index=False))
    df["nivel_m"] = df["nivel_m"].round(3)
    df["lluvia_mm"] = df["lluvia_mm"].round(2)
    df.to_csv(SALIDA, index=False)
    SALIDA_PLUVIOMETROS.write_text(json.dumps({"radio_km": RADIO_LLUVIA_KM, "estaciones": pluviometros, "acumulados": sorted(acumuladas)}, indent=1), encoding="utf-8")
    print(f"\nGuardado: {SALIDA} | {len(df):,} filas")


if __name__ == "__main__":
    main()

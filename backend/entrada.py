"""Archivos que sube el administrador: reconoce de qué tipo es cada uno, lo lee y lo revisa por separado.

Tipos que reconoce:
  nivel      CSV de datos.gov.co «Nivel instantáneo» (CodigoEstacion, FechaObservacion, ValorObservado, DescripcionSensor…)
  lluvia     CSV de datos.gov.co «Precipitación» (mismas columnas; el sensor dice PRECIPITACIÓN)
  combinado  CSV o Excel ya armado: estacion, fecha_hora, nivel_m, lluvia_mm (una fila por estación y hora)
Los archivos de datos.gov.co se leen por partes y solo se guardan las estaciones que usa el modelo, así que pueden ser muy grandes.
Este módulo no unifica ni publica nada: solo devuelve, por cada archivo, un informe y sus datos.
"""
import csv
import io
import unicodedata

import pandas as pd

from pipeline import normalizar_tabla
from preparacion import leer_fechas
from validacion import validar

CLAVE_CRUDA = ["codigoestacion", "fechaobservacion", "valorobservado"]
CLAVE_COMBINADA = ["estacion", "fecha_hora", "nivel_m", "lluvia_mm"]
COLUMNAS_CRUDAS = ["codigoestacion", "codigosensor", "fechaobservacion", "valorobservado", "descripcionsensor"]
TIPOS = {"nivel": "Nivel del río", "lluvia": "Lluvia", "combinado": "Datos ya combinados", "desconocido": "No reconocido"}
EXTENSIONES = (".csv", ".xlsx")
POR_PARTES = 200_000
MAX_ILEGIBLES = 0.05                                  # más de esto de fechas o valores ilegibles: el archivo no sirve


def _normal(columna) -> str:
    return str(columna).strip().lstrip("﻿").lower()


def _mayusculas(texto) -> str:
    return unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii").upper()


def _hallazgo(severidad: str, mensaje: str) -> dict:
    return {"severidad": severidad, "mensaje": mensaje}


def _informe(nombre: str, tipo: str = "desconocido", hallazgos: list | None = None, **datos) -> dict:
    hallazgos = hallazgos or []
    estado = "error" if any(h["severidad"] == "error" for h in hallazgos) else "advertencias" if hallazgos else "ok"
    return {"archivo": nombre, "tipo": tipo, "tipo_texto": TIPOS[tipo], "estado": estado, "filas": 0, "filas_utiles": 0, "estaciones": [],
            "desde": None, "hasta": None, **datos, "hallazgos": hallazgos}


# ---------------------------------------------------------------- lectura de CSV
def _abrir(flujo):
    """Prueba UTF-8 y luego Latin-1; devuelve (codificación, separador, columnas del encabezado)."""
    flujo.seek(0)
    primera = flujo.readline()
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            texto = primera.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    separador = max([",", ";", "\t"], key=texto.count)
    flujo.seek(0)
    return codificacion, separador, [_normal(c) for c in next(csv.reader([texto.strip()], delimiter=separador))]


def _partes(flujo, codificacion: str, separador: str, columnas: list[str], **opciones):
    """Lee el CSV por partes sin cerrar el archivo (se vuelve a leer más de una vez)."""
    flujo.seek(0)
    texto = io.TextIOWrapper(flujo, encoding=codificacion, newline="")
    try:
        yield from pd.read_csv(texto, sep=separador, dtype=str, usecols=lambda c: _normal(c) in columnas, chunksize=POR_PARTES, **opciones)
    finally:
        texto.detach()


def _tipo_de_sensor(descripciones: pd.Series) -> tuple[str, str | None]:
    """(tipo, motivo si no sirve) según lo que dice la columna DescripcionSensor."""
    texto = _mayusculas(descripciones.mode().iloc[0]) if descripciones.notna().any() else ""
    if "PRECIP" in texto or "LLUVIA" in texto:
        return "lluvia", None
    if "NIVEL" in texto:
        if "MAXIM" in texto or "MINIM" in texto:
            return "nivel", "Es nivel máximo o mínimo por hora. El modelo se entrenó con el nivel instantáneo, y mezclarlos sesgaría los semáforos. Descargue «Nivel instantáneo»."
        return "nivel", None
    return "desconocido", None


def _leer_crudo(nombre: str, flujo, codificacion: str, separador: str, columnas: list[str], codigos_nivel: set, codigos_lluvia: set):
    primera = next(_partes(flujo, codificacion, separador, columnas, nrows=5000))
    primera.columns = [_normal(c) for c in primera.columns]
    if "descripcionsensor" not in primera.columns:
        return _informe(nombre, hallazgos=[_hallazgo("error", "No se pudo saber si el archivo es de nivel o de lluvia: falta la columna DescripcionSensor.")]), None
    tipo, motivo = _tipo_de_sensor(primera["descripcionsensor"])
    if tipo == "desconocido":
        return _informe(nombre, hallazgos=[_hallazgo("error", "El archivo no parece ser de nivel ni de lluvia: DescripcionSensor no lo dice.")]), None
    if motivo:
        return _informe(nombre, tipo, [_hallazgo("error", motivo)]), None
    esperadas = codigos_nivel if tipo == "nivel" else codigos_lluvia
    filas = ilegibles_fecha = ilegibles_valor = 0
    utiles = []
    for parte in _partes(flujo, codificacion, separador, columnas):
        parte.columns = [_normal(c) for c in parte.columns]
        filas += len(parte)
        codigo = parte["codigoestacion"].astype(str).str.strip().str.zfill(10)
        parte = parte.loc[codigo.isin(esperadas)].assign(codigoestacion=codigo)
        if parte.empty:
            continue
        parte["fechaobservacion"] = leer_fechas(parte["fechaobservacion"])
        parte["valorobservado"] = pd.to_numeric(parte["valorobservado"].str.replace(",", ".", regex=False), errors="coerce")
        ilegibles_fecha += int(parte["fechaobservacion"].isna().sum())
        ilegibles_valor += int(parte["valorobservado"].isna().sum())
        parte = parte.dropna(subset=["fechaobservacion", "valorobservado"])
        parte["codigosensor"] = parte["codigosensor"] if "codigosensor" in parte else "0"
        utiles.append(parte[["codigoestacion", "codigosensor", "fechaobservacion", "valorobservado"]])
    datos = pd.concat(utiles, ignore_index=True) if utiles else pd.DataFrame(columns=["codigoestacion", "codigosensor", "fechaobservacion", "valorobservado"])
    hallazgos = []
    del_modelo = len(datos) + ilegibles_fecha + ilegibles_valor
    if datos.empty:
        hallazgos.append(_hallazgo("error", f"No trae lecturas de ninguna {'estación' if tipo == 'nivel' else 'estación de lluvia'} que use el modelo "
                                            f"({'las 4 estaciones de nivel' if tipo == 'nivel' else 'los pluviómetros cercanos a ellas'})."))
    else:
        for cantidad, que in ((ilegibles_fecha, "fecha"), (ilegibles_valor, "valor")):
            if cantidad:
                grave = cantidad / del_modelo > MAX_ILEGIBLES
                hallazgos.append(_hallazgo("error" if grave else "advertencia", f"{cantidad:,} lecturas tienen {que} ilegible y se ignoran.".replace(",", ".")
                                           + (" Son demasiadas: revise el archivo." if grave else "")))
        faltan = sorted(esperadas - set(datos["codigoestacion"]))
        if faltan:
            hallazgos.append(_hallazgo("advertencia", f"No trae lecturas de {len(faltan)} de los {len(esperadas)} {'puntos de medición de nivel' if tipo == 'nivel' else 'pluviómetros'} que usa el modelo ({', '.join(faltan)})."))
    return _informe(nombre, tipo, hallazgos, filas=filas, filas_utiles=len(datos), estaciones=sorted(set(datos["codigoestacion"])),
                    desde=datos["fechaobservacion"].min() if len(datos) else None, hasta=datos["fechaobservacion"].max() if len(datos) else None), datos


# ---------------------------------------------------------------- archivo ya combinado
def _leer_combinado(nombre: str, tabla: pd.DataFrame, metadatos: dict):
    diagnostico = validar(tabla, metadatos, exigir_historia=False)
    hallazgos = [_hallazgo(h["severidad"], h["mensaje"]) for h in diagnostico["hallazgos"][:10]]
    resumen = diagnostico["resumen"]
    informe = _informe(nombre, "combinado", hallazgos, filas=resumen["filas"], estaciones=resumen["estaciones"], desde=resumen["desde"], hasta=resumen["hasta"])
    if informe["estado"] == "error":
        return informe, None
    datos = normalizar_tabla(tabla).dropna(subset=["fecha_hora"])
    return {**informe, "filas_utiles": len(datos)}, datos


# ---------------------------------------------------------------- punto de entrada
def leer(nombre: str, flujo, codigos_nivel: set, codigos_lluvia: set, metadatos: dict, max_mb: int = 400):
    """Reconoce y revisa un archivo. Devuelve (informe, datos); datos es None si el archivo no sirve."""
    flujo.seek(0, 2)
    tamano = flujo.tell()
    flujo.seek(0)
    if not tamano:
        return _informe(nombre, hallazgos=[_hallazgo("error", "El archivo está vacío.")]), None
    if tamano > max_mb * 1024 * 1024:
        return _informe(nombre, hallazgos=[_hallazgo("error", f"El archivo pesa más de {max_mb} MB, que es el máximo permitido.")]), None
    extension = nombre[nombre.rfind("."):].lower() if "." in nombre else ""
    if extension not in EXTENSIONES:
        return _informe(nombre, hallazgos=[_hallazgo("error", f"El formato «{extension or 'desconocido'}» no se acepta. Suba archivos .csv o .xlsx.")]), None
    try:
        if extension == ".xlsx":
            flujo.seek(0)
            tabla = pd.read_excel(io.BytesIO(flujo.read()), dtype=str)
            tabla.columns = [_normal(c) for c in tabla.columns]
            if not all(c in tabla.columns for c in CLAVE_COMBINADA):
                return _informe(nombre, hallazgos=[_hallazgo("error", "Los archivos Excel solo se aceptan ya combinados, con las columnas estacion, fecha_hora, nivel_m y lluvia_mm.")]), None
            return _leer_combinado(nombre, tabla.dropna(how="all").reset_index(drop=True), metadatos)
        codificacion, separador, columnas = _abrir(flujo)
        if all(c in columnas for c in CLAVE_COMBINADA) or ("estacion" in columnas and "fecha_hora" in columnas):      # si le falta una columna, validar() dice cuál
            flujo.seek(0)
            tabla = pd.read_csv(flujo, sep=separador, encoding=codificacion, dtype=str)
            tabla.columns = [_normal(c) for c in tabla.columns]
            return _leer_combinado(nombre, tabla.dropna(how="all").reset_index(drop=True), metadatos)
        if all(c in columnas for c in CLAVE_CRUDA):
            for intento in dict.fromkeys([codificacion, "latin-1"]):          # si en el medio aparece un carácter que no es UTF-8, se prueba Latin-1
                try:
                    return _leer_crudo(nombre, flujo, intento, separador, [c for c in COLUMNAS_CRUDAS if c in columnas], codigos_nivel, codigos_lluvia)
                except UnicodeDecodeError:
                    continue
            raise ValueError("codificación")
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError):
        return _informe(nombre, hallazgos=[_hallazgo("error", "No se pudo leer el archivo: no parece un CSV o un Excel válido, o está dañado.")]), None
    return _informe(nombre, hallazgos=[_hallazgo("error", "No se reconoce el formato. Se esperaban las columnas de datos.gov.co (CodigoEstacion, FechaObservacion, "
                                                          "ValorObservado, DescripcionSensor) o las del archivo combinado (estacion, fecha_hora, nivel_m, lluvia_mm). "
                                                          f"Columnas encontradas: {', '.join(columnas[:8])}.")]), None

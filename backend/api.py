"""API del semáforo de riesgo de inundaciones (FastAPI).

Dos archivos: este (rutas, acceso de administrador y errores) y publicacion.py (las lecturas publicadas).
Recibe los archivos, llama a los módulos y devuelve los resultados:
    validacion.py  comprueba que el archivo sirva para el modelo
    eda.py         análisis exploratorio de cualquier archivo
    pipeline.py    predicciones con el modelo activo

Público: ver el semáforo de cada estación.   Administrador (clave): subir, revisar, analizar y publicar archivos.
Iniciar:   uvicorn api:app --reload        Documentación y pruebas:  http://127.0.0.1:8000/docs
"""
import io
import math
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, File, Header, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as ErrorHTTP

from eda import analizar
from pipeline import PipelineSemaforo, normalizar_tabla
from publicacion import Publicacion
from validacion import validar

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_MODELOS = Path(os.environ.get("SEMAFORO_MODELOS", RAIZ / "modelos"))
CARPETA_PUBLICADO = Path(os.environ.get("SEMAFORO_PUBLICADO", RAIZ / "datos" / "publicado"))
DATOS_INICIALES = RAIZ / "datos" / "datos_horarios.csv"
CLAVE_ADMIN = os.environ.get("SEMAFORO_CLAVE_ADMIN", "admin")      # cámbiela antes de usar el sistema de verdad
TAM_MAX_MB = int(os.environ.get("SEMAFORO_TAM_MAX_MB", "60"))
EXTENSIONES = (".csv", ".xlsx")


# ------------------------------------------------------------------ errores y utilidades
class ErrorDeUsuario(Exception):
    """Un problema que la persona puede corregir. Lleva un mensaje claro, nunca una traza técnica."""

    def __init__(self, mensaje: str, estado: int = 400, codigo: str = "SOLICITUD_INVALIDA", detalle: dict | None = None):
        super().__init__(mensaje)
        self.mensaje, self.estado, self.codigo, self.detalle = mensaje, estado, codigo, detalle or {}


def a_json_seguro(objeto):
    """Convierte tipos de numpy y pandas (y NaN) en tipos que JSON acepta."""
    if objeto is None or objeto is pd.NaT:
        return None
    if isinstance(objeto, (bool, np.bool_)):
        return bool(objeto)
    if isinstance(objeto, str):
        return objeto
    if isinstance(objeto, (int, np.integer)):
        return int(objeto)
    if isinstance(objeto, (float, np.floating)):
        return None if math.isnan(objeto) or math.isinf(objeto) else float(objeto)
    if isinstance(objeto, pd.Timestamp):
        return objeto.isoformat(sep=" ")
    if isinstance(objeto, dict):
        return {str(k): a_json_seguro(v) for k, v in objeto.items()}
    if isinstance(objeto, (list, tuple, set, np.ndarray, pd.Series)):
        return [a_json_seguro(v) for v in list(objeto)]
    if isinstance(objeto, pd.DataFrame):
        return [a_json_seguro(f) for f in objeto.to_dict(orient="records")]
    return str(objeto)


def _leer_csv(contenido: bytes) -> pd.DataFrame:
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            texto = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    encabezado = texto.splitlines()[0] if texto else ""
    return pd.read_csv(io.StringIO(texto), sep=max([",", ";", "\t"], key=encabezado.count), dtype=str)


def leer_archivo(archivo: UploadFile) -> tuple[pd.DataFrame, str]:
    """Lee un CSV o un Excel como texto, sin corregir nada: validar los datos es trabajo de validacion.py."""
    nombre = archivo.filename or ""
    extension = Path(nombre).suffix.lower()
    if extension not in EXTENSIONES:
        raise ErrorDeUsuario(f"El formato '{extension or 'desconocido'}' no está soportado. Cargue un archivo .csv o .xlsx.",
                             codigo="FORMATO_NO_SOPORTADO")
    contenido = archivo.file.read()
    if not contenido:
        raise ErrorDeUsuario("El archivo está vacío.", codigo="ARCHIVO_VACIO")
    if len(contenido) > TAM_MAX_MB * 1024 * 1024:
        raise ErrorDeUsuario(f"El archivo pesa más de {TAM_MAX_MB} MB, que es el máximo permitido.", 413, "ARCHIVO_DEMASIADO_GRANDE")
    try:
        tabla = _leer_csv(contenido) if extension == ".csv" else pd.read_excel(io.BytesIO(contenido), dtype=str)
    except Exception:
        raise ErrorDeUsuario("No se pudo leer el archivo: no parece un CSV o un Excel válido, o está dañado.", codigo="ARCHIVO_ILEGIBLE")
    tabla.columns = [str(c).strip().lstrip("﻿") for c in tabla.columns]
    tabla = tabla.dropna(how="all").reset_index(drop=True)
    if tabla.empty:
        raise ErrorDeUsuario("El archivo no tiene filas de datos.", codigo="ARCHIVO_VACIO")
    return tabla, nombre


@lru_cache(maxsize=4)
def _cargar(version: str) -> PipelineSemaforo:
    return PipelineSemaforo.cargar(CARPETA_MODELOS / version)


def pipeline_activo() -> PipelineSemaforo:
    archivo = CARPETA_MODELOS / "activo.txt"
    version = archivo.read_text(encoding="utf-8").strip() if archivo.exists() else "v3"
    try:
        return _cargar(version)
    except FileNotFoundError as error:
        raise ErrorDeUsuario(str(error), 404, "MODELO_NO_DISPONIBLE")


@lru_cache(maxsize=1)
def publicacion() -> Publicacion:
    return Publicacion(CARPETA_PUBLICADO, DATOS_INICIALES)


def exigir_administrador(x_clave_admin: str | None = Header(None, description="Clave del administrador.")) -> None:
    if not x_clave_admin or not secrets.compare_digest(x_clave_admin.encode(), CLAVE_ADMIN.encode()):
        raise ErrorDeUsuario("La clave de administrador no es correcta.", 401, "CLAVE_INCORRECTA")


# ------------------------------------------------------------------ aplicación y manejo de errores
app = FastAPI(
    title="API del semáforo de riesgo de inundaciones",
    description=("Anticipa si un río del Chocó llegará a nivel rojo en las próximas 24 horas. "
                 "El público consulta el semáforo de cada estación; el administrador, con su clave, sube y publica los archivos de datos."),
    version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in os.environ.get("SEMAFORO_ORIGENES", "*").split(",")],
                   allow_methods=["*"], allow_headers=["*"])


def _error(estado: int, codigo: str, mensaje: str, detalle: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=estado, content=a_json_seguro({"error": {"codigo": codigo, "mensaje": mensaje, "detalle": detalle or {}}}))


@app.exception_handler(ErrorDeUsuario)
async def _error_de_usuario(_, error: ErrorDeUsuario):
    return _error(error.estado, error.codigo, error.mensaje, error.detalle)


@app.exception_handler(RequestValidationError)
async def _solicitud_invalida(_, error: RequestValidationError):
    faltan = [str(e["loc"][-1]) for e in error.errors() if e["type"] == "missing"]
    mensaje = f"Falta el campo obligatorio: {', '.join(faltan)}." if faltan else "La solicitud no tiene el formato esperado."
    return _error(422, "SOLICITUD_INVALIDA", mensaje)


@app.exception_handler(ErrorHTTP)
async def _error_http(_, error: ErrorHTTP):
    return _error(error.status_code, "SOLICITUD_NO_VALIDA",
                  {404: "No se encontró lo que busca.", 405: "Esa operación no está permitida en esa dirección."}.get(error.status_code, str(error.detail)))


@app.exception_handler(Exception)
async def _error_inesperado(_, error: Exception):
    return _error(500, "ERROR_INESPERADO", "Ocurrió un error inesperado. Intente de nuevo y, si se repite, avise a quien administra el sistema.")


# ------------------------------------------------------------------ rutas públicas
@app.get("/salud", tags=["Público"], summary="Comprobar que la API funciona")
def salud():
    return {"estado": "ok"}


@app.get("/estaciones", tags=["Público"], summary="Semáforo más reciente de cada estación")
def estaciones():
    return a_json_seguro(publicacion().estaciones(pipeline_activo()))


@app.get("/estaciones/{codigo}", tags=["Público"], summary="Detalle de una estación: semáforo, probabilidades y últimos 7 días")
def estacion(codigo: str):
    try:
        return a_json_seguro(publicacion().detalle(pipeline_activo(), codigo))
    except KeyError:
        raise ErrorDeUsuario("No existe esa estación.", 404, "ESTACION_NO_ENCONTRADA")


@app.get("/modelo/activo", tags=["Público"], summary="Metadatos y versión del modelo activo")
def modelo_activo():
    return a_json_seguro(pipeline_activo().resumen_modelo())


# ------------------------------------------------------------------ rutas del administrador (requieren clave)
admin = APIRouter(prefix="/admin", tags=["Administrador"], dependencies=[Depends(exigir_administrador)])


@admin.get("/verificar", summary="Comprobar la clave de administrador")
def verificar():
    return {"estado": "ok"}


@admin.post("/validar", summary="Comprobar si un archivo es compatible con el modelo")
def validar_archivo(archivo: UploadFile = File(..., description="CSV o Excel con las columnas del esquema del modelo.")):
    tabla, nombre = leer_archivo(archivo)
    pipeline = pipeline_activo()
    return a_json_seguro({"archivo": nombre, "modelo_version": pipeline.version, "validacion": validar(tabla, pipeline.metadatos)})


@admin.post("/analizar", summary="Análisis exploratorio automático (sirve para cualquier archivo)")
def analizar_archivo(archivo: UploadFile = File(..., description="Cualquier CSV o Excel."),
                     incluir_figuras: bool = Query(False, description="Incluir las gráficas como imágenes PNG en base64.")):
    tabla, nombre = leer_archivo(archivo)
    resultado, figuras = analizar(tabla, nombre)
    return a_json_seguro({"archivo": nombre, "resultado": resultado,
                          "figuras": figuras if incluir_figuras else [{"id": f["id"], "titulo": f["titulo"]} for f in figuras]})


@admin.post("/predecir", summary="Predecir con un archivo, sin publicarlo (solo archivos compatibles)",
            responses={422: {"description": "El archivo no es compatible con el modelo; el cuerpo trae el diagnóstico."}})
def predecir(archivo: UploadFile = File(..., description="CSV o Excel con las columnas del esquema del modelo."),
             solo_ultima_hora: bool = Query(False, description="Predecir solo la última hora con nivel de cada estación."),
             formato: Literal["json", "csv"] = Query("json", description="json: resumen y primeras predicciones. csv: todas, para descargar."),
             limite: int = Query(200, ge=1, le=5000, description="Cuántas predicciones incluir en la respuesta json.")):
    tabla, nombre = leer_archivo(archivo)
    pipeline = pipeline_activo()
    diagnostico = validar(tabla, pipeline.metadatos)
    if not diagnostico["puede_predecir"]:
        raise ErrorDeUsuario("El archivo no es compatible con el modelo, por eso no se generaron predicciones. Revise el diagnóstico, "
                             "corrija el archivo y cárguelo de nuevo.", 422, "ARCHIVO_INCOMPATIBLE", {"diagnostico": diagnostico})
    predicciones = pipeline.predecir(tabla, solo_ultima_hora)
    salida = pipeline.tabla_de_salida(predicciones)
    if formato == "csv":
        return Response(content=salida.to_csv(index=False).encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="predicciones_{Path(nombre).stem}.csv"'})
    return a_json_seguro({"archivo": nombre, "modelo_version": pipeline.version, "validacion": diagnostico,
                          "resumen": {"filas_archivo": len(tabla), **pipeline.resumen(predicciones, 48)},
                          "total_predicciones": len(salida), "predicciones": salida.head(limite)})


@admin.post("/publicar", summary="Agregar las lecturas del archivo y actualizar los semáforos que ve el público",
            responses={422: {"description": "El archivo no es compatible con el modelo; no se publicó nada."}})
def publicar(archivo: UploadFile = File(..., description="CSV o Excel con lecturas nuevas (pueden ser pocas horas).")):
    tabla, nombre = leer_archivo(archivo)
    pipeline = pipeline_activo()
    diagnostico = validar(tabla, pipeline.metadatos, exigir_historia=False)     # las lecturas nuevas se suman a las ya publicadas
    if diagnostico["resumen"]["errores"]:
        raise ErrorDeUsuario("El archivo tiene errores, por eso no se publicó nada. Revise el diagnóstico, corríjalo y cárguelo de nuevo.",
                             422, "ARCHIVO_INCOMPATIBLE", {"diagnostico": diagnostico})
    cambios = publicacion().integrar(normalizar_tabla(tabla), pipeline.codigos)
    return a_json_seguro({"archivo": nombre, "validacion": diagnostico, "cambios": cambios, **publicacion().estaciones(pipeline)})


app.include_router(admin)

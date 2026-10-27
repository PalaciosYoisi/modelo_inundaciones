"""API del semáforo de riesgo de inundaciones (FastAPI).

Dos archivos: este (rutas, acceso de administrador y errores) y publicacion.py (las lecturas publicadas).
Recibe los archivos, llama a los módulos y devuelve los resultados:
    validacion.py  comprueba que el archivo sirva para el modelo
    eda.py         análisis exploratorio de cualquier archivo
    pipeline.py    predicciones con el modelo activo

Público: ver el semáforo de cada estación.   Administradores (usuario y clave): subir, revisar, analizar y publicar archivos.
Cada acción de un administrador queda en la auditoría (auditoria.py); usuarios.py maneja el ingreso. Ambos usan la base MySQL (base_datos.py).
Iniciar:   uvicorn api:app --reload        Documentación y pruebas:  http://127.0.0.1:8000/docs
"""
import io
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, File, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as ErrorHTTP

import auditoria
import consultar_diario
import entrada
import ingesta
import lotes
import recomendacion
import reportes
import usuarios
from base_datos import ErrorBaseDatos
from ingesta import CARPETA_MODELOS, DATOS_INICIALES, PUBLICADO_ANTERIOR
from eda import analizar
from pipeline import PipelineSemaforo, normalizar_tabla, version_activa
from publicacion import Publicacion
from validacion import validar

TAM_MAX_MB = int(os.environ.get("SEMAFORO_TAM_MAX_MB", "60"))
TAM_MAX_CRUDO_MB = int(os.environ.get("SEMAFORO_TAM_MAX_CRUDO_MB", "400"))     # los archivos de datos.gov.co pueden ser muy grandes
MAX_ARCHIVOS = 12
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
    try:
        return _cargar(version_activa(CARPETA_MODELOS))
    except FileNotFoundError as error:
        raise ErrorDeUsuario(str(error), 404, "MODELO_NO_DISPONIBLE")


@lru_cache(maxsize=1)
def publicacion() -> Publicacion:
    return Publicacion(DATOS_INICIALES, PUBLICADO_ANTERIOR)


def exigir_sesion(authorization: str | None = Header(None, description="Sesión del administrador: «Bearer <token>».")) -> dict:
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    sesion = usuarios.validar_sesion(token) if token else None
    if sesion is None:
        raise ErrorDeUsuario("Su sesión no es válida o ya venció. Ingrese de nuevo.", 401, "SESION_INVALIDA")
    return {**sesion, "token": token}


def exigir_administrador(sesion: dict = Depends(exigir_sesion)) -> dict:
    if sesion["debe_cambiar_clave"]:
        raise ErrorDeUsuario("Antes de continuar debe cambiar la clave inicial.", 403, "CAMBIO_DE_CLAVE_PENDIENTE")
    return sesion


def auditar(request: Request, quien: str, accion: str, detalle: dict | None = None, estricto: bool = False) -> None:
    """Anota la acción. Si es de las que cambian datos (estricto) y no se puede anotar, la acción no se realiza."""
    try:
        auditoria.registrar(quien, accion, detalle, request.client.host if request.client else None)
    except ErrorBaseDatos:
        if estricto:
            raise ErrorDeUsuario("No se pudo guardar el registro de auditoría, por eso no se realizó la acción. Intente de nuevo.", 503, "AUDITORIA_NO_DISPONIBLE")


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


@app.exception_handler(ErrorBaseDatos)
async def _base_no_disponible(_, error: ErrorBaseDatos):
    return _error(503, "BASE_DE_DATOS_NO_DISPONIBLE", str(error))


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


# ------------------------------------------------------------------ ingreso y cuenta del administrador
class Credenciales(BaseModel):
    usuario: str
    clave: str


class CambioDeClave(BaseModel):
    actual: str
    nueva: str


@app.post("/sesion", tags=["Administrador"], summary="Ingresar con usuario y clave")
def ingresar(datos: Credenciales, request: Request):
    try:
        sesion = usuarios.iniciar_sesion(datos.usuario, datos.clave)
    except usuarios.ErrorDeAcceso as error:
        auditar(request, datos.usuario.strip().lower() or "?", "ingreso_fallido", {"motivo": error.codigo})
        raise ErrorDeUsuario(error.mensaje, error.estado, error.codigo)
    auditar(request, sesion["usuario"], "ingreso")
    return sesion


class Rechazo(BaseModel):
    motivo: str = ""


cuenta = APIRouter(prefix="/cuenta", tags=["Administrador"], dependencies=[Depends(exigir_sesion)])


@cuenta.get("/yo", summary="Datos de la sesión actual")
def quien_soy(sesion: dict = Depends(exigir_sesion)):
    return {"usuario": sesion["usuario"], "nombre": sesion["nombre"], "debe_cambiar_clave": sesion["debe_cambiar_clave"]}


@cuenta.post("/salir", summary="Cerrar la sesión")
def salir(request: Request, sesion: dict = Depends(exigir_sesion)):
    usuarios.cerrar_sesion(sesion["token"])
    auditar(request, sesion["usuario"], "salida")
    return {"estado": "ok"}


@cuenta.post("/clave", summary="Cambiar la clave propia")
def cambiar_clave(datos: CambioDeClave, request: Request, sesion: dict = Depends(exigir_sesion)):
    try:
        usuarios.cambiar_clave(sesion["usuario"], datos.actual, datos.nueva)
    except usuarios.ErrorDeAcceso as error:
        raise ErrorDeUsuario(error.mensaje, error.estado, error.codigo)
    auditar(request, sesion["usuario"], "cambio_de_clave")
    return {"estado": "ok"}


# ------------------------------------------------------------------ descargas de datos combinados
def _archivo_de_datos(estacion: str | None, dias: int | None) -> tuple[Response, int]:
    pipeline = pipeline_activo()
    if estacion and estacion not in pipeline.codigos:
        raise ErrorDeUsuario("No existe esa estación.", 404, "ESTACION_NO_ENCONTRADA")
    estaciones = pipeline.metadatos["estaciones"]
    tabla = reportes.datos_combinados(publicacion().predicciones(pipeline), estaciones, estacion, dias)
    nombre = reportes.nombre_de_archivo(estaciones, estacion, dias)
    return Response(content=reportes.a_csv(tabla), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'}), len(tabla)


@app.get("/descargas/datos.csv", tags=["Público"], summary="Datos combinados (nivel, lluvia y semáforo por hora) en CSV")
def descargar_datos(estacion: str | None = Query(None, description="Código de una estación; sin él, todas."),
                    dias: int | None = Query(None, ge=1, le=120, description="Solo los últimos días; sin él, los 120 que se conservan.")):
    return _archivo_de_datos(estacion, dias)[0]


# ------------------------------------------------------------------ rutas del administrador (requieren sesión)
admin = APIRouter(prefix="/admin", tags=["Administrador"], dependencies=[Depends(exigir_administrador)])


@admin.post("/validar", summary="Comprobar si un archivo es compatible con el modelo")
def validar_archivo(request: Request, archivo: UploadFile = File(..., description="CSV o Excel con las columnas del esquema del modelo."),
                    sesion: dict = Depends(exigir_administrador)):
    tabla, nombre = leer_archivo(archivo)
    pipeline = pipeline_activo()
    diagnostico = validar(tabla, pipeline.metadatos)
    auditar(request, sesion["usuario"], "validar", {"archivo": nombre, "filas": len(tabla), "estado": diagnostico["estado"]})
    return a_json_seguro({"archivo": nombre, "modelo_version": pipeline.version, "validacion": diagnostico})


@admin.post("/analizar", summary="Análisis exploratorio automático (sirve para cualquier archivo)")
def analizar_archivo(request: Request, archivo: UploadFile = File(..., description="Cualquier CSV o Excel."),
                     incluir_figuras: bool = Query(False, description="Incluir las gráficas como imágenes PNG en base64."),
                     sesion: dict = Depends(exigir_administrador)):
    tabla, nombre = leer_archivo(archivo)
    resultado, figuras = analizar(tabla, nombre)
    auditar(request, sesion["usuario"], "analizar", {"archivo": nombre, "filas": len(tabla)})
    return a_json_seguro({"archivo": nombre, "resultado": resultado,
                          "figuras": figuras if incluir_figuras else [{"id": f["id"], "titulo": f["titulo"]} for f in figuras]})


@admin.post("/predecir", summary="Predecir con un archivo, sin publicarlo (solo archivos compatibles)",
            responses={422: {"description": "El archivo no es compatible con el modelo; el cuerpo trae el diagnóstico."}})
def predecir(request: Request, archivo: UploadFile = File(..., description="CSV o Excel con las columnas del esquema del modelo."),
             solo_ultima_hora: bool = Query(False, description="Predecir solo la última hora con nivel de cada estación."),
             formato: Literal["json", "csv"] = Query("json", description="json: resumen y primeras predicciones. csv: todas, para descargar."),
             limite: int = Query(200, ge=1, le=5000, description="Cuántas predicciones incluir en la respuesta json."),
             sesion: dict = Depends(exigir_administrador)):
    tabla, nombre = leer_archivo(archivo)
    pipeline = pipeline_activo()
    diagnostico = validar(tabla, pipeline.metadatos)
    if not diagnostico["puede_predecir"]:
        raise ErrorDeUsuario("El archivo no es compatible con el modelo, por eso no se generaron predicciones. Revise el diagnóstico, "
                             "corrija el archivo y cárguelo de nuevo.", 422, "ARCHIVO_INCOMPATIBLE", {"diagnostico": diagnostico})
    predicciones = pipeline.predecir(tabla, solo_ultima_hora)
    salida = pipeline.tabla_de_salida(predicciones)
    auditar(request, sesion["usuario"], "descargar_predicciones" if formato == "csv" else "predecir", {"archivo": nombre, "filas": len(salida)})
    if formato == "csv":
        return Response(content=salida.to_csv(index=False).encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="predicciones_{Path(nombre).stem}.csv"'})
    return a_json_seguro({"archivo": nombre, "modelo_version": pipeline.version, "validacion": diagnostico,
                          "resumen": {"filas_archivo": len(tabla), **pipeline.resumen(predicciones, 48)},
                          "total_predicciones": len(salida), "predicciones": salida.head(limite)})


@admin.post("/archivos", summary="Subir uno o varios archivos de datos: se reconoce y revisa cada uno y se unifican en un lote pendiente",
            responses={200: {"description": "Los informes por archivo y, si todo sirve y se pidió, el lote creado."}})
def subir_archivos(request: Request, archivos: list[UploadFile] = File(..., description="CSV de nivel y de lluvia de datos.gov.co, o un archivo ya combinado."),
                   crear: bool = Query(True, description="False: solo revisar los archivos, sin crear el lote."),
                   sesion: dict = Depends(exigir_administrador)):
    if not archivos or len(archivos) > MAX_ARCHIVOS:
        raise ErrorDeUsuario(f"Suba entre 1 y {MAX_ARCHIVOS} archivos a la vez.", 400, "CANTIDAD_DE_ARCHIVOS")
    pipeline = pipeline_activo()
    try:
        ctx = ingesta.contexto(pipeline, publicacion())
    except ingesta.ErrorDeIngesta as error:
        raise ErrorDeUsuario(str(error), 503, "SIN_MAPA_DE_PLUVIOMETROS")
    informes, partes = [], {"nivel": [], "lluvia": [], "combinado": []}
    for archivo in archivos:
        informe, datos = entrada.leer(archivo.filename or "archivo", archivo.file, set(ctx.codigos), set(ctx.pluviometros), pipeline.metadatos, TAM_MAX_CRUDO_MB)
        informes.append(informe)
        if datos is not None:
            partes[informe["tipo"]].append(datos)
    nombres = [i["archivo"] for i in informes]
    con_errores = [i for i in informes if i["estado"] == "error"]
    if con_errores or not crear:
        auditar(request, sesion["usuario"], "revisar_archivos", {"archivos": nombres, "con_errores": len(con_errores)})
        mensaje = ("No se creó el lote: quite o corrija los archivos con errores." if con_errores else
                   "Los archivos sirven. Pulse «Crear lote» para unificarlos y dejarlos en revisión.")
        return a_json_seguro({"informes": informes, "lote": None, "mensaje": mensaje})
    auditar(request, sesion["usuario"], "subir_archivos", {"archivos": nombres}, estricto=True)
    unir = lambda lista, columnas, llaves: (pd.concat(lista, ignore_index=True).drop_duplicates(llaves, keep="last") if lista
                                            else pd.DataFrame(columns=columnas))
    combinado = unir(partes["combinado"], ["estacion", "fecha_hora", "nivel_m", "lluvia_mm"], ["estacion", "fecha_hora"])
    lote = ingesta.crear_lote("archivo", unir(partes["nivel"], ingesta.COLUMNAS_CRUDAS, ingesta.COLUMNAS_CRUDAS[:3]),
                              unir(partes["lluvia"], ingesta.COLUMNAS_CRUDAS, ingesta.COLUMNAS_CRUDAS[:3]), combinado if len(combinado) else None, ctx,
                              extra={"archivos": [{k: i[k] for k in ("archivo", "tipo", "tipo_texto", "estado", "filas", "filas_utiles", "desde", "hasta")} for i in informes]})
    auditar(request, sesion["usuario"], "subir_archivos_resultado", {"resultado": lote["estado"], "lote": lote.get("lote")})
    return a_json_seguro({"informes": informes, "lote": lote, "mensaje": lote["mensaje"]})

@admin.get("/administradores", summary="Administradores y su estado (para desbloquear a quien se haya bloqueado)")
def administradores():
    return a_json_seguro(usuarios.estado_de_todos())


@admin.post("/administradores/{usuario}/desbloquear", summary="Desbloquear a otro administrador")
def desbloquear(usuario: str, request: Request, sesion: dict = Depends(exigir_administrador)):
    auditar(request, sesion["usuario"], "desbloquear", {"usuario": usuario}, estricto=True)
    try:
        usuarios.desbloquear(usuario)
    except usuarios.ErrorDeAcceso as error:
        raise ErrorDeUsuario(error.mensaje, error.estado, error.codigo)
    return {"estado": "ok"}


# ------------------------------------------------------------------ consulta diaria: lotes de datos nuevos
def _lote(id_lote: int) -> dict:
    ficha = lotes.obtener(id_lote)
    if ficha is None:
        raise ErrorDeUsuario("No existe ese lote.", 404, "LOTE_NO_ENCONTRADO")
    return ficha


def _datos_del_lote(id_lote: int) -> pd.DataFrame:
    try:
        return lotes.datos(id_lote)
    except FileNotFoundError:
        raise ErrorDeUsuario("Los datos de este lote ya no están disponibles.", 404, "LOTE_SIN_DATOS")


@admin.get("/descargas/datos.csv", summary="Datos combinados en CSV (queda en la auditoría)")
def descargar_datos_admin(request: Request, estacion: str | None = Query(None), dias: int | None = Query(None, ge=1, le=120),
                          sesion: dict = Depends(exigir_administrador)):
    respuesta, filas = _archivo_de_datos(estacion, dias)
    auditar(request, sesion["usuario"], "descargar_datos", {"estacion": estacion or "todas", "dias": dias or "todos", "filas": filas})
    return respuesta


@admin.get("/lotes", summary="Lotes de datos nuevos y cuántos esperan revisión")
def lista_de_lotes():
    return a_json_seguro({"pendientes": lotes.pendientes(), "lotes": lotes.listar()})


@admin.get("/lotes/{id_lote}", summary="Un lote: resumen, advertencias y datos diarios para revisarlo")
def detalle_del_lote(id_lote: int):
    ficha = _lote(id_lote)
    try:                                  # la recomendación es una ayuda: si falla, el lote igual se puede revisar
        nombres = {c: e["nombre"] for c, e in pipeline_activo().metadatos["estaciones"].items()}
        aviso = recomendacion.recomendar(ficha, nombres)
    except Exception:
        aviso = None
    return a_json_seguro({**ficha, "diario": lotes.diario(_datos_del_lote(id_lote)), "recomendacion": aviso})


@admin.get("/lotes/{id_lote}/descargar", summary="Descargar las lecturas del lote (CSV)")
def descargar_lote(id_lote: int, request: Request, sesion: dict = Depends(exigir_administrador)):
    _lote(id_lote)
    tabla = _datos_del_lote(id_lote)
    auditar(request, sesion["usuario"], "descargar_lote", {"lote": id_lote, "filas": len(tabla)})
    return Response(content=tabla.to_csv(index=False).encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="lote_{id_lote}.csv"'})


@admin.post("/lotes/{id_lote}/confirmar", summary="Confirmar un lote: sus lecturas pasan a lo publicado y al modelo")
def confirmar_lote(id_lote: int, request: Request, sesion: dict = Depends(exigir_administrador)):
    ficha = _lote(id_lote)
    if ficha["estado"] != "pendiente":
        raise ErrorDeUsuario("Este lote ya no está pendiente (se confirmó, se rechazó o llegó uno más nuevo). Actualice la lista.", 409, "LOTE_NO_PENDIENTE")
    tabla = _datos_del_lote(id_lote)
    auditar(request, sesion["usuario"], "confirmar_lote", {"lote": id_lote, "filas": len(tabla)}, estricto=True)
    if not lotes.decidir(id_lote, "confirmado", sesion["usuario"]):
        raise ErrorDeUsuario("Otra persona ya decidió sobre este lote.", 409, "LOTE_NO_PENDIENTE")
    pipeline = pipeline_activo()
    try:
        cambios = publicacion().integrar(normalizar_tabla(tabla), pipeline.codigos)
    except Exception:
        lotes.reabrir(id_lote)
        auditar(request, sesion["usuario"], "confirmar_lote_fallido", {"lote": id_lote})
        raise
    return a_json_seguro({"lote": id_lote, "cambios": cambios, **publicacion().estaciones(pipeline)})


@admin.post("/lotes/{id_lote}/rechazar", summary="Rechazar un lote: no llega al modelo")
def rechazar_lote(id_lote: int, request: Request, datos: Rechazo | None = None, sesion: dict = Depends(exigir_administrador)):
    _lote(id_lote)
    motivo = ((datos.motivo if datos else "") or "").strip()[:300]
    auditar(request, sesion["usuario"], "rechazar_lote", {"lote": id_lote, **({"motivo": motivo} if motivo else {})}, estricto=True)
    if not lotes.decidir(id_lote, "rechazado", sesion["usuario"]):
        raise ErrorDeUsuario("Este lote ya no está pendiente. Actualice la lista.", 409, "LOTE_NO_PENDIENTE")
    return {"estado": "ok"}


@admin.post("/consulta/ejecutar", summary="Consultar datos.gov.co ahora (lo mismo que hace el script diario)")
def consultar_ahora(request: Request, sesion: dict = Depends(exigir_administrador)):
    resultado = consultar_diario.ejecutar()
    auditar(request, sesion["usuario"], "consulta_manual", {"resultado": resultado["estado"], "lote": resultado.get("lote")})
    return a_json_seguro(resultado)


@admin.get("/actividad", summary="Registro de auditoría: quién hizo qué")
def actividad(usuario: str | None = Query(None), accion: str | None = Query(None), limite: int = Query(100, ge=1, le=500)):
    return a_json_seguro(auditoria.listar(usuario, accion, limite))


app.include_router(cuenta)
app.include_router(admin)
"""Versiones del modelo: cada una con su estado (activo, candidato o histórico), sus metadatos y el archivo del bosque, guardados en MySQL.

El disco de los servidores en la nube se borra al reiniciar, así que una versión entrenada desde la web solo puede vivir en la base.
El archivo del bosque se parte en trozos de 256 KB, que caben incluso en el límite de paquete por defecto de MySQL (1 MB).
La primera vez se importan las versiones que haya en la carpeta modelos/; si la base no responde, se sigue usando esa carpeta.
Cambiar de versión activa exige una justificación, que queda guardada junto con quién decidió y cuándo.
Ver las versiones:  python versiones.py
"""
import io
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

import joblib
import pymysql

from base_datos import ERROR_COLUMNA_REPETIDA, TABLAS_MODELOS, ErrorBaseDatos, conexion
from hora import hora_actual
from pipeline import PipelineSemaforo, cargar_metadatos, version_activa

TAM_TROZO = 256 * 1024
MIN_JUSTIFICACION = 10            # caracteres: una decisión sobre el modelo no se justifica con una palabra
_cerrojo = threading.RLock()
_tablas_listas = False            # las tablas ya se comprobaron en este proceso
_activa: str | None = None        # versión activa recordada, para no consultar la base en cada petición


class ErrorDeVersion(Exception):
    """No se puede hacer lo pedido con esa versión. El mensaje es apto para mostrarlo a una persona."""


def _preparar(cursor) -> None:
    """Crea las tablas la primera vez que se usan, para que en la nube no haya que correr nada a mano."""
    global _tablas_listas
    if not _tablas_listas:
        for sentencia in TABLAS_MODELOS:
            cursor.execute(sentencia)
        try:
            cursor.execute("ALTER TABLE modelos ADD COLUMN decision VARCHAR(10) NULL")       # bases creadas antes de guardar si la decisión fue activar o descartar
        except pymysql.err.OperationalError as error:
            if error.args[0] != ERROR_COLUMNA_REPETIDA:
                raise
        _tablas_listas = True


def _olvidar_activa() -> None:
    global _activa
    _activa = None


def _fecha(metadatos: dict) -> datetime:
    try:
        return datetime.fromisoformat(metadatos["fecha_entrenamiento"])
    except (KeyError, ValueError):
        return hora_actual()


def _insertar(cursor, estado: str, metadatos: dict, contenido: bytes) -> None:
    version = metadatos["version"]
    cursor.execute("INSERT INTO modelos (version, estado, creado, metadatos) VALUES (%s, %s, %s, %s)",
                   (version, estado, _fecha(metadatos), json.dumps(metadatos, ensure_ascii=False)))
    for parte, inicio in enumerate(range(0, len(contenido), TAM_TROZO)):
        cursor.execute("INSERT INTO modelo_trozos (version, parte, datos) VALUES (%s, %s, %s)", (version, parte, contenido[inicio:inicio + TAM_TROZO]))


# ------------------------------------------------------------------ guardar
def sembrar(carpeta_modelos: Path) -> None:
    """Si la base no tiene versiones (primer arranque), importa las de la carpeta modelos/: la que diga activo.txt queda activa."""
    with _cerrojo, conexion() as cursor:
        _preparar(cursor)
        cursor.execute("SELECT COUNT(*) AS n FROM modelos")
        if cursor.fetchone()["n"]:
            return
        activa = version_activa(carpeta_modelos)
        for carpeta in sorted(Path(carpeta_modelos).glob("v*")):
            try:
                metadatos = cargar_metadatos(carpeta)
                contenido = (carpeta / metadatos["archivo_modelo"]).read_bytes()
            except (FileNotFoundError, KeyError):
                continue
            estado = "activo" if metadatos["version"] == activa else "candidato" if metadatos.get("estado") == "candidato" else "historico"
            _insertar(cursor, estado, metadatos, contenido)


def guardar(resultado: dict) -> None:
    """Guarda como candidata la versión que devolvió reentrenamiento.entrenar_version. No cambia cuál es la activa."""
    version = resultado["version"]
    archivo = io.BytesIO()
    joblib.dump(resultado["modelo"], archivo, compress=3)
    with _cerrojo, conexion() as cursor:
        _preparar(cursor)
        cursor.execute("SELECT version FROM modelos WHERE version=%s", (version,))
        if cursor.fetchone():
            raise ErrorDeVersion(f"Ya existe la versión {version}.")
        _insertar(cursor, "candidato", {**resultado["metadatos"], "version": version}, archivo.getvalue())


# ------------------------------------------------------------------ leer
def cargar(version: str, carpeta_modelos: Path) -> PipelineSemaforo:
    """El pipeline de una versión, leído de la base (o de la carpeta modelos/ si la base no responde o no la tiene)."""
    try:
        with conexion() as cursor:
            _preparar(cursor)
            cursor.execute("SELECT estado, metadatos FROM modelos WHERE version=%s", (version,))
            ficha = cursor.fetchone()
            if ficha:
                cursor.execute("SELECT datos FROM modelo_trozos WHERE version=%s ORDER BY parte", (version,))
                trozos = [fila["datos"] for fila in cursor.fetchall()]
    except ErrorBaseDatos:
        ficha = None
    if ficha is None:
        return PipelineSemaforo.cargar(Path(carpeta_modelos) / version)          # sin la versión en ninguna parte, avisa que no hay modelo
    return PipelineSemaforo(joblib.load(io.BytesIO(b"".join(trozos))), {**json.loads(ficha["metadatos"]), "estado": ficha["estado"]})


def activa(carpeta_modelos: Path) -> str:
    """Nombre de la versión en uso. Si la base no responde, la que diga modelos/activo.txt."""
    global _activa
    with _cerrojo:
        if _activa is None:
            try:
                sembrar(carpeta_modelos)
                with conexion() as cursor:
                    cursor.execute("SELECT version FROM modelos WHERE estado='activo'")
                    fila = cursor.fetchone()
                _activa = fila["version"] if fila else None
            except ErrorBaseDatos:
                _activa = None
        return _activa or version_activa(carpeta_modelos)


def siguiente() -> str:
    """El nombre de la próxima versión: v4 si la mayor que existe es v3."""
    with conexion() as cursor:
        _preparar(cursor)
        cursor.execute("SELECT version FROM modelos")
        numeros = [int(fila["version"][1:]) for fila in cursor.fetchall() if fila["version"][1:].isdigit()]
    return f"v{max(numeros, default=0) + 1}"


def listar() -> list[dict]:
    """Todas las versiones, de la más nueva a la más vieja, con lo que hace falta para compararlas y ver la decisión tomada."""
    with conexion() as cursor:
        _preparar(cursor)
        cursor.execute("SELECT version, estado, creado, metadatos, decidido_por, decidido_en, justificacion, decision FROM modelos ORDER BY creado DESC, version DESC")
        filas = cursor.fetchall()
    lista = []
    for fila in filas:
        metadatos = json.loads(fila["metadatos"])
        lista.append({"version": fila["version"], "estado": fila["estado"], "creado": fila["creado"], "registros": metadatos.get("registros_entrenamiento"),
                      "periodo_datos": metadatos.get("periodo_datos"), "f1_rojo": (metadatos.get("metrica_principal") or {}).get("valor_modelo"),
                      "decidido_por": fila["decidido_por"], "decidido_en": fila["decidido_en"], "justificacion": fila["justificacion"], "decision": fila["decision"]})
    return lista


def obtener(version: str) -> dict | None:
    """Una versión con todos sus metadatos (sin las tablas del percentil, que son muy largas). None si no existe."""
    with conexion() as cursor:
        _preparar(cursor)
        cursor.execute("SELECT version, estado, creado, metadatos, decidido_por, decidido_en, justificacion, decision FROM modelos WHERE version=%s", (version,))
        fila = cursor.fetchone()
    if fila is None:
        return None
    metadatos = {k: v for k, v in json.loads(fila["metadatos"]).items() if k != "referencia_percentil"}
    return {**{k: fila[k] for k in ("version", "estado", "creado", "decidido_por", "decidido_en", "justificacion", "decision")}, "metadatos": {**metadatos, "estado": fila["estado"]}}


# ------------------------------------------------------------------ decidir
def justificada(justificacion: str) -> str:
    texto = (justificacion or "").strip()
    if len(texto) < MIN_JUSTIFICACION:
        raise ErrorDeVersion(f"Escriba por qué toma esta decisión (al menos {MIN_JUSTIFICACION} caracteres).")
    return texto[:500]


def activar(version: str, usuario: str, justificacion: str) -> None:
    """Pone una versión en uso. La que estaba en uso queda como histórica, que se puede volver a activar si algo sale mal."""
    texto = justificada(justificacion)
    with _cerrojo:
        with conexion() as cursor:
            _preparar(cursor)
            cursor.execute("SELECT estado FROM modelos WHERE version=%s", (version,))
            fila = cursor.fetchone()
            if fila is None:
                raise ErrorDeVersion("Esa versión no existe.")
            if fila["estado"] == "activo":
                raise ErrorDeVersion("Esa versión ya es la que está en uso.")
            cursor.execute("UPDATE modelos SET estado='historico' WHERE estado='activo'")
            cursor.execute("UPDATE modelos SET estado='activo', decidido_por=%s, decidido_en=%s, justificacion=%s, decision='activada' WHERE version=%s",
                           (usuario, hora_actual(), texto, version))
        _olvidar_activa()


def descartar(version: str, usuario: str, justificacion: str) -> None:
    """Descarta una versión candidata: no entra en uso, pero se conserva como histórica con quién la descartó, cuándo y por qué."""
    texto = justificada(justificacion)
    with _cerrojo, conexion() as cursor:
        _preparar(cursor)
        cursor.execute("SELECT estado FROM modelos WHERE version=%s", (version,))
        fila = cursor.fetchone()
        if fila is None:
            raise ErrorDeVersion("Esa versión no existe.")
        if fila["estado"] != "candidato":
            raise ErrorDeVersion("Solo se puede descartar una versión candidata.")
        cursor.execute("UPDATE modelos SET estado='historico', decidido_por=%s, decidido_en=%s, justificacion=%s, decision='descartada' WHERE version=%s",
                       (usuario, hora_actual(), texto, version))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from ingesta import CARPETA_MODELOS
    try:
        sembrar(CARPETA_MODELOS)
        for v in listar():
            f1 = "sin dato" if v["f1_rojo"] is None else f"{v['f1_rojo']:.3f}"
            print(f"{v['version']:<6}{v['estado']:<11}F1 del rojo {f1:<9}creada {v['creado']:%Y-%m-%d %H:%M}"
                  + (f"  decidió {v['decidido_por']}: {v['justificacion']}" if v["decidido_por"] else ""))
    except ErrorBaseDatos as error:
        sys.exit(f"No se pudo completar: {error}")

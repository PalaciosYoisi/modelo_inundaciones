"""Administradores: claves cifradas, ingreso, sesiones y bloqueo tras intentos fallidos.

Las claves nunca se guardan en texto: solo su resumen cifrado (PBKDF2) con una sal propia de cada persona.
Crear o restablecer (la clave se genera al azar y se muestra una sola vez, en esta terminal):
    python usuarios.py crear yoisi "Yoisi Palacios"
    python usuarios.py restablecer yoisi
    python usuarios.py listar
"""
import hashlib
import secrets
import sys
from datetime import datetime, timedelta

from base_datos import conexion

ITERACIONES = 200_000
DURACION_SESION = timedelta(hours=8)
MAX_INTENTOS = 5
BLOQUEO = timedelta(minutes=5)
MIN_LARGO_CLAVE = 8


class ErrorDeAcceso(Exception):
    def __init__(self, mensaje: str, codigo: str, estado: int = 401):
        super().__init__(mensaje)
        self.mensaje, self.codigo, self.estado = mensaje, codigo, estado


def _cifrar(clave: str, sal: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), ITERACIONES).hex()


def _huella(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _limpio(usuario: str) -> str:
    return (usuario or "").strip().lower()[:30]


def _guardar_clave(cursor, usuario: str, clave: str, debe_cambiar: bool) -> None:
    sal = secrets.token_hex(16)
    cursor.execute("UPDATE usuarios SET clave_hash=%s, sal=%s, debe_cambiar_clave=%s, intentos_fallidos=0, bloqueado_hasta=NULL WHERE usuario=%s",
                   (_cifrar(clave, sal), sal, int(debe_cambiar), usuario))


# ------------------------------------------------------------------ administración de usuarios
def crear(usuario: str, nombre: str) -> str:
    """Crea al administrador y devuelve su clave inicial (debe cambiarla al ingresar)."""
    usuario, clave = _limpio(usuario), secrets.token_urlsafe(9)
    with conexion() as cursor:
        cursor.execute("INSERT INTO usuarios (usuario, nombre, clave_hash, sal, creado) VALUES (%s, %s, '', '', %s)", (usuario, nombre.strip(), datetime.now()))
        _guardar_clave(cursor, usuario, clave, True)
    return clave


def restablecer(usuario: str) -> str:
    usuario, clave = _limpio(usuario), secrets.token_urlsafe(9)
    with conexion() as cursor:
        cursor.execute("SELECT usuario FROM usuarios WHERE usuario=%s", (usuario,))
        if cursor.fetchone() is None:
            raise ErrorDeAcceso("Ese usuario no existe.", "USUARIO_NO_EXISTE", 404)
        _guardar_clave(cursor, usuario, clave, True)
        cursor.execute("DELETE FROM sesiones WHERE usuario=%s", (usuario,))
    return clave


def estado_de_todos(ahora: datetime | None = None) -> list[dict]:
    """Todos los administradores y si alguno está bloqueado, para que otro pueda desbloquearlo."""
    ahora = ahora or datetime.now()
    with conexion() as cursor:
        cursor.execute("SELECT usuario, nombre, debe_cambiar_clave, intentos_fallidos, bloqueado_hasta FROM usuarios ORDER BY usuario")
        filas = cursor.fetchall()
    return [{"usuario": f["usuario"], "nombre": f["nombre"], "debe_cambiar_clave": bool(f["debe_cambiar_clave"]), "intentos_fallidos": f["intentos_fallidos"],
             "bloqueado": bool(f["bloqueado_hasta"] and f["bloqueado_hasta"] > ahora),
             "bloqueado_hasta": f["bloqueado_hasta"] if f["bloqueado_hasta"] and f["bloqueado_hasta"] > ahora else None} for f in filas]


def desbloquear(usuario: str) -> None:
    usuario, existe = _limpio(usuario), False
    with conexion() as cursor:
        cursor.execute("SELECT usuario FROM usuarios WHERE usuario=%s", (usuario,))
        existe = cursor.fetchone() is not None
        if existe:
            cursor.execute("UPDATE usuarios SET intentos_fallidos=0, bloqueado_hasta=NULL WHERE usuario=%s", (usuario,))
    if not existe:
        raise ErrorDeAcceso("Ese usuario no existe.", "USUARIO_NO_EXISTE", 404)


def listar() -> list[dict]:
    with conexion() as cursor:
        cursor.execute("SELECT usuario, nombre, debe_cambiar_clave FROM usuarios ORDER BY usuario")
        return cursor.fetchall()


# ------------------------------------------------------------------ ingreso y sesiones
def iniciar_sesion(usuario: str, clave: str, ahora: datetime | None = None) -> dict:
    ahora, usuario = ahora or datetime.now(), _limpio(usuario)
    error = None                                          # se lanza después de cerrar la base, para que el intento fallido quede guardado
    with conexion() as cursor:
        cursor.execute("SELECT * FROM usuarios WHERE usuario=%s", (usuario,))
        fila = cursor.fetchone()
        if fila is None:
            _cifrar(clave or "", "00" * 16)               # tarda lo mismo que con un usuario real
            error = ErrorDeAcceso("El usuario o la clave no son correctos.", "CREDENCIALES_INCORRECTAS")
        elif fila["bloqueado_hasta"] and fila["bloqueado_hasta"] > ahora:
            minutos = int((fila["bloqueado_hasta"] - ahora).total_seconds() // 60) + 1
            error = ErrorDeAcceso(f"Demasiados intentos fallidos. Espere {minutos} minuto(s) e intente de nuevo.", "CUENTA_BLOQUEADA", 429)
        elif not secrets.compare_digest(_cifrar(clave or "", fila["sal"]), fila["clave_hash"]):
            fallidos = fila["intentos_fallidos"] + 1
            bloqueo = ahora + BLOQUEO if fallidos >= MAX_INTENTOS else None
            cursor.execute("UPDATE usuarios SET intentos_fallidos=%s, bloqueado_hasta=%s WHERE usuario=%s", (0 if bloqueo else fallidos, bloqueo, usuario))
            error = ErrorDeAcceso("El usuario o la clave no son correctos.", "CREDENCIALES_INCORRECTAS")
        if error is None:
            token = secrets.token_urlsafe(32)
            cursor.execute("UPDATE usuarios SET intentos_fallidos=0, bloqueado_hasta=NULL WHERE usuario=%s", (usuario,))
            cursor.execute("DELETE FROM sesiones WHERE expira < %s", (ahora,))
            cursor.execute("INSERT INTO sesiones (token_hash, usuario, expira) VALUES (%s, %s, %s)", (_huella(token), usuario, ahora + DURACION_SESION))
    if error:
        raise error
    return {"token": token, "usuario": usuario, "nombre": fila["nombre"], "debe_cambiar_clave": bool(fila["debe_cambiar_clave"])}


def validar_sesion(token: str, ahora: datetime | None = None) -> dict | None:
    with conexion() as cursor:
        cursor.execute("SELECT u.usuario, u.nombre, u.debe_cambiar_clave FROM sesiones s JOIN usuarios u ON u.usuario = s.usuario "
                       "WHERE s.token_hash=%s AND s.expira > %s", (_huella(token), ahora or datetime.now()))
        fila = cursor.fetchone()
    return None if fila is None else {**fila, "debe_cambiar_clave": bool(fila["debe_cambiar_clave"])}


def cerrar_sesion(token: str) -> None:
    with conexion() as cursor:
        cursor.execute("DELETE FROM sesiones WHERE token_hash=%s", (_huella(token),))


def cambiar_clave(usuario: str, actual: str, nueva: str) -> None:
    if len(nueva or "") < MIN_LARGO_CLAVE:
        raise ErrorDeAcceso(f"La clave nueva debe tener al menos {MIN_LARGO_CLAVE} caracteres.", "CLAVE_MUY_CORTA", 400)
    if nueva == actual:
        raise ErrorDeAcceso("La clave nueva debe ser distinta de la actual.", "CLAVE_IGUAL", 400)
    with conexion() as cursor:
        cursor.execute("SELECT sal, clave_hash FROM usuarios WHERE usuario=%s", (usuario,))
        fila = cursor.fetchone()
        if fila is None or not secrets.compare_digest(_cifrar(actual or "", fila["sal"]), fila["clave_hash"]):
            raise ErrorDeAcceso("La clave actual no es correcta.", "CREDENCIALES_INCORRECTAS", 400)
        _guardar_clave(cursor, usuario, nueva, False)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    orden = sys.argv[1:]
    try:
        if orden[:1] == ["crear"] and len(orden) == 3:
            print(f"Usuario «{_limpio(orden[1])}» creado. Clave inicial (se muestra una sola vez): {crear(orden[1], orden[2])}")
        elif orden[:1] == ["restablecer"] and len(orden) == 2:
            print(f"Nueva clave de «{_limpio(orden[1])}» (se muestra una sola vez): {restablecer(orden[1])}")
        elif orden[:1] == ["listar"]:
            for u in listar():
                print(f"{u['usuario']:<12} {u['nombre']}{'  (debe cambiar la clave)' if u['debe_cambiar_clave'] else ''}")
        else:
            print(__doc__)
    except Exception as error:
        print(f"No se pudo completar: {getattr(error, 'mensaje', None) or error}")

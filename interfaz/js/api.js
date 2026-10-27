// Comunicación con la API. Aquí no se dibuja nada: solo se piden datos y se traducen los errores.
import { API_URL } from "./config.js";

export class ErrorApi extends Error {
  constructor(mensaje, { estado = null, codigo = null, detalle = {} } = {}) {
    super(mensaje);
    this.estado = estado;
    this.codigo = codigo;
    this.detalle = detalle;
  }
}

// La sesión del administrador vive solo mientras esté abierta la pestaña (nunca se guarda la clave, solo el permiso temporal).
export const sesion = {
  token: () => sessionStorage.getItem("sesionAdmin"),
  guardar: (token) => sessionStorage.setItem("sesionAdmin", token),
  cerrar: () => sessionStorage.removeItem("sesionAdmin"),
};

async function pedir(ruta, { conSesion = false, ...opciones } = {}) {
  const encabezados = { ...(opciones.headers ?? {}) };
  if (conSesion) encabezados.Authorization = `Bearer ${sesion.token() ?? ""}`;
  let respuesta;
  try {
    respuesta = await fetch(API_URL + ruta, { ...opciones, headers: encabezados });
  } catch {
    throw new ErrorApi("No se pudo conectar con el servidor. Intente de nuevo en unos minutos.", { codigo: "SIN_CONEXION" });
  }
  if (!respuesta.ok) {
    let error = null;
    try { error = (await respuesta.json()).error; } catch { /* la respuesta no era JSON */ }
    throw new ErrorApi(error?.mensaje ?? "El servidor respondió con un error.", { estado: respuesta.status, codigo: error?.codigo, detalle: error?.detalle });
  }
  return respuesta;
}

const conArchivo = (archivo) => {
  const formulario = new FormData();
  formulario.append("archivo", archivo, archivo.name);
  return { method: "POST", body: formulario, conSesion: true };
};

// ------------------------------------------------------------------ Público
export const estaciones = async () => (await pedir("/estaciones")).json();
export const estacion = async (codigo) => (await pedir(`/estaciones/${encodeURIComponent(codigo)}`)).json();
export const modeloActivo = async () => (await pedir("/modelo/activo")).json();

// ------------------------------------------------------------------ Administrador
const json = (datos) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos) });

export const ingresar = async (usuario, clave) => (await pedir("/sesion", json({ usuario, clave }))).json();
export const quienSoy = async () => (await pedir("/cuenta/yo", { conSesion: true })).json();
export const salir = async () => (await pedir("/cuenta/salir", { method: "POST", conSesion: true })).json();
export const cambiarClave = async (actual, nueva) => (await pedir("/cuenta/clave", { ...json({ actual, nueva }), conSesion: true })).json();
export const descargarDatos = async (estacion, dias) => {
  const filtros = new URLSearchParams({ ...(estacion && { estacion }), ...(dias && { dias }) });
  return (await pedir(`/admin/descargas/datos.csv?${filtros}`, { conSesion: true })).blob();
};
export const lotes = async () => (await pedir("/admin/lotes", { conSesion: true })).json();
export const lote = async (id) => (await pedir(`/admin/lotes/${id}`, { conSesion: true })).json();
export const descargarLote = async (id) => (await pedir(`/admin/lotes/${id}/descargar`, { conSesion: true })).blob();
export const confirmarLote = async (id) => (await pedir(`/admin/lotes/${id}/confirmar`, { method: "POST", conSesion: true })).json();
export const rechazarLote = async (id) => (await pedir(`/admin/lotes/${id}/rechazar`, { method: "POST", conSesion: true })).json();
export const consultarAhora = async () => (await pedir("/admin/consulta/ejecutar", { method: "POST", conSesion: true })).json();
export const administradores = async () => (await pedir("/admin/administradores", { conSesion: true })).json();
export const desbloquear = async (usuario) => (await pedir(`/admin/administradores/${encodeURIComponent(usuario)}/desbloquear`, { method: "POST", conSesion: true })).json();
export const actividad = async (usuario, accion) => {
  const filtros = new URLSearchParams({ ...(usuario && { usuario }), ...(accion && { accion }) });
  return (await pedir(`/admin/actividad?${filtros}`, { conSesion: true })).json();
};
export const validar = async (archivo) => (await pedir("/admin/validar", conArchivo(archivo))).json();
export const analizar = async (archivo) => (await pedir("/admin/analizar?incluir_figuras=true", conArchivo(archivo))).json();
export const publicar = async (archivo) => (await pedir("/admin/publicar", conArchivo(archivo))).json();
export const descargarPredicciones = async (archivo) => (await pedir("/admin/predecir?formato=csv", conArchivo(archivo))).blob();

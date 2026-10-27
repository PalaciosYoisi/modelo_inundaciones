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

// La clave del administrador vive solo mientras esté abierta la pestaña.
export const sesion = {
  clave: () => sessionStorage.getItem("claveAdmin"),
  guardar: (clave) => sessionStorage.setItem("claveAdmin", clave),
  cerrar: () => sessionStorage.removeItem("claveAdmin"),
};

async function pedir(ruta, { clave = null, ...opciones } = {}) {
  const encabezados = { ...(opciones.headers ?? {}) };
  if (clave !== null) encabezados["X-Clave-Admin"] = clave;
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
  return { method: "POST", body: formulario, clave: sesion.clave() ?? "" };
};

// ------------------------------------------------------------------ Público
export const estaciones = async () => (await pedir("/estaciones")).json();
export const estacion = async (codigo) => (await pedir(`/estaciones/${encodeURIComponent(codigo)}`)).json();
export const modeloActivo = async () => (await pedir("/modelo/activo")).json();

// ------------------------------------------------------------------ Administrador
export const verificarClave = async (clave) => (await pedir("/admin/verificar", { clave })).json();
export const validar = async (archivo) => (await pedir("/admin/validar", conArchivo(archivo))).json();
export const analizar = async (archivo) => (await pedir("/admin/analizar?incluir_figuras=true", conArchivo(archivo))).json();
export const publicar = async (archivo) => (await pedir("/admin/publicar", conArchivo(archivo))).json();
export const descargarPredicciones = async (archivo) => (await pedir("/admin/predecir?formato=csv", conArchivo(archivo))).blob();

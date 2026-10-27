// Página del administrador: ingreso con clave, resumen de las estaciones y flujo de archivos (revisar, analizar y publicar).
import * as api from "./api.js";
import { fecha, guardarArchivo, h, hace, icono, numero, reemplazar } from "./dom.js";
import { iniciarCarga } from "./cargar_archivos.js";
import { iniciarConsulta } from "./consulta_diaria.js";
import { iniciarDescargas } from "./descargas.js";

const $ = (id) => document.getElementById(id);
let yo = null;
const carga = iniciarCarga({
  alVencer: () => { api.sesion.cerrar(); mostrarPanel(false); },
  alLoteCreado: () => consulta.actualizarAviso(),
  alRevisar: (id) => { irAVista("consulta"); consulta.abrir(id); },
});
const descargas = iniciarDescargas({ alVencer: () => { api.sesion.cerrar(); mostrarPanel(false); } });
const consulta = iniciarConsulta({
  alVencer: () => { api.sesion.cerrar(); mostrarPanel(false); },
  alCambiar: () => cargarResumen(),
  alRevisar: () => irAVista("consulta"),
});

// ------------------------------------------------------------------ Ingreso y salida
const mostrar = (nodo, visible) => { nodo.hidden = !visible; };

function mostrarPanel(visible, persona = null) {
  mostrar($("ingreso"), !visible);
  mostrar($("panel-admin"), visible);
  if (visible) {
    $("nombre-admin").textContent = persona?.nombre ?? "Administrador";
    irAVista("resumen");
    consulta.actualizarAviso();
  } else {
    mostrar($("form-admin"), true);
    mostrar($("form-clave"), false);
    $("usuario-admin").focus();
  }
}

function entrar(persona) {
  if (persona.debe_cambiar_clave) {               // primer ingreso: antes de nada hay que elegir una clave propia
    mostrar($("form-admin"), false);
    mostrar($("form-clave"), true);
    $("clave-actual").focus();
  } else {
    mostrarPanel(true, persona);
  }
}

const textoDeError = (error) => (error.estado === 401 || error.estado === 429 || error.estado === 400 ? error.message : "No se pudo completar. Intente de nuevo en unos minutos.");

$("form-admin").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  try {
    const persona = await api.ingresar($("usuario-admin").value, $("clave-admin").value);
    api.sesion.guardar(persona.token);
    yo = persona;
    $("error-admin").hidden = true;
    $("clave-admin").value = "";
    entrar(persona);
  } catch (error) {
    $("error-admin").textContent = textoDeError(error);
    $("error-admin").hidden = false;
  }
});

$("form-clave").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  try {
    await api.cambiarClave($("clave-actual").value, $("clave-nueva").value);
    $("form-clave").reset();
    $("error-clave").hidden = true;
    mostrarPanel(true, yo);
  } catch (error) {
    $("error-clave").textContent = error.message;
    $("error-clave").hidden = false;
  }
});

function cerrarSesion() {
  api.salir().catch(() => {});                    // si no se puede avisar al servidor, igual se cierra aquí
  api.sesion.cerrar();
  yo = null;
  carga.reiniciar();
  mostrarPanel(false);
}
$("cerrar-sesion").addEventListener("click", cerrarSesion);

if (api.sesion.token()) {                         // si la pestaña ya tenía una sesión, se recupera sin molestar
  api.quienSoy().then((persona) => { yo = persona; entrar(persona); }).catch(() => api.sesion.cerrar());
}

// ------------------------------------------------------------------ Menú lateral
const TITULOS = { resumen: "Resumen", consulta: "Consulta diaria", datos: "Cargar archivos", descargas: "Descargas", actividad: "Actividad", equipo: "Administradores" };

function irAVista(vista) {
  for (const enlace of document.querySelectorAll("[data-vista]")) {
    if (enlace.dataset.vista === vista) enlace.setAttribute("aria-current", "page"); else enlace.removeAttribute("aria-current");
  }
  for (const nombre of Object.keys(TITULOS)) mostrar($(`vista-${nombre}`), nombre === vista);
  $("titulo-vista").textContent = TITULOS[vista];
  $("lateral").classList.remove("lateral--abierto");
  if (vista === "resumen") { cargarResumen(); consulta.actualizarAviso(); }
  if (vista === "consulta") consulta.cargar();
  if (vista === "descargas") descargas.cargar();
  if (vista === "actividad") cargarActividad();
  if (vista === "equipo") cargarEquipo();
}
document.querySelectorAll("[data-vista]").forEach((e) => e.addEventListener("click", () => irAVista(e.dataset.vista)));
$("abrir-menu").addEventListener("click", () => $("lateral").classList.toggle("lateral--abierto"));
// ------------------------------------------------------------------ Resumen
const COLOR = { Verde: "verde", Amarillo: "amarillo", Rojo: "rojo" };

function indicador(nombre, valor, detalle, color) {
  return h("article", { clase: `indicador indicador--${color}` },
    h("span", { clase: "indicador__icono" }, icono(nombre)),
    h("div", {}, h("b", {}, valor), h("span", {}, detalle)));
}

async function cargarResumen() {
  try {
    const d = await api.estaciones();
    const conSemaforo = d.estaciones.filter((e) => e.semaforo && !e.desactualizada);
    const rojas = conSemaforo.filter((e) => e.semaforo === "Rojo").length;
    reemplazar($("indicadores"),
      indicador("mapa", numero(d.estaciones.length), "estaciones publicadas", "agua"),
      indicador("aviso", numero(rojas), "en rojo ahora", rojas ? "rojo" : "verde"),
      indicador("reloj", fecha(d.ultimo_dato, false), "último dato recibido", "amarillo"),
      indicador("escudo", d.modelo_version, "versión del modelo", "azul"));
    reemplazar($("tabla-estaciones"), tablaEstaciones(d.estaciones));
  } catch (error) {
    reemplazar($("indicadores"));
    reemplazar($("tabla-estaciones"), h("p", { clase: "vacio" }, error.message));
  }
}

function tablaEstaciones(lista) {
  const fila = (e) => h("tr", {},
    h("td", {}, h("b", {}, e.nombre), h("br"), h("small", {}, `${e.municipio} · ${e.codigo}`)),
    h("td", {}, e.ultima_lectura ? [fecha(e.ultima_lectura), h("br"), h("small", {}, hace(e.horas_desde_ultima_lectura))] : "—"),
    h("td", { clase: "num" }, e.prob_rojo === undefined ? "—" : `${Math.round(e.prob_rojo * 100)} %`),
    h("td", {}, e.semaforo ? h("span", { clase: `etiqueta etiqueta--${COLOR[e.semaforo]}` }, e.semaforo) : h("span", { clase: "etiqueta etiqueta--gris" }, "Sin datos")),
    h("td", {}, e.desactualizada ? h("span", { clase: "etiqueta etiqueta--gris" }, "No recientes") : "Recientes"));
  return h("div", { clase: "tabla" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Estación", "Última lectura", "Posibilidad de peligro", "Semáforo", "Datos"].map((t, i) => h("th", { clase: i === 2 ? "num" : null }, t)))),
    h("tbody", {}, lista.map(fila))));
}

// ------------------------------------------------------------------ Actividad (auditoría)
const ACCIONES = {
  ingreso: "Ingresó", ingreso_fallido: "Intento de ingreso fallido", salida: "Cerró sesión", cambio_de_clave: "Cambió su clave",
  validar: "Revisó un archivo", analizar: "Analizó un archivo", predecir: "Hizo predicciones con un archivo",
  descargar_predicciones: "Descargó predicciones", publicar: "Publicó datos", publicar_rechazado: "Intentó publicar un archivo con errores",
  revisar_archivos: "Revisó archivos de datos", subir_archivos: "Subió archivos para crear un lote", subir_archivos_resultado: "Unificó los archivos en un lote",
  desbloquear: "Desbloqueó a un administrador", descargar_datos: "Descargó los datos publicados", consulta_manual: "Consultó datos.gov.co", consulta_diaria: "Consulta diaria automática",
  descargar_lote: "Descargó un lote", confirmar_lote: "Confirmó un lote", confirmar_lote_fallido: "Confirmó un lote, pero falló la publicación", rechazar_lote: "Rechazó un lote",
};
const cuentaDe = (r) => {
  const d = r.detalle ?? {};
  return [d.archivo && `Archivo: ${d.archivo}`, d.archivos && `Archivos: ${d.archivos.join(", ")}`, d.filas !== undefined && `${numero(d.filas)} filas`, d.errores && `${d.errores} errores`, d.motivo && `Motivo: ${d.motivo}`, d.usuario && `Persona: ${d.usuario}`, d.lote && `Lote ${d.lote}`, d.estacion && `Estación: ${d.estacion}`, d.dias && `Periodo: ${d.dias === "todos" ? "todo" : `${d.dias} días`}`, d.resultado && `Resultado: ${d.resultado}`].filter(Boolean).join(" · ");
};

async function cargarActividad() {
  try {
    const d = await api.actividad($("filtro-usuario").value, $("filtro-accion").value);
    const llenar = (selector, valores, texto) => {
      const elegido = selector.value;
      reemplazar(selector, h("option", { value: "" }, "Todas"), valores.map((v) => h("option", { value: v }, texto(v))));
      selector.value = valores.includes(elegido) ? elegido : "";
    };
    llenar($("filtro-usuario"), d.usuarios, (v) => v);
    llenar($("filtro-accion"), d.acciones, (v) => ACCIONES[v] ?? v);
    reemplazar($("tabla-actividad"), d.registros.length
      ? h("div", { clase: "tabla" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Cuándo", "Quién", "Qué hizo", "Detalle"].map((x) => h("th", {}, x)))),
        h("tbody", {}, d.registros.map((r) => h("tr", {}, h("td", {}, fecha(r.fecha)), h("td", {}, h("b", {}, r.usuario)),
          h("td", {}, ACCIONES[r.accion] ?? r.accion), h("td", {}, cuentaDe(r)))))))
      : h("p", { clase: "vacio" }, "Todavía no hay actividad con ese filtro."));
  } catch (error) {
    if (error.estado === 401) { api.sesion.cerrar(); mostrarPanel(false); }
    reemplazar($("tabla-actividad"), h("p", { clase: "vacio" }, error.message));
  }
}
$("filtro-usuario").addEventListener("change", cargarActividad);
$("filtro-accion").addEventListener("change", cargarActividad);
// ------------------------------------------------------------------ Administradores (desbloquear)
async function cargarEquipo() {
  try {
    const lista = await api.administradores();
    const estado = (a) => (a.bloqueado ? h("span", { clase: "etiqueta etiqueta--rojo" }, `Bloqueado hasta las ${new Date(a.bloqueado_hasta.replace(" ", "T")).toLocaleTimeString("es-CO", { hour: "numeric", minute: "2-digit" })}`)
      : a.debe_cambiar_clave ? h("span", { clase: "etiqueta etiqueta--amarillo" }, "Debe cambiar su clave") : h("span", { clase: "etiqueta etiqueta--verde" }, "Activo"));
    reemplazar($("tabla-equipo"), h("div", { clase: "tabla" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Usuario", "Nombre", "Estado", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, lista.map((a) => h("tr", {}, h("td", {}, h("b", {}, a.usuario)), h("td", {}, a.nombre), h("td", {}, estado(a)),
        h("td", {}, a.bloqueado ? h("button", { clase: "boton boton--contorno-oscuro boton--pequeno", type: "button", onclick: () => desbloquear(a.usuario) }, "Desbloquear") : null)))))));
  } catch (error) {
    if (error.estado === 401) { api.sesion.cerrar(); mostrarPanel(false); }
    reemplazar($("tabla-equipo"), h("p", { clase: "vacio" }, error.message));
  }
}

async function desbloquear(usuario) {
  try {
    await api.desbloquear(usuario);
  } catch (error) {
    reemplazar($("tabla-equipo"), h("p", { clase: "vacio" }, error.message));
    return;
  }
  cargarEquipo();
}

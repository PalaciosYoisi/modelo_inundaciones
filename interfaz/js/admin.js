// Página del administrador: ingreso con clave, resumen de las estaciones y flujo de archivos (revisar, analizar y publicar).
import * as api from "./api.js";
import { fecha, guardarArchivo, h, hace, icono, numero, reemplazar } from "./dom.js";
import { iniciarConsulta } from "./consulta_diaria.js";
import { iniciarDescargas } from "./descargas.js";
import { vistaAnalisis, vistaPublicacion, vistaRevision } from "./vistas_admin.js";

const $ = (id) => document.getElementById(id);
const botones = { revisar: $("adm-revisar"), analizar: $("adm-analizar"), publicar: $("adm-publicar") };
const paneles = { revision: $("adm-panel-revision"), analisis: $("adm-panel-analisis"), publicacion: $("adm-panel-publicacion") };
const pestanas = { revision: $("adm-pestana-revision"), analisis: $("adm-pestana-analisis"), publicacion: $("adm-pestana-publicacion") };
const iniciales = new Map(Object.values(paneles).map((p) => [p, [...p.childNodes].map((n) => n.cloneNode(true))]));
let archivo = null;
let yo = null;
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
  elegir(null);
  mostrarPanel(false);
}
$("cerrar-sesion").addEventListener("click", cerrarSesion);

if (api.sesion.token()) {                         // si la pestaña ya tenía una sesión, se recupera sin molestar
  api.quienSoy().then((persona) => { yo = persona; entrar(persona); }).catch(() => api.sesion.cerrar());
}

// ------------------------------------------------------------------ Menú lateral
const TITULOS = { resumen: "Resumen", consulta: "Consulta diaria", datos: "Cargar archivo", descargas: "Descargas", actividad: "Actividad", equipo: "Administradores" };

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

// ------------------------------------------------------------------ Avisos y pestañas
function aviso(tipo, mensaje) {
  const caja = $("adm-aviso");
  caja.className = `aviso aviso--${tipo}`;
  reemplazar(caja, tipo === "error" ? icono("aviso") : null, h("p", { style: "margin:0" }, mensaje));
  caja.hidden = false;
}
const ocultarAviso = () => { $("adm-aviso").hidden = true; };

function irA(clave) {
  for (const [k, pestana] of Object.entries(pestanas)) {
    pestana.setAttribute("aria-selected", String(k === clave));
    paneles[k].hidden = k !== clave;
  }
}
const claves = Object.keys(pestanas);
for (const [clave, pestana] of Object.entries(pestanas)) {
  pestana.addEventListener("click", () => irA(clave));
  pestana.addEventListener("keydown", (e) => {
    const paso = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!paso) return;
    const siguiente = claves[(claves.indexOf(clave) + paso + claves.length) % claves.length];
    irA(siguiente);
    pestanas[siguiente].focus();
  });
}

// ------------------------------------------------------------------ Archivo elegido
const tamano = (bytes) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);

function elegir(nuevo) {
  archivo = nuevo;
  $("adm-zona-carga").dataset.conArchivo = String(Boolean(nuevo));
  $("adm-archivo-elegido").hidden = !nuevo;
  if (nuevo) {
    $("adm-archivo-nombre").textContent = nuevo.name;
    $("adm-archivo-detalle").textContent = tamano(nuevo.size);
  } else {
    $("adm-archivo").value = "";
  }
  Object.values(botones).forEach((b) => { b.disabled = !nuevo; });
  for (const [panel, nodos] of iniciales) reemplazar(panel, nodos.map((n) => n.cloneNode(true)));
  ocultarAviso();
  irA("revision");
}

$("adm-archivo").addEventListener("change", (e) => elegir(e.target.files[0] ?? null));
$("adm-quitar").addEventListener("click", () => elegir(null));
const area = $("adm-area-carga");
["dragenter", "dragover"].forEach((n) => area.addEventListener(n, (e) => { e.preventDefault(); area.classList.add("arrastrando"); }));
["dragleave", "drop"].forEach((n) => area.addEventListener(n, () => area.classList.remove("arrastrando")));
area.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) elegir(e.dataTransfer.files[0]); });

// ------------------------------------------------------------------ Acciones
async function ejecutar(textoCarga, tarea) {
  Object.values(botones).forEach((b) => { b.disabled = true; });
  aviso("cargando", textoCarga);
  try {
    await tarea();
    ocultarAviso();
  } catch (error) {
    if (error.estado === 401) {                   // la sesión venció: se vuelve a pedir el ingreso
      api.sesion.cerrar();
      mostrarPanel(false);
    }
    if (error.detalle?.diagnostico) {             // el archivo no sirve: se muestra el diagnóstico completo
      reemplazar(paneles.revision, vistaRevision(error.detalle.diagnostico));
      irA("revision");
    }
    aviso("error", error.message ?? "Ocurrió un error inesperado.");
  } finally {
    Object.values(botones).forEach((b) => { b.disabled = !archivo; });
  }
}

botones.revisar.addEventListener("click", () => ejecutar("Revisando el archivo…", async () => {
  reemplazar(paneles.revision, vistaRevision((await api.validar(archivo)).validacion));
  irA("revision");
}));

botones.analizar.addEventListener("click", () => ejecutar("Analizando los datos…", async () => {
  reemplazar(paneles.analisis, vistaAnalisis(await api.analizar(archivo)));
  irA("analisis");
}));

botones.publicar.addEventListener("click", () => ejecutar("Revisando y publicando…", async () => {
  const respuesta = await api.publicar(archivo);
  reemplazar(paneles.revision, vistaRevision(respuesta.validacion));
  reemplazar(paneles.publicacion, vistaPublicacion(respuesta, { alDescargar: descargar }));
  irA("publicacion");
}));

async function descargar() {
  await ejecutar("Preparando el archivo…", async () => {
    guardarArchivo(await api.descargarPredicciones(archivo), `predicciones_${archivo.name.replace(/\.[^.]+$/, "")}.csv`);
  });
}

// ------------------------------------------------------------------ Actividad (auditoría)
const ACCIONES = {
  ingreso: "Ingresó", ingreso_fallido: "Intento de ingreso fallido", salida: "Cerró sesión", cambio_de_clave: "Cambió su clave",
  validar: "Revisó un archivo", analizar: "Analizó un archivo", predecir: "Hizo predicciones con un archivo",
  descargar_predicciones: "Descargó predicciones", publicar: "Publicó datos", publicar_rechazado: "Intentó publicar un archivo con errores",
  desbloquear: "Desbloqueó a un administrador", descargar_datos: "Descargó los datos publicados", consulta_manual: "Consultó datos.gov.co", consulta_diaria: "Consulta diaria automática",
  descargar_lote: "Descargó un lote", confirmar_lote: "Confirmó un lote", confirmar_lote_fallido: "Confirmó un lote, pero falló la publicación", rechazar_lote: "Rechazó un lote",
};
const cuentaDe = (r) => {
  const d = r.detalle ?? {};
  return [d.archivo && `Archivo: ${d.archivo}`, d.filas !== undefined && `${numero(d.filas)} filas`, d.errores && `${d.errores} errores`, d.motivo && `Motivo: ${d.motivo}`, d.usuario && `Persona: ${d.usuario}`, d.lote && `Lote ${d.lote}`, d.estacion && `Estación: ${d.estacion}`, d.dias && `Periodo: ${d.dias === "todos" ? "todo" : `${d.dias} días`}`, d.resultado && `Resultado: ${d.resultado}`].filter(Boolean).join(" · ");
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

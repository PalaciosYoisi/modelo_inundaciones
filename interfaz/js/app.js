// Punto de entrada de la página pública: carga las estaciones y conecta el mapa con la lista y el detalle.
import * as api from "./api.js";
import { h, reemplazar } from "./dom.js";
import { crearMapa, vistaConfianza, vistaDetalle, vistaLista } from "./publico.js";

const $ = (id) => document.getElementById(id);
const mapa = crearMapa($("mapa"), elegir);
let datos = null;
let elegida = null;

// ------------------------------------------------------------------ Estaciones
function pintar() {
  reemplazar($("lista-estaciones"), vistaLista(datos.estaciones, elegida, elegir));
  mapa.pintar(datos.estaciones, elegida);
}

async function elegir(codigo) {
  elegida = codigo;
  pintar();
  try {
    reemplazar($("detalle"), vistaDetalle(await api.estacion(codigo), datos.umbral_aviso_rojo));
  } catch (error) {
    reemplazar($("detalle"), h("p", { clase: "vacio" }, error.message));
  }
}

async function cargarEstaciones() {
  try {
    datos = await api.estaciones();
  } catch (error) {
    reemplazar($("lista-estaciones"), h("p", { clase: "vacio" }, error.message));
    return;
  }
  // Al entrar se muestra primero la estación con más posibilidad de peligro
  const conSemaforo = datos.estaciones.filter((e) => e.semaforo);
  const masRiesgo = [...conSemaforo].sort((a, b) => b.prob_rojo - a.prob_rojo)[0] ?? datos.estaciones[0];
  const mantener = datos.estaciones.some((e) => e.codigo === elegida) ? elegida : masRiesgo?.codigo;
  if (mantener) await elegir(mantener); else pintar();
  setTimeout(() => mapa.ajustar(), 150);
}

// ------------------------------------------------------------------ Qué tan confiable es
async function cargarConfianza() {
  try {
    const vista = vistaConfianza(await api.modeloActivo());
    $("modelo-descripcion").textContent = vista.descripcion;
    reemplazar($("modelo-cifras"), vista.cifras);
  } catch (error) {
    $("modelo-descripcion").textContent = error.message;
  }
}

// ------------------------------------------------------------------ Barra que se oscurece al bajar
const barra = $("barra");
const marcarBarra = () => barra.classList.toggle("barra--solida", window.scrollY > 40);
window.addEventListener("scroll", marcarBarra, { passive: true });
marcarBarra();

cargarEstaciones();
cargarConfianza();

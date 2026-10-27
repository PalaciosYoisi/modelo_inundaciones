// Descargas del administrador: los datos publicados (combinados) y los lotes consultados a datos.gov.co.
import * as api from "./api.js";
import { fecha, guardarArchivo, h, icono, numero, reemplazar } from "./dom.js";

const $ = (id) => document.getElementById(id);
const ETIQUETAS = { pendiente: ["Esperando revisión", "amarillo"], con_errores: ["Con errores", "rojo"], confirmado: ["Confirmado", "verde"], rechazado: ["Rechazado", "gris"], unificado: ["Unificado", "gris"] };

const sinTildes = (texto) => texto.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

export function iniciarDescargas({ alVencer }) {
  let listas = false;
  const falla = (error) => { if (error.estado === 401) alVencer(); return error.message; };

  function aviso(tipo, mensaje) {
    const caja = $("desc-aviso");
    caja.className = `aviso aviso--${tipo}`;
    reemplazar(caja, tipo === "error" ? icono("aviso") : null, h("p", { style: "margin:0" }, mensaje));
    caja.hidden = false;
  }

  async function descargarLote(id) {
    try { guardarArchivo(await api.descargarLote(id), `lote_${id}.csv`); } catch (error) { aviso("error", falla(error)); }
  }

  function tablaLotes(lista) {
    if (!lista.length) return h("p", { clase: "vacio" }, "Todavía no hay lotes consultados.");
    return h("div", { clase: "tabla" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Lote", "Consultado", "Estado", "Horas", ""].map((t, i) => h("th", { clase: i === 3 ? "num" : null }, t)))),
      h("tbody", {}, lista.map((l) => h("tr", {}, h("td", {}, h("b", {}, `Lote ${l.id}`)), h("td", {}, fecha(l.creado)),
        h("td", {}, h("span", { clase: `etiqueta etiqueta--${ETIQUETAS[l.estado]?.[1] ?? "gris"}` }, ETIQUETAS[l.estado]?.[0] ?? l.estado)),
        h("td", { clase: "num" }, numero(l.filas)),
        h("td", {}, h("button", { clase: "boton boton--contorno-oscuro boton--pequeno", type: "button", onclick: () => descargarLote(l.id) }, icono("descarga"), "Descargar")))))));
  }

  async function cargar() {
    if (!listas) {
      try {
        const { estaciones } = await api.estaciones();
        $("desc-estacion").append(...estaciones.map((e) => h("option", { value: e.codigo }, e.nombre)));
        listas = true;
      } catch { /* queda solo «Todas» */ }
    }
    try {
      reemplazar($("desc-lotes"), tablaLotes((await api.lotes()).lotes));
    } catch (error) {
      reemplazar($("desc-lotes"), h("p", { clase: "vacio" }, falla(error)));
    }
  }

  $("desc-datos").addEventListener("click", async () => {
    const lista = $("desc-estacion"), estacion = lista.value, dias = $("desc-dias").value;
    const boton = $("desc-datos");
    boton.disabled = true;
    aviso("cargando", "Preparando el archivo…");
    try {
      guardarArchivo(await api.descargarDatos(estacion, dias), `datos_${estacion ? sinTildes(lista.selectedOptions[0].textContent) : "todas_las_estaciones"}${dias ? `_${dias}_dias` : ""}.csv`);
      $("desc-aviso").hidden = true;
    } catch (error) {
      aviso("error", falla(error));
    } finally {
      boton.disabled = false;
    }
  });

  return { cargar };
}

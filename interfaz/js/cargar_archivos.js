// Carga de archivos: se suben los CSV de datos.gov.co (o uno ya combinado), el sistema reconoce y revisa cada uno y los unifica en un lote.
import * as api from "./api.js";
import { fecha, h, icono, numero, reemplazar } from "./dom.js";
import { vistaAnalisis } from "./vistas_admin.js";

const $ = (id) => document.getElementById(id);
const tamano = (bytes) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);
const ICONO = { ok: "check", advertencias: "aviso", error: "cruz" };
const ETIQUETA = { ok: "verde", advertencias: "amarillo", error: "rojo" };

export function iniciarCarga({ alVencer, alLoteCreado, alRevisar }) {
  let archivos = [];
  let nombres = {};
  const botones = { revisar: $("adm-revisar"), analizar: $("adm-analizar"), crear: $("adm-crear") };
  const falla = (error) => { if (error.estado === 401) alVencer(); return error.message ?? "Ocurrió un error inesperado."; };

  function aviso(tipo, mensaje) {
    const caja = $("adm-aviso");
    caja.className = `aviso aviso--${tipo}`;
    reemplazar(caja, tipo === "error" ? icono("aviso") : null, h("p", { style: "margin:0" }, mensaje));
    caja.hidden = false;
  }

  function limpiarResultados() {
    $("adm-aviso").hidden = true;
    $("adm-resultado").hidden = true;
    $("adm-analisis").hidden = true;
  }

  // ---------------------------------------------------------------- Archivos elegidos
  function pintarLista() {
    reemplazar($("adm-archivos"), archivos.map((a, i) => h("li", {},
      icono("archivo"), h("span", {}, h("b", {}, a.name), h("small", {}, tamano(a.size))),
      h("button", { clase: "boton-texto", type: "button", onclick: () => { archivos.splice(i, 1); pintarLista(); limpiarResultados(); } }, "Quitar"))));
    $("adm-archivos").hidden = !archivos.length;
    botones.revisar.disabled = botones.crear.disabled = !archivos.length;
    botones.analizar.disabled = archivos.length !== 1;
  }

  function agregar(lista) {
    for (const a of lista) if (!archivos.some((x) => x.name === a.name && x.size === a.size)) archivos.push(a);
    pintarLista();
    limpiarResultados();
  }

  $("adm-archivo").addEventListener("change", (e) => { agregar([...e.target.files]); e.target.value = ""; });
  const area = $("adm-area-carga");
  ["dragenter", "dragover"].forEach((n) => area.addEventListener(n, (e) => { e.preventDefault(); area.classList.add("arrastrando"); }));
  ["dragleave", "drop"].forEach((n) => area.addEventListener(n, () => area.classList.remove("arrastrando")));
  area.addEventListener("drop", (e) => { e.preventDefault(); agregar([...e.dataTransfer.files]); });

  // ---------------------------------------------------------------- Resultado por archivo
  function tarjetaDeArchivo(i) {
    const estaciones = i.estaciones.map((c) => nombres[c] ?? c);
    return h("article", { clase: `informe informe--${i.estado}` },
      h("div", { clase: "informe__cabecera" }, h("span", { clase: "informe__icono" }, icono(ICONO[i.estado])), h("b", {}, i.archivo),
        i.tipo !== "desconocido" ? h("span", { clase: `etiqueta etiqueta--${ETIQUETA[i.estado]}` }, i.tipo_texto) : null),
      i.filas_utiles ? h("p", {}, `${numero(i.filas_utiles)} lecturas útiles de ${numero(i.filas)} · ${fecha(i.desde, false)} a ${fecha(i.hasta, false)}`) : null,
      estaciones.length ? h("p", { clase: "informe__estaciones" }, `Estaciones: ${estaciones.join(", ")}`) : null,
      i.hallazgos.length ? h("div", { clase: "hallazgos" }, i.hallazgos.map((x) => h("article", { clase: `hallazgo hallazgo--${x.severidad}` },
        h("div", { clase: "hallazgo__cabecera" }, h("span", { clase: `etiqueta etiqueta--${x.severidad}` }, x.severidad === "error" ? "Error" : "Advertencia")),
        h("p", {}, x.mensaje)))) : h("p", {}, "El archivo está bien."));
  }

  function pintarResultado(r) {
    const hayErrores = r.informes.some((i) => i.estado === "error");
    const lote = r.lote;
    const [clase, titulo] = hayErrores ? ["incompatible", "Hay archivos con errores"] : lote?.lote ? ["publicado", `Lote ${lote.lote} creado`]
      : lote ? ["gris", "No hay nada nuevo"] : ["compatible", "Los archivos sirven"];
    reemplazar($("adm-resultado"),
      h("div", { clase: `estado estado--${clase}` }, h("span", { clase: "estado__icono" }, icono(hayErrores ? "cruz" : "check")), h("div", {}, h("h3", {}, titulo), h("p", {}, r.mensaje))),
      lote?.lote ? h("p", {}, h("button", { clase: "boton boton--primario boton--pequeno", type: "button", onclick: () => alRevisar(lote.lote) }, "Revisar el lote y decidir")) : null,
      h("h3", {}, "Archivo por archivo"), r.informes.map(tarjetaDeArchivo));
    $("adm-resultado").hidden = false;
  }

  // ---------------------------------------------------------------- Acciones
  async function ejecutar(textoCarga, tarea) {
    Object.values(botones).forEach((b) => { b.disabled = true; });
    aviso("cargando", textoCarga);
    try {
      await tarea();
      $("adm-aviso").hidden = true;
    } catch (error) {
      aviso("error", falla(error));
    } finally {
      pintarLista();
    }
  }

  async function conNombres() {
    if (Object.keys(nombres).length) return;
    try { nombres = Object.fromEntries((await api.estaciones()).estaciones.map((e) => [e.codigo, e.nombre])); } catch { /* se muestran los códigos */ }
  }

  botones.revisar.addEventListener("click", () => ejecutar("Revisando los archivos. Los de datos.gov.co pueden tardar un poco…", async () => {
    $("adm-analisis").hidden = true;
    await conNombres();
    pintarResultado(await api.subirArchivos(archivos, false));
  }));

  botones.crear.addEventListener("click", () => ejecutar("Revisando y unificando los archivos…", async () => {
    $("adm-analisis").hidden = true;
    await conNombres();
    const r = await api.subirArchivos(archivos, true);
    pintarResultado(r);
    if (r.lote?.lote) alLoteCreado();
  }));

  botones.analizar.addEventListener("click", () => ejecutar("Analizando los datos…", async () => {
    $("adm-resultado").hidden = true;
    reemplazar($("adm-analisis"), h("h2", {}, `Análisis de ${archivos[0].name}`), vistaAnalisis(await api.analizar(archivos[0])));
    $("adm-analisis").hidden = false;
  }));

  return { reiniciar() { archivos = []; pintarLista(); limpiarResultados(); } };
}

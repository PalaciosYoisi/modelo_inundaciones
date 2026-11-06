// Reportes: ver en pantalla, descargar o imprimir los reportes del sistema (análisis, compatibilidad, predicciones y actualización del modelo).
// Los reportes salen de lo que ya está guardado: los datos publicados o un lote de datos nuevos. No hay que subir nada.
import * as api from "./api.js";
import { fecha, guardarArchivo, h, icono, numero, reemplazar } from "./dom.js";

const $ = (id) => document.getElementById(id);

const DE_DATOS = [
  { tipo: "eda", icono: "grafica", titulo: "Análisis de los datos", texto: "Resumen de los datos, qué tan completos están, estadísticas, gráficas y observaciones generadas por el sistema." },
  { tipo: "compatibilidad", icono: "escudo", titulo: "Compatibilidad", texto: "Columnas esperadas y recibidas, tipos de datos, advertencias, errores y el estado final de los datos frente al modelo." },
  { tipo: "predicciones", icono: "semaforo", titulo: "Predicciones", texto: "El semáforo y la probabilidad de rojo que el modelo calcula para cada hora, con un resumen general. Solo si los datos son compatibles." },
];

export function iniciarReportes({ alVencer }) {
  let actual = null;                                 // { blob, nombre, csv } del reporte que se está viendo; csv: cómo pedir todas las predicciones
  let versiones = [];

  const falla = (error) => { if (error.estado === 401) alVencer(); return error.message ?? "Ocurrió un error inesperado."; };

  function aviso(tipo, mensaje) {
    const caja = $("rep-aviso");
    if (!mensaje) { caja.hidden = true; return; }
    caja.className = `aviso aviso--${tipo}`;
    reemplazar(caja, tipo === "error" ? icono("aviso") : null, h("p", { style: "margin:0" }, mensaje));
    caja.hidden = false;
  }

  const ocupado = (si) => document.querySelectorAll("#vista-reportes [data-generar]").forEach((b) => { b.disabled = si; });

  // ---------------------------------------------------------------- Ver el reporte en la página
  async function mostrar(titulo, nombre, pedirReporte, csv = null) {
    ocupado(true);
    aviso("cargando", `Preparando el reporte: ${titulo.toLowerCase()}…`);
    try {
      const blob = await pedirReporte();
      actual = { blob, nombre, csv };
      $("rep-csv").hidden = !csv;
      $("rep-marco").srcdoc = await blob.text();
      $("rep-titulo").textContent = titulo;
      $("rep-vista").hidden = false;
      aviso("info", null);
      $("rep-vista").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      aviso("error", falla(error));
    } finally {
      ocupado(false);
    }
  }

  function ajustarAlto() {                           // el reporte se ve completo; si es muy largo, se desplaza dentro del cuadro
    const marco = $("rep-marco");
    try { marco.style.height = `${Math.min(marco.contentDocument.body.offsetHeight + 24, 1500)}px`; } catch { marco.style.height = "70vh"; }
  }
  $("rep-marco").addEventListener("load", ajustarAlto);
  window.addEventListener("resize", () => { if (!$("rep-vista").hidden) ajustarAlto(); });
  $("rep-descargar").addEventListener("click", () => { if (actual) guardarArchivo(actual.blob, actual.nombre); });
  $("rep-csv").addEventListener("click", async () => {
    if (!actual?.csv) return;
    try { guardarArchivo(await actual.csv.pedir(), actual.csv.nombre); } catch (error) { aviso("error", falla(error)); }
  });

  // El PDF se arma en el navegador con el reporte que se ve en pantalla. Las dos librerías se descargan solo al pedir el primero.
  const LIBRERIAS = ["https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js", "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"];
  const cargarScript = (src) => new Promise((ok, fallo) => {
    const s = h("script", { src });
    s.onload = ok; s.onerror = () => fallo(new Error("No se pudo preparar el PDF. Revise su conexión a internet o descargue el reporte en HTML."));
    document.head.append(s);
  });
  const cargarLibrerias = async () => { if (!window.html2canvas) await cargarScript(LIBRERIAS[0]); if (!window.jspdf) await cargarScript(LIBRERIAS[1]); };

  const ANCHO = 794;                                 // 210 mm en píxeles: el reporte se arma con el ancho de una hoja A4
  const MARGEN = 8;                                  // mm arriba y abajo
  async function armarPdf() {
    const copia = h("iframe", { title: "Copia del reporte para el PDF", sandbox: "allow-same-origin", style: `position:fixed;left:0;top:0;width:${ANCHO}px;height:1200px;border:0;z-index:-1;pointer-events:none` });
    document.body.append(copia);
    try {
      await new Promise((ok) => { copia.onload = ok; copia.srcdoc = $("rep-marco").srcdoc; });
      const doc = copia.contentDocument, alto = doc.documentElement.scrollHeight;
      copia.style.height = `${alto}px`;
      const lienzo = await window.html2canvas(doc.body, { scale: 2, windowWidth: ANCHO, windowHeight: alto, scrollX: 0, scrollY: 0, backgroundColor: "#ffffff" });
      const k = lienzo.width / ANCHO;                // píxeles del lienzo por píxel del reporte
      const porPagina = Math.floor(lienzo.width * (297 - 2 * MARGEN) / 210);
      // Dónde se puede cortar sin partir una fila, un párrafo o un cuadro (nunca justo después de un título)
      const cortes = [...doc.querySelectorAll("tr, p, li, figure, .nota, .estado, .dato, .barra")].map((e) => Math.round(e.getBoundingClientRect().bottom * k)).sort((a, b) => a - b);
      const pdf = new window.jspdf.jsPDF({ unit: "mm", format: "a4" });
      for (let desde = 0, primera = true; desde < lienzo.height; primera = false) {
        let hasta = Math.min(desde + porPagina, lienzo.height);
        if (hasta < lienzo.height) hasta = cortes.filter((c) => c > desde + porPagina * 0.5 && c <= hasta).pop() ?? hasta;
        const trozo = document.createElement("canvas");
        trozo.width = lienzo.width; trozo.height = hasta - desde;
        const ctx = trozo.getContext("2d");
        ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, trozo.width, trozo.height);
        ctx.drawImage(lienzo, 0, desde, lienzo.width, hasta - desde, 0, 0, lienzo.width, hasta - desde);
        if (!primera) pdf.addPage();
        pdf.addImage(trozo.toDataURL("image/jpeg", 0.95), "JPEG", 0, MARGEN, 210, (hasta - desde) * 210 / lienzo.width);
        desde = hasta;
      }
      pdf.save(actual.nombre.replace(/\.html$/, ".pdf"));
    } finally {
      copia.remove();
    }
  }
  $("rep-pdf").addEventListener("click", async () => {
    if (!actual) return;
    const boton = $("rep-pdf"), texto = boton.textContent;
    boton.disabled = true; boton.textContent = "Preparando PDF…";
    try {
      await cargarLibrerias();
      await armarPdf();
    } catch (error) {
      aviso("error", error.message ?? "No se pudo generar el PDF.");
    } finally {
      boton.disabled = false; boton.textContent = texto;
    }
  });

  // ---------------------------------------------------------------- Reportes de los datos
  const fuente = () => {
    const v = $("rep-fuente").value;
    return v === "publicados" ? { fuente: "publicados", texto: "Datos publicados", archivo: "datos_publicados" }
      : { fuente: "lote", lote: Number(v), texto: `Lote ${v}`, archivo: `lote_${v}` };
  };

  function pintarTarjetas() {
    reemplazar($("rep-tipos"), DE_DATOS.map((r) => h("article", { clase: "reporte-opcion" },
      h("span", { clase: "icono-circulo" }, icono(r.icono)),
      h("h3", {}, r.titulo), h("p", {}, r.texto),
      h("button", {
        clase: "boton boton--primario boton--pequeno", type: "button", "data-generar": "1",
        onclick: () => {
          const f = fuente();
          const csv = r.tipo === "predicciones" ? { nombre: `predicciones_${f.archivo}.csv`, pedir: () => api.reporteDeDatos("predicciones", f.fuente, f.lote, "csv") } : null;
          mostrar(`${r.titulo} · ${f.texto}`, `reporte_${r.tipo}_${f.archivo}.html`, () => api.reporteDeDatos(r.tipo, f.fuente, f.lote), csv);
        },
      }, "Ver reporte"))));
  }

  async function cargarFuentes() {
    const elegida = $("rep-fuente").value;
    let lista = [];
    try { lista = (await api.lotes()).lotes; } catch (error) { if (error.estado === 401) alVencer(); }      // si falla, igual quedan los datos publicados
    reemplazar($("rep-fuente"),
      h("option", { value: "publicados" }, "Datos publicados (los que usa el modelo)"),
      lista.map((l) => h("option", { value: String(l.id) },
        `Lote ${l.id} · ${l.origen === "archivo" ? "archivo cargado" : "consulta a datos.gov.co"} · ${fecha(l.creado)} · ${numero(l.filas)} filas`)));
    if ([...$("rep-fuente").options].some((o) => o.value === elegida)) $("rep-fuente").value = elegida;
  }

  // ---------------------------------------------------------------- Reporte de actualización del modelo
  function pintarVersiones(activa) {
    const candidatas = versiones.filter((v) => v.version !== activa);
    const opcion = (v) => h("option", { value: v.version }, `${v.version}${v.estado === "candidato" ? " (por decidir)" : v.estado === "activo" ? " (en uso)" : ""}`);
    reemplazar($("rep-candidata"), candidatas.map(opcion));
    reemplazar($("rep-anterior"), versiones.map(opcion));
    $("rep-anterior").value = activa;
    const hay = candidatas.length > 0;
    $("rep-act-vacio").textContent = "Todavía no hay una versión nueva para comparar. Se puede generar cuando se entrene una desde «Actualizar el modelo».";
    $("rep-act-vacio").hidden = hay;
    $("rep-act-form").hidden = !hay;
  }

  $("rep-act-ver").addEventListener("click", () => {
    const candidata = $("rep-candidata").value, anterior = $("rep-anterior").value;
    mostrar(`Actualización del modelo · ${candidata} frente a ${anterior}`, `reporte_actualizacion_${candidata}.html`, () => api.reporteDeActualizacion(anterior, candidata));
  });

  async function cargar() {
    aviso("info", null);
    if (!$("rep-tipos").children.length) pintarTarjetas();
    cargarFuentes();
    try {
      const datos = await api.versionesDelModelo();
      versiones = datos.versiones;
      pintarVersiones(datos.activa);
    } catch (error) {
      reemplazar($("rep-act-vacio"), falla(error));
      $("rep-act-vacio").hidden = false;
      $("rep-act-form").hidden = true;
    }
  }

  return { cargar };
}

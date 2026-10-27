// Gráfica de una línea en el tiempo, dibujada con SVG (sin librerías).
import { h } from "./dom.js";

const ESPACIO = "http://www.w3.org/2000/svg";
const ANCHO = 560, ALTO = 240, M = { izq: 50, der: 14, arr: 14, aba: 32 };

function svg(etiqueta, atributos = {}, texto = null) {
  const nodo = document.createElementNS(ESPACIO, etiqueta);
  for (const [clave, valor] of Object.entries(atributos)) nodo.setAttribute(clave, valor);
  if (texto !== null) nodo.textContent = texto;
  return nodo;
}

/**
 * titulo: texto sobre la gráfica. puntos: [{ t: "2026-10-24 23:00:00", y: número }].
 * Opciones: color, yMin, yMax, formatoY(valor), umbral { valor, etiqueta }.
 */
export function graficaLinea({ titulo, puntos, color = "#0f4c75", yMin = null, yMax = null, formatoY = (v) => String(v), umbral = null }) {
  const datos = puntos.filter((p) => p.y !== null && p.y !== undefined).map((p) => ({ t: new Date(p.t.replace(" ", "T")).getTime(), y: p.y }));
  if (datos.length < 2) return h("div", { clase: "grafica" }, h("h5", {}, titulo), h("p", { clase: "vacio" }, "Todavía no hay suficientes lecturas para dibujar esta gráfica."));

  const ys = datos.map((d) => d.y);
  let min = yMin ?? Math.min(...ys), max = yMax ?? Math.max(...ys);
  if (yMin === null || yMax === null) { const margen = Math.max((max - min) * 0.12, 0.05); min = yMin ?? min - margen; max = yMax ?? max + margen; }
  const t0 = datos[0].t, t1 = datos[datos.length - 1].t;
  const x = (t) => M.izq + ((t - t0) / Math.max(t1 - t0, 1)) * (ANCHO - M.izq - M.der);
  const y = (v) => M.arr + (1 - (v - min) / Math.max(max - min, 1e-9)) * (ALTO - M.arr - M.aba);

  const lienzo = svg("svg", { viewBox: `0 0 ${ANCHO} ${ALTO}`, role: "img", "aria-label": titulo });
  for (let i = 0; i <= 4; i++) {
    const v = min + ((max - min) * i) / 4;
    lienzo.append(svg("line", { x1: M.izq, x2: ANCHO - M.der, y1: y(v), y2: y(v), stroke: "#e3e9ef", "stroke-width": 1 }));
    lienzo.append(svg("text", { x: M.izq - 8, y: y(v) + 4, "text-anchor": "end", "font-size": 11.5, fill: "#556677" }, formatoY(v)));
  }
  for (let i = 0; i <= 3; i++) {
    const t = t0 + ((t1 - t0) * i) / 3;
    lienzo.append(svg("text", { x: x(t), y: ALTO - 9, "text-anchor": i === 0 ? "start" : i === 3 ? "end" : "middle", "font-size": 11.5, fill: "#556677" },
      new Date(t).toLocaleDateString("es-CO", { day: "numeric", month: "short" })));
  }
  if (umbral && umbral.valor >= min && umbral.valor <= max) {
    lienzo.append(svg("line", { x1: M.izq, x2: ANCHO - M.der, y1: y(umbral.valor), y2: y(umbral.valor), stroke: "#e5484d", "stroke-width": 1.6, "stroke-dasharray": "6 5" }));
    lienzo.append(svg("text", { x: ANCHO - M.der, y: y(umbral.valor) - 6, "text-anchor": "end", "font-size": 11.5, "font-weight": 700, fill: "#e5484d" }, umbral.etiqueta));
  }
  const trazo = datos.map((d) => `${x(d.t).toFixed(1)},${y(d.y).toFixed(1)}`).join(" ");
  lienzo.append(svg("polygon", { points: `${x(t0)},${y(min)} ${trazo} ${x(t1)},${y(min)}`, fill: color, "fill-opacity": 0.1 }));
  lienzo.append(svg("polyline", { points: trazo, fill: "none", stroke: color, "stroke-width": 2.2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  return h("div", { clase: "grafica" }, h("h5", {}, titulo), lienzo);
}

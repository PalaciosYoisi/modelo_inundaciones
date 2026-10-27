// Ayudas para construir la página. Todo el texto se inserta como texto (nunca como HTML),
// así lo que venga en un archivo cargado no puede alterar la página.

export function h(etiqueta, atributos = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  for (const [clave, valor] of Object.entries(atributos ?? {})) {
    if (valor === null || valor === undefined || valor === false) continue;
    if (clave === "clase") nodo.className = valor;
    else if (clave.startsWith("on")) nodo.addEventListener(clave.slice(2).toLowerCase(), valor);
    else nodo.setAttribute(clave, valor === true ? "" : valor);
  }
  nodo.append(...hijos.flat(Infinity).filter((x) => x !== null && x !== undefined && x !== false));
  return nodo;
}

export function icono(nombre) {
  const espacio = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(espacio, "svg");
  svg.setAttribute("aria-hidden", "true");
  const uso = document.createElementNS(espacio, "use");
  uso.setAttribute("href", `img/iconos.svg#${nombre}`);
  svg.append(uso);
  return svg;
}

export function reemplazar(contenedor, ...nodos) {
  contenedor.replaceChildren(...nodos.flat(Infinity).filter(Boolean));
}

export const numero = (valor, decimales = 0) =>
  valor === null || valor === undefined || Number.isNaN(Number(valor))
    ? "—"
    : Number(valor).toLocaleString("es-CO", { minimumFractionDigits: decimales, maximumFractionDigits: decimales });

export const porcentaje = (fraccion, decimales = 0) => (fraccion === null || fraccion === undefined ? "—" : `${(fraccion * 100).toFixed(decimales)} %`);

export function fecha(texto, conHora = true) {
  if (!texto) return "—";
  const f = new Date(String(texto).replace(" ", "T"));
  if (Number.isNaN(f.getTime())) return String(texto);
  const opciones = conHora ? { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" } : { day: "numeric", month: "short", year: "numeric" };
  return f.toLocaleString("es-CO", opciones);
}

/** «hace 3 horas», «hace 2 días»… a partir de un número de horas. */
export function hace(horas) {
  if (horas === null || horas === undefined) return "";
  if (horas < 1) return "hace menos de una hora";
  if (horas < 24) return `hace ${Math.round(horas)} ${Math.round(horas) === 1 ? "hora" : "horas"}`;
  const dias = Math.round(horas / 24);
  return `hace ${dias} ${dias === 1 ? "día" : "días"}`;
}

export const titulo = (texto) => String(texto ?? "").toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());

/** Tabla a partir de columnas [{ titulo, valor(fila), numerica }] y una lista de filas. */
export function tabla(columnas, filas) {
  return h("div", { clase: "tabla" },
    h("table", {},
      h("thead", {}, h("tr", {}, columnas.map((c) => h("th", { clase: c.numerica ? "num" : null }, c.titulo)))),
      h("tbody", {}, filas.map((fila) => h("tr", {}, columnas.map((c) => h("td", { clase: c.numerica ? "num" : null }, c.valor(fila))))))));
}

/** Entrega al navegador un archivo ya descargado (un Blob) para que la persona lo guarde. */
export function guardarArchivo(blob, nombre) {
  const enlace = h("a", { href: URL.createObjectURL(blob), download: nombre });
  document.body.append(enlace);
  enlace.click();
  enlace.remove();
  URL.revokeObjectURL(enlace.href);
}

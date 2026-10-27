// Cómo se muestra el análisis exploratorio de un archivo (tablas, observaciones y gráficas).
import { fecha, h, numero, porcentaje, tabla } from "./dom.js";

const dato = (valor, texto) => h("div", { clase: "dato" }, h("b", {}, valor), h("span", {}, texto));

// ------------------------------------------------------------------ Análisis (EDA)
export function vistaAnalisis({ resultado: r, figuras }) {
  return h("div", {},
    h("h3", {}, "Observaciones del sistema"), h("ul", { clase: "notas" }, r.observaciones.map((o) => h("li", {}, o))),
    h("div", { clase: "datos" }, dato(numero(r.resumen.filas), "filas"), dato(numero(r.resumen.columnas), "columnas"),
      dato(numero(r.resumen.filas_duplicadas), "filas duplicadas"), dato(`${numero(r.resumen.porcentaje_celdas_faltantes, 1)} %`, "celdas faltantes")),
    h("h3", {}, "Variables y calidad de los datos"),
    tabla([{ titulo: "Variable", valor: (v) => h("b", {}, v.nombre) }, { titulo: "Tipo detectado", valor: (v) => v.tipo },
      { titulo: "Con dato", numerica: true, valor: (v) => numero(v.datos) },
      { titulo: "Faltantes", numerica: true, valor: (v) => `${numero(v.faltantes)} (${numero(v.porcentaje_faltantes, 1)} %)` },
      { titulo: "Valores distintos", numerica: true, valor: (v) => numero(v.valores_unicos) }], r.variables),
    r.numericas.length ? [
      h("h3", {}, "Variables numéricas"),
      tabla([{ titulo: "Variable", valor: (e) => h("b", {}, e.nombre) }, { titulo: "Media", numerica: true, valor: (e) => numero(e.media, 2) },
        { titulo: "Desv.", numerica: true, valor: (e) => numero(e.desviacion, 2) }, { titulo: "Mín.", numerica: true, valor: (e) => numero(e.minimo, 2) },
        { titulo: "Mediana", numerica: true, valor: (e) => numero(e.mediana, 2) }, { titulo: "Máx.", numerica: true, valor: (e) => numero(e.maximo, 2) },
        { titulo: "Atípicos", numerica: true, valor: (e) => `${numero(e.atipicos.cantidad)} (${numero(e.atipicos.porcentaje, 1)} %)` }], r.numericas),
      h("p", { clase: "nota-suave" }, `Valores atípicos según el criterio ${r.numericas[0].atipicos.criterio}. Un valor atípico no es necesariamente un error.`),
    ] : null,
    r.categoricas.map((c) => [h("h3", {}, `${c.nombre} (${numero(c.unicos)} categorías)`),
      tabla([{ titulo: "Valor", valor: (f) => f.valor }, { titulo: "Registros", numerica: true, valor: (f) => numero(f.cantidad) },
        { titulo: "Proporción", numerica: true, valor: (f) => porcentaje(f.proporcion, 1) }], c.mas_frecuentes)]),
    r.temporales.length ? [h("h3", {}, "Fechas y horas"),
      tabla([{ titulo: "Variable", valor: (t) => h("b", {}, t.nombre) }, { titulo: "Desde", valor: (t) => fecha(t.desde) }, { titulo: "Hasta", valor: (t) => fecha(t.hasta) },
        { titulo: "Días cubiertos", numerica: true, valor: (t) => numero(t.dias_cubiertos, 1) }, { titulo: "Interrupciones", numerica: true, valor: (t) => numero(t.huecos) }], r.temporales)] : null,
    r.relaciones.pares.length ? [h("h3", {}, "Relaciones entre variables"), h("p", { clase: "nota-suave" }, `${r.relaciones.criterio}. ${r.relaciones.aviso}`),
      tabla([{ titulo: "Variable A", valor: (p) => p.a }, { titulo: "Variable B", valor: (p) => p.b },
        { titulo: "Spearman", numerica: true, valor: (p) => numero(p.rho, 2) }], r.relaciones.pares)] : null,
    figuras.length ? [h("h3", {}, "Gráficas"), h("div", { clase: "figuras" }, figuras.map((f) => h("figure", { clase: "figura" },
      h("img", { alt: f.titulo, src: `data:image/png;base64,${f.imagen_base64}` }), h("figcaption", {}, f.titulo))))] : null);
}

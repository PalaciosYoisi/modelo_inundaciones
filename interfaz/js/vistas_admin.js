// Cómo se muestran los resultados del administrador: revisión del archivo, análisis y publicación.
import { h, icono, numero, porcentaje, fecha, titulo, tabla } from "./dom.js";

const dato = (valor, texto) => h("div", { clase: "dato" }, h("b", {}, valor), h("span", {}, texto));
const si = (valor) => (valor ? "Sí" : "No");

// ------------------------------------------------------------------ Revisión (compatibilidad)
const ESTADOS = {
  compatible: { titulo: "El archivo sirve", icono: "check", texto: "Cumple todo lo que el modelo necesita." },
  compatible_con_advertencias: { titulo: "El archivo sirve, con advertencias", icono: "aviso", texto: "Se puede usar, pero hay situaciones que conviene conocer." },
  incompatible: { titulo: "El archivo no sirve todavía", icono: "cruz", texto: "Tiene errores que impiden usarlo de forma segura. Corríjalos y vuelva a cargarlo." },
};
const NIVELES = { esquema: "Columnas", tipo: "Tipo de dato", contenido: "Contenido", dominio: "Valores permitidos" };

function hallazgo(hh) {
  const ejemplos = hh.ejemplos_filas?.length ? ` (por ejemplo, las filas ${hh.ejemplos_filas.join(", ")})` : "";
  return h("article", { clase: `hallazgo hallazgo--${hh.severidad}` },
    h("div", { clase: "hallazgo__cabecera" },
      h("span", { clase: `etiqueta etiqueta--${hh.severidad}` }, hh.severidad === "error" ? "Error" : "Advertencia"),
      h("b", {}, hh.variable), h("small", { clase: "hallazgo__pie" }, `${hh.id} · ${NIVELES[hh.nivel] ?? hh.nivel}`)),
    h("p", {}, hh.mensaje),
    hh.esperado || hh.encontrado ? h("p", { clase: "hallazgo__pie" }, `Se esperaba: ${hh.esperado ?? "—"} · Se encontró: ${hh.encontrado ?? "—"}`) : null,
    hh.filas_afectadas !== null && hh.filas_afectadas !== undefined ? h("p", { clase: "hallazgo__pie" }, `Filas afectadas: ${numero(hh.filas_afectadas)}${ejemplos}`) : null);
}

export function vistaRevision(v) {
  const estado = ESTADOS[v.estado];
  const orden = { error: 0, advertencia: 1 };
  const hallazgos = [...v.hallazgos].sort((a, b) => orden[a.severidad] - orden[b.severidad]);
  return h("div", {},
    h("div", { clase: `estado estado--${v.estado}` }, h("span", { clase: "estado__icono" }, icono(estado.icono)), h("div", {}, h("h3", {}, estado.titulo), h("p", {}, estado.texto))),
    h("div", { clase: "datos" }, dato(numero(v.resumen.filas), "filas recibidas"), dato(numero(v.resumen.errores), "errores"),
      dato(numero(v.resumen.advertencias), "advertencias"), dato(numero(v.resumen.estaciones.length), "estaciones")),
    v.resumen.desde ? h("p", {}, `Periodo recibido: ${fecha(v.resumen.desde)} a ${fecha(v.resumen.hasta)}.`) : null,
    h("h3", {}, "Errores y advertencias"),
    hallazgos.length ? h("div", { clase: "hallazgos" }, hallazgos.map(hallazgo)) : h("p", {}, "No se encontró ningún problema."),
    h("h3", {}, "Columnas esperadas y recibidas"),
    tabla([
      { titulo: "Columna", valor: (c) => h("b", {}, c.nombre) }, { titulo: "¿Está?", valor: (c) => si(c.presente) },
      { titulo: "Tipo esperado", valor: (c) => c.tipo_esperado }, { titulo: "Tipo recibido", valor: (c) => c.tipo_recibido ?? "—" },
      { titulo: "Vacíos", numerica: true, valor: (c) => (c.presente ? `${numero(c.vacios)} (${numero(c.porcentaje_vacios, 1)} %)` : "—") },
    ], v.columnas),
    v.resumen.columnas_ignoradas?.length ? h("p", { clase: "nota-suave" }, `Columnas que el modelo no usa y se ignoran: ${v.resumen.columnas_ignoradas.join(", ")}.`) : null,
    h("p", { clase: "nota-suave" }, "El sistema no corrige ni reemplaza datos para forzar un resultado. Las filas se numeran como en el archivo (la fila 1 es el encabezado)."));
}

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

// ------------------------------------------------------------------ Publicación
const COLOR = { Verde: "verde", Amarillo: "amarillo", Rojo: "rojo" };

export function vistaPublicacion(r, { alDescargar }) {
  const c = r.cambios;
  return h("div", {},
    h("div", { clase: "estado estado--publicado" }, h("span", { clase: "estado__icono" }, icono("check")),
      h("div", {}, h("h3", {}, "Datos publicados"), h("p", {}, "Los semáforos que ve el público ya se actualizaron."))),
    h("div", { clase: "datos" }, dato(numero(c.filas_recibidas), "lecturas recibidas"), dato(numero(c.agregadas), "lecturas nuevas"),
      dato(numero(c.actualizadas), "horas que ya estaban y se actualizaron"), dato(numero(c.estaciones.length), "estaciones con datos nuevos")),
    c.descartadas_por_antiguas ? h("p", { clase: "nota-suave" }, `${numero(c.descartadas_por_antiguas)} lecturas del archivo son de hace más de 120 días y no se guardaron: el sistema solo conserva los últimos 120 días de cada estación.`) : null,
    r.validacion.hallazgos.length ? h("p", { clase: "nota-suave" }, `La revisión dejó ${r.validacion.hallazgos.length} advertencia(s); puede verlas en la pestaña «Revisión».`) : null,
    h("h3", {}, "Semáforos después de publicar"),
    tabla([{ titulo: "Estación", valor: (e) => h("span", {}, h("b", {}, e.nombre), h("br"), h("small", {}, `${e.municipio} · ${e.codigo}`)) },
      { titulo: "Última lectura", valor: (e) => fecha(e.ultima_lectura) },
      { titulo: "Posibilidad de peligro", numerica: true, valor: (e) => porcentaje(e.prob_rojo) },
      { titulo: "Semáforo", valor: (e) => (e.semaforo ? h("span", { clase: `etiqueta etiqueta--${COLOR[e.semaforo]}` }, e.semaforo) : h("span", { clase: "etiqueta etiqueta--gris" }, "Sin datos")) },
      { titulo: "Datos", valor: (e) => (e.desactualizada ? h("span", { clase: "etiqueta etiqueta--gris" }, "No recientes") : "Recientes") }], r.estaciones),
    h("div", { clase: "descarga" }, h("p", {}, "También puede descargar las predicciones hora por hora de este archivo."),
      h("button", { clase: "boton boton--contorno-oscuro boton--pequeno", type: "button", onclick: alDescargar }, icono("descarga"), "Descargar predicciones (CSV)")));
}

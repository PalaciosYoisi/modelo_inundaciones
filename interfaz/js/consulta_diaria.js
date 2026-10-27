// Consulta diaria: los lotes de datos nuevos de datos.gov.co que el administrador revisa antes de enviarlos al modelo.
import * as api from "./api.js";
import { fecha, guardarArchivo, h, icono, numero, reemplazar } from "./dom.js";
import { graficaLinea } from "./grafica.js";

const $ = (id) => document.getElementById(id);

const ESTADOS = {
  pendiente: { texto: "Esperando revisión", clase: "amarillo", banner: "aviso", titulo: "Hay datos nuevos esperando su decisión",
    detalle: "Revíselos. Si los confirma, pasan al modelo y los semáforos que ve el público se actualizan." },
  con_errores: { texto: "Con errores", clase: "rojo", banner: "incompatible", titulo: "Estos datos traen errores",
    detalle: "No se pueden confirmar. Espere la próxima consulta o revise el origen de los datos." },
  confirmado: { texto: "Confirmado", clase: "verde", banner: "publicado", titulo: "Lote confirmado", detalle: "Estos datos ya llegaron al modelo." },
  rechazado: { texto: "Rechazado", clase: "gris", banner: "gris", titulo: "Lote rechazado", detalle: "Estos datos no llegaron al modelo." },
  unificado: { texto: "Unificado", clase: "gris", banner: "gris", titulo: "Lote unificado", detalle: "Sus datos se combinaron con los de una consulta más nueva." },
};
const DESCARTES = {
  nivel_cero_o_negativo: "niveles en cero o negativos", nivel_fuera_de_rango: "niveles fuera de lo posible para la estación",
  lluvia_negativa: "lecturas de lluvia negativas", lluvia_dias_de_mas_de_500_mm: "lecturas de lluvia de días con más de 500 mm",
  picos_de_una_hora: "picos de una sola hora (falla del sensor)",
};
const suma = (lista, campo) => lista.reduce((total, e) => total + (e[campo] ?? 0), 0);
const dato = (valor, texto) => h("div", { clase: "dato" }, h("b", {}, valor), h("span", {}, texto));

export function iniciarConsulta({ alVencer, alCambiar, alRevisar }) {
  let nombres = {};
  let abierto = null;                               // el lote que se está revisando
  let estacion = null;
  let forma = "graficas";
  let pregunta = null;                              // «confirmar» o «rechazar» mientras se espera el sí
  const nombreDe = (codigo) => nombres[codigo] ?? codigo;
  const falla = (error) => { if (error.estado === 401) alVencer(); return error.message; };

  function aviso(tipo, mensaje) {
    const caja = $("cons-aviso");
    caja.className = `aviso aviso--${tipo}`;
    reemplazar(caja, tipo === "error" ? icono("aviso") : null, h("p", { style: "margin:0" }, mensaje));
    caja.hidden = false;
  }

  // ---------------------------------------------------------------- Aviso de lotes pendientes
  function marcar(pendientes) {
    $("insignia-consulta").hidden = !pendientes;
    $("insignia-consulta").textContent = pendientes;
    $("aviso-lote").hidden = !pendientes;
  }

  async function actualizarAviso() {
    try { marcar((await api.lotes()).pendientes); } catch (error) { falla(error); }
  }

  // ---------------------------------------------------------------- Lista de lotes
  function tablaLotes(lista) {
    if (!lista.length) return h("p", { clase: "vacio" }, "Todavía no hay consultas. Use «Consultar ahora» o programe la consulta diaria.");
    const fila = (l) => h("tr", {},
      h("td", {}, h("b", {}, `Lote ${l.id}`)), h("td", {}, fecha(l.creado)),
      h("td", {}, h("span", { clase: `etiqueta etiqueta--${ESTADOS[l.estado]?.clase ?? "gris"}` }, ESTADOS[l.estado]?.texto ?? l.estado)),
      h("td", { clase: "num" }, numero(l.filas)), h("td", {}, `${fecha(l.desde, false)} a ${fecha(l.hasta, false)}`),
      h("td", {}, l.decidido_por ?? "—"),
      h("td", {}, h("button", { clase: "boton boton--contorno-oscuro boton--pequeno", type: "button", onclick: () => abrir(l.id) }, "Ver")));
    return h("div", { clase: "tabla" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Lote", "Consultado", "Estado", "Horas", "Periodo", "Decidió", ""].map((t, i) => h("th", { clase: i === 3 ? "num" : null }, t)))),
      h("tbody", {}, lista.map(fila))));
  }

  async function cargar() {
    try {
      if (!Object.keys(nombres).length) nombres = Object.fromEntries((await api.estaciones()).estaciones.map((e) => [e.codigo, e.nombre]));
    } catch { /* si falla, se muestran los códigos */ }
    try {
      const { lotes, pendientes } = await api.lotes();
      marcar(pendientes);
      reemplazar($("cons-lista"), tablaLotes(lotes));
      const pendiente = lotes.find((l) => l.estado === "pendiente");
      if (!abierto && pendiente) await abrir(pendiente.id);
    } catch (error) {
      reemplazar($("cons-lista"), h("p", { clase: "vacio" }, falla(error)));
    }
  }

  // ---------------------------------------------------------------- Revisión de un lote
  async function abrir(id) {
    try {
      abierto = await api.lote(id);
    } catch (error) {
      aviso("error", falla(error));
      return;
    }
    pregunta = null;
    estacion = abierto.resumen.por_estacion?.[0]?.estacion ?? null;
    $("cons-detalle").hidden = false;
    pintar();
    $("cons-detalle").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function hallazgos(lista) {
    if (!lista?.length) return h("p", {}, "La revisión automática no encontró ningún problema.");
    return h("div", { clase: "hallazgos" }, [...lista].sort((a, b) => (a.severidad === "error" ? 0 : 1) - (b.severidad === "error" ? 0 : 1)).map((x) => h("article", { clase: `hallazgo hallazgo--${x.severidad}` },
      h("div", { clase: "hallazgo__cabecera" }, h("span", { clase: `etiqueta etiqueta--${x.severidad}` }, x.severidad === "error" ? "Error" : "Advertencia"), h("b", {}, x.variable)),
      h("p", {}, x.mensaje))));
  }

  // Cuando una consulta nueva llega con un lote ya pendiente, se combinan; aquí se deja claro qué se combinó y en qué se parecen
  function combinado(comparacion) {
    if (!comparacion?.length) return null;
    const lista = comparacion.map((c) => `lote ${c.lote} (consultado el ${fecha(c.consultado)})`).join(" y ");
    return h("div", { clase: "combinado" },
      h("h3", {}, "Este lote combina varias consultas"),
      h("p", {}, `Reúne los datos de la consulta más reciente con los del ${lista}, que estaban esperando revisión. Si una misma hora aparece en las dos, se usó el valor de la consulta más reciente.`),
      h("div", { clase: "tabla" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Lote anterior", "Horas que tenía", "Iguales en ambas", "Con valor distinto", "Solo en el anterior", "Solo en la consulta nueva"].map((t, i) => h("th", { clase: i ? "num" : null }, t)))),
        h("tbody", {}, comparacion.map((c) => h("tr", {}, h("td", {}, h("b", {}, `Lote ${c.lote}`)), [c.horas, c.iguales, c.distintas, c.solo_en_anterior, c.solo_en_nueva].map((n) => h("td", { clase: "num" }, numero(n)))))))));
  }

  function descartes(d) {
    const partes = Object.entries(d ?? {}).filter(([, n]) => n > 0).map(([k, n]) => `${numero(n)} ${DESCARTES[k] ?? k}`);
    return h("p", { clase: "nota-suave" }, partes.length ? `El sistema descartó automáticamente: ${partes.join(", ")}.` : "El sistema no tuvo que descartar ninguna lectura.");
  }

  function datosDeEstacion() {
    const filas = abierto.diario.filter((d) => d.estacion === estacion);
    if (forma === "tabla") {
      return h("div", { clase: "tabla tabla--alta" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Día"), h("th", { clase: "num" }, "Nivel del río (m)"), h("th", { clase: "num" }, "Lluvia del día (mm)"), h("th", { clase: "num" }, "Horas con datos"))),
        h("tbody", {}, [...filas].reverse().map((d) => h("tr", {}, h("td", {}, fecha(d.dia, false)),
          h("td", { clase: "num" }, d.nivel_min === null ? "—" : `${numero(d.nivel_min, 2)} a ${numero(d.nivel_max, 2)}`),
          h("td", { clase: "num" }, numero(d.lluvia_mm, 1)), h("td", { clase: "num" }, numero(d.horas)))))));
    }
    return h("div", { clase: "graficas" },
      graficaLinea({ titulo: "Nivel del río, máximo de cada día (metros)", color: "#0f4c75", formatoY: (v) => numero(v, 1),
        puntos: filas.map((d) => ({ t: `${d.dia} 12:00:00`, y: d.nivel_max })) }),
      graficaLinea({ titulo: "Lluvia de cada día (milímetros)", color: "#0d97ab", yMin: 0, formatoY: (v) => numero(v, 0),
        puntos: filas.map((d) => ({ t: `${d.dia} 12:00:00`, y: d.lluvia_mm })) }));
  }

  function acciones(l, nuevas, actualizadas) {
    if (pregunta) {
      const texto = pregunta === "confirmar"
        ? `Se agregarán ${numero(nuevas)} horas nuevas, se completarán ${numero(actualizadas)} y los semáforos que ve el público se actualizarán. ¿Confirma?`
        : "El lote se descartará y no llegará al modelo. ¿Lo rechaza?";
      return h("div", { clase: "confirmar", role: "alert" }, h("p", {}, texto + (l.resumen.combina?.length ? " Recuerde que este lote combina varias consultas." : "")),
        h("button", { clase: "boton boton--contorno-oscuro boton--pequeno", type: "button", onclick: () => { pregunta = null; pintar(); } }, "Cancelar"),
        h("button", { clase: "boton boton--primario boton--pequeno", type: "button", onclick: () => decidir(pregunta) }, pregunta === "confirmar" ? "Sí, confirmar" : "Sí, rechazar"));
    }
    return h("div", { clase: "acciones-lote" },
      h("button", { clase: "boton boton--contorno-oscuro", type: "button", onclick: descargar }, icono("descarga"), "Descargar lote (CSV)"),
      l.estado === "pendiente" ? [
        h("button", { clase: "boton boton--contorno-oscuro", type: "button", onclick: () => { pregunta = "rechazar"; pintar(); } }, "Rechazar"),
        h("button", { clase: "boton boton--primario", type: "button", onclick: () => { pregunta = "confirmar"; pintar(); } }, icono("check"), "Confirmar y enviar al modelo"),
      ] : null);
  }

  function pintar() {
    const l = abierto, r = l.resumen, e = ESTADOS[l.estado] ?? ESTADOS.rechazado;
    const porEstacion = r.por_estacion ?? [];
    const nuevas = suma(porEstacion, "horas_nuevas"), actualizadas = suma(porEstacion, "horas_actualizadas");
    const zona = h("div", {});
    const pintarZona = () => reemplazar(zona, datosDeEstacion());
    const selector = h("select", { id: "cons-estacion", onchange: (ev) => { estacion = ev.target.value; pintarZona(); } },
      porEstacion.map((x) => h("option", { value: x.estacion, selected: x.estacion === estacion }, nombreDe(x.estacion))));
    const botones = {};
    for (const [clave, texto] of [["graficas", "Ver gráficas"], ["tabla", "Ver tabla"]]) {
      botones[clave] = h("button", { type: "button", "aria-pressed": String(clave === forma), onclick: () => {
        forma = clave;
        Object.entries(botones).forEach(([k, b]) => b.setAttribute("aria-pressed", String(k === forma)));
        pintarZona();
      } }, texto);
    }
    pintarZona();
    reemplazar($("cons-detalle"),
      h("div", { clase: "cabecera-lote" }, h("h2", {}, `Lote ${l.id}`), h("span", { clase: `etiqueta etiqueta--${e.clase}` }, e.texto)),
      h("p", { clase: "tarjeta__texto" }, `Consultado el ${fecha(l.creado)}${l.decidido_por ? ` · Decidió: ${l.decidido_por}, el ${fecha(l.decidido_en)}` : ""}`),
      h("div", { clase: `estado estado--${e.banner}` }, h("span", { clase: "estado__icono" }, icono(l.estado === "con_errores" ? "cruz" : l.estado === "pendiente" ? "aviso" : "check")),
        h("div", {}, h("h3", {}, e.titulo), h("p", {}, e.detalle))),
      h("div", { clase: "datos" }, dato(numero(l.filas), "lecturas por hora"), dato(numero(porEstacion.length), "estaciones con datos"),
        dato(numero(nuevas), "horas nuevas"), dato(numero(actualizadas), "horas que se completan")),
      l.estado === "unificado" && r.unificado_en
        ? h("p", {}, h("button", { clase: "boton boton--contorno-oscuro boton--pequeno", type: "button", onclick: () => abrir(r.unificado_en) }, `Ver el lote ${r.unificado_en}, que lo reemplaza`)) : null,
      combinado(r.combina),
      h("p", {}, `Periodo: ${fecha(l.desde, false)} a ${fecha(l.hasta, false)}. Se consultó nivel y lluvia desde ${fecha(r.consulta?.nivel_desde, false)}.`),
      h("h3", {}, "Revisión automática"), hallazgos(r.validacion?.hallazgos), descartes(r.descartes),
      r.estaciones_sin_datos?.length ? h("p", { clase: "nota-suave" }, `Sin datos nuevos en: ${r.estaciones_sin_datos.map(nombreDe).join(", ")}.`) : null,
      h("h3", {}, "Datos por estación"),
      h("div", { clase: "lote-estacion" }, h("label", { for: "cons-estacion" }, "Estación"), selector, h("div", { clase: "conmutador", role: "group", "aria-label": "Forma de ver los datos" }, Object.values(botones))),
      zona,
      acciones(l, nuevas, actualizadas));
  }

  // ---------------------------------------------------------------- Decidir y descargar
  async function decidir(que) {
    const id = abierto.id;
    pregunta = null;
    aviso("cargando", que === "confirmar" ? "Enviando los datos al modelo…" : "Rechazando el lote…");
    try {
      if (que === "confirmar") { await api.confirmarLote(id); aviso("info", "Lote confirmado. Los semáforos que ve el público ya se actualizaron."); alCambiar(); }
      else { await api.rechazarLote(id); aviso("info", "Lote rechazado. No llegó al modelo."); }
    } catch (error) {
      aviso("error", falla(error));
    }
    await cargar();
    await abrir(id);
  }

  async function descargar() {
    try {
      guardarArchivo(await api.descargarLote(abierto.id), `lote_${abierto.id}.csv`);
    } catch (error) {
      aviso("error", falla(error));
    }
  }

  $("cons-ahora").addEventListener("click", async () => {
    const boton = $("cons-ahora");
    boton.disabled = true;
    aviso("cargando", "Consultando datos.gov.co. Puede tardar hasta un minuto…");
    try {
      const r = await api.consultarAhora();
      aviso(r.estado === "error" ? "error" : "info", r.mensaje);
      abierto = null;
      await cargar();
      if (r.lote && abierto?.id !== r.lote) await abrir(r.lote);
    } catch (error) {
      aviso("error", falla(error));
    } finally {
      boton.disabled = false;
    }
  });
  $("ir-a-consulta").addEventListener("click", alRevisar);

  return { cargar, actualizarAviso };
}

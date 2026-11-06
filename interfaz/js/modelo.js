// Actualizar el modelo: entrenar una versión nueva con todos los datos, seguir su avance, compararla con la que está en uso y decidir.
import * as api from "./api.js";
import { fecha, guardarArchivo, h, icono, numero, porcentaje, reemplazar } from "./dom.js";

const $ = (id) => document.getElementById(id);
const ESTADOS = { activo: ["En uso", "verde"], candidato: ["Por decidir", "amarillo"], historico: ["Anterior", "gris"], descartada: ["Descartada", "gris"] };
const ETIQUETAS_RAZON = { contra: "En contra", cuidado: "Con cuidado", favor: "A favor", nota: "Para tener en cuenta" };
const ALGORITMOS = { RandomForestClassifier: "Bosque aleatorio" };
const CADA = 3000;                                  // cada cuántos milisegundos se pregunta cómo va la actualización
const MIN_JUSTIFICACION = 10;
const mayuscula = (texto) => texto.charAt(0).toUpperCase() + texto.slice(1);
const estadoVisible = (v) => (v.estado === "historico" && v.decision === "descartada" ? "descartada" : v.estado);      // se guarda como histórica, pero se ve como descartada
const etiqueta = (estado) => h("span", { clase: `etiqueta etiqueta--${ESTADOS[estado]?.[1] ?? "gris"}` }, ESTADOS[estado]?.[0] ?? estado);
const dato = (valor, texto) => h("div", { clase: "dato" }, h("b", {}, valor), h("span", {}, texto));

// [nombre en los metadatos, cómo se lee, cómo se muestra, ¿más es mejor?]
const METRICAS = [
  ["Color acertado", "Veces que el color del semáforo coincidió con lo que pasó", "porcentaje", true],
  ["Rojos detectados", "Crecidas (rojo) que el modelo avisó", "porcentaje", true],
  ["Alertas rojas correctas", "Alertas rojas que de verdad eran crecidas", "porcentaje", true],
  ["F1 del rojo", "Puntaje del rojo (de 0 a 1; 1 sería perfecto)", "decimal", true],
  ["Rojos no detectados", "Crecidas que el modelo no avisó", "entero", false],
  ["Falsas alarmas", "Alertas rojas que no eran crecidas", "entero", false],
];
const mostrarValor = (valor, forma) => (valor === null || valor === undefined ? "—" : forma === "porcentaje" ? porcentaje(valor) : numero(valor, forma === "decimal" ? 2 : 0));

export function iniciarModelo({ alVencer, alCambiar }) {
  let siguiendo = false;
  let activa = null;                                // la versión que está en uso
  let advertido = false;                            // ya se avisó que la decisión va contra lo que aconseja el sistema
  const falla = (error) => { if (error.estado === 401) alVencer(); return error.message; };

  function aviso(tipo, mensaje, id = "mod-aviso") {
    const caja = $(id);
    caja.className = `aviso aviso--${tipo}`;
    reemplazar(caja, tipo === "error" ? icono("aviso") : null, h("p", { style: "margin:0" }, mensaje));
    caja.hidden = false;
  }

  // ---------------------------------------------------------------- ¿Conviene actualizar? Se mira lo guardado, no el calendario
  function tablaEstaciones(lista) {
    const pct = (x) => (x === null || x === undefined ? "—" : porcentaje(x));
    return h("div", { clase: "tabla" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Estación", "Horas nuevas con nivel", "Nivel completo", "Lluvia completa", "Último dato", "Horas en rojo"].map((t, i) => h("th", { clase: i === 1 || i === 5 ? "num" : null }, t)))),
      h("tbody", {}, lista.map((e) => h("tr", {}, h("td", {}, h("b", {}, e.nombre)), h("td", { clase: "num" }, numero(e.horas_con_nivel)), h("td", {}, pct(e.cobertura_nivel)),
        h("td", {}, pct(e.cobertura_lluvia)), h("td", {}, e.ultimo_dato ? fecha(e.ultimo_dato) : "Sin datos nuevos"), h("td", { clase: "num" }, numero(e.horas_en_rojo)))))));
  }

  async function cargarSugerencia(medir = false) {
    const caja = $("mod-sugerencia"), titulo = h("h2", {}, "¿Conviene actualizar el modelo?");
    const boton = $("mod-medir");
    if (medir && boton) { boton.disabled = true; boton.textContent = "Midiendo…"; }
    try {
      const s = await api.sugerenciaDeActualizacion(medir), d = s.datos;
      reemplazar(caja, titulo,
        h("p", { clase: "tarjeta__texto" }, `El sistema mira la información guardada desde el último entrenamiento (${fecha(d.desde, false)}): cuánta hay, qué tan completa es y si trae crecidas. No actualiza nada por sí solo.`),
        h("div", { clase: "datos" }, dato(numero(d.horas_con_nivel), "horas nuevas con nivel"), dato(numero(d.ejemplos_nuevos), `ejemplos nuevos (${numero(d.proporcion * 100, 1)} % de los que ya usó el modelo)`),
          dato(numero(d.lotes_confirmados), "lotes confirmados"), dato(numero(d.horas_en_rojo), "horas en rojo (crecidas)")),
        recomendacion(s),
        h("details", {}, h("summary", {}, "Ver cada estación"), tablaEstaciones(d.por_estacion)),
        h("div", { clase: "acciones" }, h("button", { id: "mod-medir", clase: "boton boton--contorno-oscuro boton--pequeno", type: "button", onclick: () => cargarSugerencia(true) },
          medir ? "Volver a medir" : "Medir cómo le va a la versión en uso con los datos nuevos")));
    } catch (error) {
      reemplazar(caja, titulo, h("p", { clase: "vacio" }, falla(error)));
    }
  }

  // ---------------------------------------------------------------- Actualización en curso
  function mostrarAvance(e) {
    const caja = $("mod-avance");
    caja.hidden = e.estado !== "en_curso";
    if (e.estado !== "en_curso") return;
    const porcentajeHecho = Math.round((e.avance ?? 0) * 100);
    reemplazar(caja,
      h("div", { clase: "avance__texto" }, h("span", {}, mayuscula(e.etapa ?? "Preparando")), h("span", {}, `${porcentajeHecho} %`)),
      h("div", { clase: "avance__fondo", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": porcentajeHecho },
        h("div", { clase: "avance__barra", style: `width:${porcentajeHecho}%` })));
  }

  function mostrar(e) {
    mostrarAvance(e);
    const sinDatosNuevos = e.estado === "fallido" && e.mensaje?.startsWith("No hay datos nuevos");
    $("mod-actualizar").disabled = e.estado === "en_curso";
    $("mod-forzar").hidden = !sinDatosNuevos;
    if (e.estado === "en_curso") aviso("cargando", "Actualizando el modelo. Puede tardar varios minutos; mientras tanto puede seguir usando el panel.");
    else if (e.estado === "terminado") aviso("info", `${e.mensaje} Revise la comparación que aparece abajo antes de decidir.`);
    else if (sinDatosNuevos) aviso("error", `${e.mensaje} «Entrenar de todos modos» sirve solo para pruebas: la versión que salga no tendrá información nueva.`);
    else if (e.estado === "fallido") aviso("error", e.mensaje);
    else $("mod-aviso").hidden = true;
  }

  async function seguir() {
    if (siguiendo) return;
    siguiendo = true;
    try {
      let e;
      do {
        e = await api.avanceDeActualizacion();
        mostrar(e);
        if (e.estado === "en_curso") await new Promise((seguir) => setTimeout(seguir, CADA));
      } while (e.estado === "en_curso");
      if (e.estado === "terminado") {
        await cargarVersiones();
        if (e.resultado?.version) await abrir(e.resultado.version);
      }
    } catch (error) {
      aviso("error", falla(error));
    } finally {
      siguiendo = false;
    }
  }

  async function actualizar(forzar) {
    $("mod-actualizar").disabled = true;
    $("mod-forzar").hidden = true;
    $("mod-detalle").hidden = true;
    try {
      await api.actualizarModelo(forzar);
    } catch (error) {
      aviso("error", falla(error));
      if (error.codigo === "ACTUALIZACION_EN_CURSO") seguir(); else $("mod-actualizar").disabled = false;
      return;
    }
    seguir();
  }

  // ---------------------------------------------------------------- Versiones
  function tablaVersiones(lista) {
    if (!lista.length) return h("p", { clase: "vacio" }, "Todavía no hay versiones guardadas.");
    const fila = (v) => h("tr", {},
      h("td", {}, h("b", {}, v.version)),
      h("td", {}, etiqueta(estadoVisible(v))),
      h("td", {}, fecha(v.creado)),
      h("td", {}, fecha(v.periodo_datos?.[1], false)),
      h("td", { clase: "num" }, numero(v.registros)),
      h("td", { clase: "num" }, numero(v.f1_rojo, 2)),
      h("td", {}, v.decidido_por ? [h("b", {}, v.decidido_por), h("br"), h("small", {}, v.justificacion)] : "—"),
      h("td", {}, h("button", { clase: "boton boton--contorno-oscuro boton--pequeno", type: "button", onclick: () => abrir(v.version) },
        v.estado === "candidato" ? "Comparar y decidir" : v.estado === "activo" ? "Ver ficha" : "Ver")));
    return h("div", { clase: "tabla" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Versión", "Estado", "Entrenada", "Datos hasta", "Ejemplos usados", "Puntaje del rojo", "Decisión", ""].map((t, i) => h("th", { clase: i === 4 || i === 5 ? "num" : null }, t)))),
      h("tbody", {}, lista.map(fila))));
  }

  async function cargarVersiones() {
    try {
      const datos = await api.versionesDelModelo();
      activa = datos.activa;
      reemplazar($("mod-versiones"), tablaVersiones(datos.versiones));
    } catch (error) {
      reemplazar($("mod-versiones"), h("p", { clase: "vacio" }, falla(error)));
    }
  }

  // ---------------------------------------------------------------- Detalle de una versión
  const metricasDe = (version) => version.metadatos?.metricas_validacion?.["Bosque aleatorio"] ?? {};

  // Qué aconseja el sistema y por qué (solo aconseja: decide el administrador)
  function recomendacion(r) {
    if (!r) return null;
    return h("div", { clase: `recomendacion recomendacion--${r.accion}` },
      h("h3", {}, r.titulo), h("p", {}, r.texto),
      h("ul", { clase: "razones" }, r.razones.map((x) => h("li", {}, h("span", { clase: `razon razon--${x.tipo}` }, ETIQUETAS_RAZON[x.tipo]), h("span", {}, x.texto)))));
  }

  // Una versión descartada se conserva, con quién la descartó y por qué
  function descartada(v) {
    if (v.decision !== "descartada") return null;
    return h("div", { clase: "aviso", style: "margin:0 0 1.2rem" }, icono("aviso"),
      h("p", { style: "margin:0" }, h("b", {}, `Esta versión se descartó el ${fecha(v.decidido_en)}, por ${v.decidido_por}. `), `Motivo: ${v.justificacion} `,
        "Se conserva guardada para poder comparar y, si hiciera falta, volver a usarla."));
  }

  function cambio(antes, despues, masEsMejor) {
    if (antes === undefined || despues === undefined) return h("span", { clase: "etiqueta etiqueta--gris" }, "—");
    const diferencia = despues - antes;
    if (Math.abs(diferencia) < 0.005) return h("span", { clase: "etiqueta etiqueta--gris" }, "Igual");
    return (diferencia > 0) === masEsMejor ? h("span", { clase: "etiqueta etiqueta--verde" }, "Mejora") : h("span", { clase: "etiqueta etiqueta--rojo" }, "Empeora");
  }

  function comparacion(uso, elegida) {
    const antes = metricasDe(uso), despues = metricasDe(elegida);
    return [
      h("h3", {}, "Comparación con la versión en uso"),
      h("div", { clase: "tabla" }, h("table", {},
        h("thead", {}, h("tr", {}, [["Qué se mide"], [`Versión ${uso.version} (en uso)`, true], [`Versión ${elegida.version}`, true], ["Cambio"]].map(([t, num]) => h("th", { clase: num ? "num" : null }, t)))),
        h("tbody", {}, METRICAS.map(([clave, texto, forma, masEsMejor]) => h("tr", {},
          h("td", {}, texto),
          h("td", { clase: "num" }, mostrarValor(antes[clave], forma)),
          h("td", { clase: "num" }, mostrarValor(despues[clave], forma)),
          h("td", {}, cambio(antes[clave], despues[clave], masEsMejor))))))),
      h("p", { clase: "tarjeta__texto", style: "margin:.8rem 0 0" },
        "Ojo: las dos versiones se probaron con periodos distintos, porque la nueva ya incluye los datos más recientes. Por eso esta tabla es una guía y no una prueba exacta."),
    ];
  }

  // Cómo le fue a la versión que estaba en uso con los datos que llegaron después de entrenarla
  function datosNuevos(elegida, uso) {
    const r = elegida.metadatos.reentrenamiento;
    if (!r?.version_anterior) return null;
    const titulo = h("h3", {}, `Cómo le fue a la versión ${r.version_anterior} con los datos nuevos`);
    if (!("modelo_anterior_en_datos_nuevos" in r)) return [titulo, h("p", { clase: "tarjeta__texto" }, "Esta versión se entrenó antes de que el sistema guardara este dato.")];
    const e = r.modelo_anterior_en_datos_nuevos;
    const intro = `Son las horas posteriores al ${fecha(r.datos_nuevos_desde, false)}, que esa versión nunca vio al entrenarse. Es la mejor señal de si sigue funcionando con lo que pasa hoy.`;
    if (!e) return [titulo, h("p", { clase: "tarjeta__texto" }, `${intro} Todavía hay muy pocas (se necesitan al menos 24 horas) para medirlo.`)];
    const hubo = e.con_rojo > 0;
    const original = uso.metadatos?.metrica_principal?.valor_modelo;
    return [titulo, h("p", { clase: "tarjeta__texto" }, intro),
      h("div", { clase: "datos" },
        dato(numero(e.momentos), "horas analizadas"),
        dato(numero(e.con_rojo), "veces que llegó una crecida en las 24 horas siguientes"),
        dato(hubo ? porcentaje(e["Rojos detectados"]) : "—", "de esas crecidas las avisó"),
        dato(numero(e["Falsas alarmas"]), "falsas alarmas"),
        dato(hubo ? numero(e["F1 del rojo"], 2) : "—", "puntaje del rojo")),
      h("p", { clase: "tarjeta__texto", style: "margin:0" }, !hubo
        ? "En esos días no hubo crecidas, así que no se puede comprobar si el modelo las avisa; solo se ve cuántas falsas alarmas dio."
        : original !== undefined && e["F1 del rojo"] < original - 0.1
          ? `Cuando se entrenó tenía un puntaje de ${numero(original, 2)} y ahora tiene ${numero(e["F1 del rojo"], 2)}: está fallando más con los datos recientes, y por eso conviene renovarla.`
          : `Cuando se entrenó tenía un puntaje de ${numero(original, 2)} y con los datos nuevos tiene ${numero(e["F1 del rojo"], 2)}: sigue funcionando de forma parecida.`)];
  }

  // Lo mínimo que se guarda de cada versión (apartado 9 de la actividad)
  function ficha(elegida) {
    const meta = elegida.metadatos, config = meta.configuracion ?? {}, variables = meta.variables_modelo ?? [], periodo = meta.periodo_datos ?? [];
    const filas = [
      ["Versión", h("b", {}, elegida.version)],
      ["Estado", etiqueta(estadoVisible(elegida))],
      ["Fecha y hora de entrenamiento", fecha(meta.fecha_entrenamiento)],
      ["Registros utilizados", `${numero(meta.registros_entrenamiento)} ejemplos (una hora de cada ${config.paso_horas_entrenamiento ?? 3}), con datos del ${fecha(periodo[0], false)} al ${fecha(periodo[1], false)}`],
      ["Variables utilizadas", [`${variables.length} variables: el nivel del río, la lluvia, la hora y la época del año y la estación. `,
        h("details", {}, h("summary", {}, "Ver la lista"), h("p", { style: "margin:.6rem 0 0;color:var(--texto-suave);font-size:.88rem;line-height:1.7" }, variables.join(", ")))]],
      ["Algoritmo y configuración", `${ALGORITMOS[meta.algoritmo] ?? meta.algoritmo}: ${config.n_estimators} árboles, profundidad máxima ${config.max_depth}, al menos ${config.min_samples_leaf} ejemplos por hoja. `
        + `Avisa rojo desde ${porcentaje(config.umbral_probabilidad_rojo)} de probabilidad y mira ${config.horizonte_horas} horas hacia adelante.`],
      ["Resultado en la validación", `Puntaje del rojo ${numero(meta.metrica_principal?.valor_modelo, 2)}, probado por bloques de tiempo (cada bloque se predice solo con lo anterior)`],
      ["Archivo del modelo", `${meta.archivo_modelo} (guardado en la base de datos)`],
      ["Decisión", elegida.decidido_por ? `${elegida.decision === "descartada" ? "Descartada" : elegida.decision === "activada" ? "Puesta en uso" : "Decisión"} por ${elegida.decidido_por}, el ${fecha(elegida.decidido_en)}. Motivo: ${elegida.justificacion}` : "Todavía sin decisión"],
    ];
    return [h("h3", {}, "Ficha de la versión"),
      h("div", { clase: "tabla" }, h("table", {}, h("tbody", {}, filas.map(([titulo, valor]) => h("tr", {}, h("th", { scope: "row" }, titulo), h("td", {}, valor))))))];
  }

  // Lo que se revisó antes de entrenar: que los datos nuevos fueran compatibles y un análisis de los nuevos y del conjunto completo (sección 8 de la actividad)
  function revisionDeDatos(v) {
    const a = v.metadatos.analisis_de_datos;
    if (!a) return null;
    const c = a.compatibilidad, n = a.nuevos?.resumen, t = a.conjunto?.resumen;
    const resumen = (r, texto) => `${texto}: ${numero(r.filas)} filas, ${numero(r.porcentaje_celdas_faltantes ?? 0, 1)} % de celdas vacías, ${numero(r.filas_duplicadas)} filas duplicadas.`;
    const lineas = [
      c ? `Compatibilidad de los datos nuevos con el modelo: ${c.errores ? "con errores" : c.advertencias ? `sin errores y con ${c.advertencias} ${c.advertencias === 1 ? "advertencia" : "advertencias"}` : "sin problemas"} (${numero(c.filas)} horas revisadas).`
        : "Esta versión no usó datos nuevos: se entrenó con los que ya había.",
      n ? resumen(n, "Datos nuevos") : null, t ? resumen(t, "Conjunto completo con el que se entrenó") : null,
    ].filter(Boolean);
    const utiles = (lista) => (lista ?? []).slice(1).filter((o) => !o.startsWith("No hay filas")).slice(0, 3);      // la primera línea (cuántas filas) y la de duplicados ya están arriba
    const notas = [...utiles(a.nuevos?.observaciones).map((o) => `Datos nuevos: ${o}`), ...utiles(a.conjunto?.observaciones).map((o) => `Conjunto completo: ${o}`)];
    return [h("h3", {}, "Datos revisados antes de entrenar"),
      h("ul", { style: "margin:0;padding-left:1.2rem;display:grid;gap:.4rem" }, [...lineas, ...notas].map((x) => h("li", {}, x)))];
  }

  function decision(elegida) {
    const candidata = elegida.estado === "candidato";
    const aconseja = elegida.recomendacion?.accion;
    const ponerEnUso = (clase) => h("button", { clase: `boton ${clase}`, type: "button", onclick: () => decidir("activar", elegida) },
      candidata ? `Poner en uso la versión ${elegida.version}` : elegida.decision === "descartada" ? `Ponerla en uso de todos modos` : `Volver a usar la versión ${elegida.version}`);
    const descartar = (clase) => h("button", { clase: `boton ${clase}`, type: "button", onclick: () => decidir("descartar", elegida) }, "Descartarla");
    const claseDe = (accion) => (aconseja === "mantener" ? accion === "descartar" : accion === "activar") ? "boton--primario" : "boton--contorno-oscuro";
    return [h("h3", {}, "Su decisión"),
      h("label", { for: "mod-justificacion", style: "display:block;font-weight:800;margin-bottom:.4rem" }, "¿Por qué toma esta decisión? (obligatorio)"),
      h("textarea", { id: "mod-justificacion", rows: 3, maxlength: 300, style: "width:100%;font:inherit;padding:.7rem .9rem;border:2px solid var(--linea);border-radius:var(--radio)",
        placeholder: aconseja === "mantener" ? "Por ejemplo: no mejora el puntaje del rojo con los datos disponibles." : "Por ejemplo: mejora el puntaje del rojo con los datos de las últimas semanas." }),
      h("div", { clase: "acciones-lote" },
        ponerEnUso(claseDe("activar")), candidata ? descartar(claseDe("descartar")) : null,
        h("button", { clase: "boton-texto", type: "button", onclick: () => descargarReporte(elegida.version) }, "Descargar el reporte de actualización"),
        h("button", { clase: "boton-texto", type: "button", onclick: () => { $("mod-detalle").hidden = true; } }, "Cerrar")),
      h("p", { clase: "tarjeta__texto", style: "margin:1rem 0 0" }, "Al poner una versión en uso, los semáforos que ve el público se calculan con ella. La que estaba en uso se conserva como anterior y se puede volver a usar. Si tiene dudas, mantenga la versión en uso."),
      h("div", { id: "mod-detalle-aviso", clase: "aviso", hidden: true })];
  }

  async function descargarReporte(version) {
    try {
      guardarArchivo(await api.reporteDeActualizacion(activa, version), `reporte_actualizacion_${version}.html`);
    } catch (error) {
      aviso("error", falla(error), "mod-detalle-aviso");
    }
  }

  async function abrir(version) {
    const caja = $("mod-detalle");
    try {
      const [elegida, uso] = await Promise.all([api.versionDelModelo(version), api.versionDelModelo(activa)]);
      const esUso = elegida.version === uso.version;
      advertido = false;
      reemplazar(caja,
        h("div", { clase: "cabecera-lote" }, h("h2", {}, esUso ? `Versión ${elegida.version}, la que está en uso` : `Versión ${elegida.version} frente a la que está en uso (${uso.version})`), etiqueta(estadoVisible(elegida))),
        descartada(elegida),
        recomendacion(elegida.recomendacion),
        esUso ? null : [datosNuevos(elegida, uso), comparacion(uso, elegida)],
        ficha(elegida), revisionDeDatos(elegida),
        esUso ? h("div", { clase: "acciones-lote" }, h("button", { clase: "boton-texto", type: "button", onclick: () => { caja.hidden = true; } }, "Cerrar")) : decision(elegida));
      caja.hidden = false;
      caja.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      aviso("error", falla(error));
    }
  }

  async function decidir(que, elegida) {
    const version = elegida.version;
    const texto = $("mod-justificacion").value.trim();
    if (texto.length < MIN_JUSTIFICACION) {
      aviso("error", `Escriba por qué toma esta decisión (al menos ${MIN_JUSTIFICACION} caracteres).`, "mod-detalle-aviso");
      return;
    }
    const contra = elegida.recomendacion && ((que === "activar" && elegida.recomendacion.accion === "mantener") || (que === "descartar" && elegida.recomendacion.accion === "poner_en_uso"));
    if (contra && !advertido) {                      // una sola vez: si insiste, la decisión es suya y queda registrada con su motivo
      advertido = true;
      aviso("error", `El sistema recomendaba ${elegida.recomendacion.accion === "mantener" ? "mantener la versión en uso" : "poner la versión nueva en uso"}. Si está segura de su decisión, pulse el botón otra vez.`, "mod-detalle-aviso");
      return;
    }
    const botones = $("mod-detalle").querySelectorAll("button");
    botones.forEach((b) => { b.disabled = true; });
    try {
      await (que === "activar" ? api.activarVersion : api.descartarVersion)(version, texto);
    } catch (error) {
      aviso("error", falla(error), "mod-detalle-aviso");
      botones.forEach((b) => { b.disabled = false; });
      return;
    }
    $("mod-detalle").hidden = true;
    aviso("info", que === "activar" ? `La versión ${version} ya está en uso: los semáforos del sitio se calculan con ella.` : `La versión ${version} se descartó y quedó guardada como anterior.`);
    await cargarVersiones();
    cargarSugerencia();
    alCambiar();
  }

  async function cargar() {
    $("mod-detalle").hidden = true;
    cargarSugerencia();
    await cargarVersiones();
    try {
      const e = await api.avanceDeActualizacion();
      mostrar(e);
      if (e.estado === "en_curso") seguir();
    } catch (error) {
      falla(error);
    }
  }

  $("mod-actualizar").addEventListener("click", () => actualizar(false));
  $("mod-forzar").addEventListener("click", () => actualizar(true));

  return { cargar };
}

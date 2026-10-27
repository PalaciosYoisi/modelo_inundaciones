// Lo que ve el público: el mapa, la lista de estaciones, el detalle de una estación y el apartado de confiabilidad.
import { fecha, h, hace, icono, numero, porcentaje } from "./dom.js";
import { graficaLinea } from "./grafica.js";

// Textos sencillos para cada color del semáforo
const ESTADOS = {
  Verde: { clase: "verde", titulo: "Nivel normal", frase: "Se espera que el río siga normal durante las próximas 24 horas." },
  Amarillo: { clase: "amarillo", titulo: "Nivel alto", frase: "Se espera un nivel alto. Conviene estar atento a la lluvia." },
  Rojo: { clase: "rojo", titulo: "Peligro de crecida", frase: "Hay riesgo de que el río crezca mucho en las próximas 24 horas. Esté atento a los avisos de las autoridades." },
};
const SIN_DATOS = { clase: "sin", titulo: "Sin datos suficientes", frase: "Todavía no hay suficientes lecturas para calcular el semáforo de esta estación." };
const estadoDe = (e) => ESTADOS[e.semaforo] ?? SIN_DATOS;

// ------------------------------------------------------------------ Mapa
export function crearMapa(contenedor, alElegir) {
  if (!window.L) {
    contenedor.append(h("p", { clase: "mapa__mensaje" }, "El mapa necesita conexión a internet. Mientras tanto, puede elegir la estación en la lista."));
    return { pintar() {}, ajustar() {} };
  }
  const mapa = window.L.map(contenedor, { scrollWheelZoom: false }).setView([5.5, -76.5], 8);
  window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(mapa);
  let marcadores = [];
  let ajustado = false;
  return {
    pintar(estaciones, elegida) {
      marcadores.forEach((m) => m.remove());
      marcadores = [];
      const puntos = [];
      for (const e of estaciones) {
        if (e.latitud === null || e.latitud === undefined) continue;
        const clases = `marcador marcador--${estadoDe(e).clase}${e.desactualizada ? " marcador--vieja" : ""}${e.codigo === elegida ? " marcador--elegido" : ""}`;
        const marcador = window.L.marker([e.latitud, e.longitud], {
          icon: window.L.divIcon({ className: clases, html: "<span></span>", iconSize: [34, 34], iconAnchor: [17, 17] }), title: e.nombre, keyboard: true,
        }).addTo(mapa).on("click", () => alElegir(e.codigo));
        marcador.bindTooltip(h("span", {}, `${e.nombre} · ${estadoDe(e).titulo}`), { direction: "top", offset: [0, -16] });
        marcadores.push(marcador);
        puntos.push([e.latitud, e.longitud]);
      }
      if (!ajustado && puntos.length) {
        mapa.fitBounds(puntos, { padding: [60, 60], maxZoom: 11 });
        ajustado = true;
      }
    },
    ajustar() { mapa.invalidateSize(); },
  };
}

// ------------------------------------------------------------------ Lista de estaciones
export function vistaLista(estaciones, elegida, alElegir) {
  return estaciones.map((e) => {
    const estado = estadoDe(e);
    return h("button", { clase: `estacion estacion--${estado.clase}`, type: "button", "aria-pressed": String(e.codigo === elegida), onclick: () => alElegir(e.codigo) },
      h("span", { clase: "estacion__luz", "aria-hidden": "true" }),
      h("span", { clase: "estacion__datos" }, h("b", {}, e.nombre), h("span", {}, e.municipio)),
      h("span", { clase: "estacion__estado" }, estado.titulo,
        h("small", {}, e.desactualizada ? "Datos no recientes" : e.ultima_lectura ? `Lectura ${hace(e.horas_desde_ultima_lectura)}` : "")));
  });
}

// ------------------------------------------------------------------ Detalle de una estación
function posibilidad(clase, texto, fraccion) {
  return h("div", { clase: `posibilidad posibilidad--${clase}` },
    h("div", { clase: "posibilidad__texto" }, h("span", {}, texto), h("span", {}, porcentaje(fraccion))),
    h("div", { clase: "posibilidad__pista" }, h("i", { style: `width:${Math.max((fraccion ?? 0) * 100, 0)}%` })));
}

export function vistaDetalle(e, umbralAviso) {
  const estado = estadoDe(e);
  const historial = e.historial ?? [];
  return [
    h("div", { clase: "detalle__cabecera" },
      h("h3", {}, e.nombre),
      h("p", {}, e.ultima_lectura ? `${e.municipio} · Última lectura: ${fecha(e.ultima_lectura)}` : e.municipio)),
    e.desactualizada
      ? h("div", { clase: "alerta-datos" }, icono("aviso"),
        h("span", {}, `Los datos de esta estación no son recientes (última lectura ${hace(e.horas_desde_ultima_lectura)}). El semáforo puede no reflejar lo que pasa ahora.`))
      : null,
    h("div", { clase: "detalle__cuerpo" },
      h("div", { clase: `faro faro--${estado.clase}` }, h("span", { clase: "faro__luz", "aria-hidden": "true" }), h("h4", {}, estado.titulo), h("p", {}, estado.frase)),
      e.semaforo
        ? h("div", {},
          h("h5", {}, `¿Qué tan posible es cada caso en las próximas ${e.horizonte_horas} horas?`),
          h("div", { clase: "posibilidades" },
            posibilidad("verde", "Que siga normal", e.prob_verde), posibilidad("amarillo", "Que esté alto", e.prob_amarillo), posibilidad("rojo", "Que haya peligro de crecida", e.prob_rojo)),
          h("p", {}, `Último nivel medido: ${numero(e.nivel_m, 2)} metros.`))
        : h("div", {}, h("p", {}, "Cuando la estación tenga más lecturas, aquí verá qué tan posible es cada caso."))),
    e.semaforo ? h("div", { clase: "graficas" },
      graficaLinea({ titulo: "Nivel del río en los últimos 7 días (metros)", color: "#0f4c75", formatoY: (v) => numero(v, 1),
        puntos: historial.map((p) => ({ t: p.fecha_hora, y: p.nivel_m })) }),
      graficaLinea({ titulo: "Posibilidad de peligro en los últimos 7 días", color: "#e5484d", yMin: 0, yMax: 1, formatoY: (v) => `${Math.round(v * 100)} %`,
        umbral: { valor: umbralAviso, etiqueta: "Desde aquí se avisa rojo" }, puntos: historial.map((p) => ({ t: p.fecha_hora, y: p.prob_rojo })) })) : null,
    h("p", { clase: "pie-detalle" }, "Este semáforo es una ayuda para anticiparse y no reemplaza los avisos oficiales de las autoridades."),
  ];
}

// ------------------------------------------------------------------ Qué tan confiable es
export function vistaConfianza(m) {
  const medidas = m.metricas_validacion?.["Bosque aleatorio"] ?? {};
  const cifra = (valor, texto) => h("li", {}, h("b", {}, valor), h("span", {}, texto));
  return {
    descripcion: "Probamos el sistema con datos reales de años anteriores que no había visto. Estos son los resultados.",
    cifras: [
      cifra(numero(Object.keys(m.estaciones).length), "estaciones en el Chocó"),
      cifra(`${m.configuracion.horizonte_horas} h`, "de anticipación"),
      cifra(`${Math.round((medidas["Alertas rojas correctas"] ?? 0) * 10)} de 10`, "avisos de rojo que acertaron"),
      cifra(`${Math.round((medidas["Rojos detectados"] ?? 0) * 10)} de 10`, "crecidas que se alcanzaron a anticipar"),
    ],
  };
}

// Panel del administrador: ingreso con clave y flujo de archivos (revisar, analizar y publicar).
import * as api from "./api.js";
import { h, icono, reemplazar } from "./dom.js";
import { vistaAnalisis, vistaPublicacion, vistaRevision } from "./vistas_admin.js";

const $ = (id) => document.getElementById(id);

export function iniciarAdmin({ alPublicar }) {
  const dialogo = $("dialogo-admin");
  const botones = { revisar: $("adm-revisar"), analizar: $("adm-analizar"), publicar: $("adm-publicar") };
  const paneles = { revision: $("adm-panel-revision"), analisis: $("adm-panel-analisis"), publicacion: $("adm-panel-publicacion") };
  const pestanas = { revision: $("adm-pestana-revision"), analisis: $("adm-pestana-analisis"), publicacion: $("adm-pestana-publicacion") };
  const iniciales = new Map(Object.values(paneles).map((p) => [p, [...p.childNodes].map((n) => n.cloneNode(true))]));
  let archivo = null;

  // ---------------------------------------------------------------- Ingreso y salida
  function mostrarPanel(visible, desplazar = false) {
    $("admin").hidden = !visible;
    if (visible && desplazar) $("admin").scrollIntoView({ behavior: "smooth" });
  }

  $("abrir-admin").addEventListener("click", () => {
    if (api.sesion.clave()) { mostrarPanel(true, true); return; }
    $("error-admin").hidden = true;
    $("clave-admin").value = "";
    dialogo.showModal();
    $("clave-admin").focus();
  });
  $("cancelar-admin").addEventListener("click", () => dialogo.close());
  $("form-admin").addEventListener("submit", async (evento) => {
    evento.preventDefault();
    try {
      await api.verificarClave($("clave-admin").value);
      api.sesion.guardar($("clave-admin").value);
      dialogo.close();
      mostrarPanel(true, true);
    } catch (error) {
      $("error-admin").textContent = error.codigo === "CLAVE_INCORRECTA" ? "La clave no es correcta. Intente de nuevo." : error.message;
      $("error-admin").hidden = false;
    }
  });
  $("cerrar-sesion").addEventListener("click", () => { api.sesion.cerrar(); elegir(null); mostrarPanel(false); window.scrollTo({ top: 0, behavior: "smooth" }); });

  if (api.sesion.clave()) {                       // si la pestaña ya tenía una sesión, se recupera sin molestar
    api.verificarClave(api.sesion.clave()).then(() => mostrarPanel(true)).catch(() => api.sesion.cerrar());
  }

  // ---------------------------------------------------------------- Avisos y pestañas
  function aviso(tipo, mensaje) {
    const caja = $("adm-aviso");
    caja.className = `aviso aviso--${tipo}`;
    reemplazar(caja, tipo === "error" ? icono("aviso") : null, h("p", { style: "margin:0" }, mensaje));
    caja.hidden = false;
  }
  const ocultarAviso = () => { $("adm-aviso").hidden = true; };

  function irA(clave) {
    for (const [k, pestana] of Object.entries(pestanas)) {
      pestana.setAttribute("aria-selected", String(k === clave));
      paneles[k].hidden = k !== clave;
    }
  }
  const claves = Object.keys(pestanas);
  for (const [clave, pestana] of Object.entries(pestanas)) {
    pestana.addEventListener("click", () => irA(clave));
    pestana.addEventListener("keydown", (e) => {
      const paso = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!paso) return;
      const siguiente = claves[(claves.indexOf(clave) + paso + claves.length) % claves.length];
      irA(siguiente);
      pestanas[siguiente].focus();
    });
  }

  // ---------------------------------------------------------------- Archivo elegido
  const tamano = (bytes) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);

  function elegir(nuevo) {
    archivo = nuevo;
    $("adm-zona-carga").dataset.conArchivo = String(Boolean(nuevo));
    $("adm-archivo-elegido").hidden = !nuevo;
    if (nuevo) {
      $("adm-archivo-nombre").textContent = nuevo.name;
      $("adm-archivo-detalle").textContent = tamano(nuevo.size);
    } else {
      $("adm-archivo").value = "";
    }
    Object.values(botones).forEach((b) => { b.disabled = !nuevo; });
    for (const [panel, nodos] of iniciales) reemplazar(panel, nodos.map((n) => n.cloneNode(true)));
    ocultarAviso();
    irA("revision");
  }

  $("adm-archivo").addEventListener("change", (e) => elegir(e.target.files[0] ?? null));
  $("adm-quitar").addEventListener("click", () => elegir(null));
  const area = $("adm-area-carga");
  ["dragenter", "dragover"].forEach((n) => area.addEventListener(n, (e) => { e.preventDefault(); area.classList.add("arrastrando"); }));
  ["dragleave", "drop"].forEach((n) => area.addEventListener(n, () => area.classList.remove("arrastrando")));
  area.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) elegir(e.dataTransfer.files[0]); });

  // ---------------------------------------------------------------- Acciones
  async function ejecutar(textoCarga, tarea) {
    Object.values(botones).forEach((b) => { b.disabled = true; });
    aviso("cargando", textoCarga);
    try {
      await tarea();
      ocultarAviso();
    } catch (error) {
      if (error.estado === 401) {                 // la clave dejó de valer: se vuelve a pedir
        api.sesion.cerrar();
        mostrarPanel(false);
      }
      if (error.detalle?.diagnostico) {           // el archivo no sirve: se muestra el diagnóstico completo
        reemplazar(paneles.revision, vistaRevision(error.detalle.diagnostico));
        irA("revision");
      }
      aviso("error", error.message ?? "Ocurrió un error inesperado.");
    } finally {
      Object.values(botones).forEach((b) => { b.disabled = !archivo; });
    }
  }

  botones.revisar.addEventListener("click", () => ejecutar("Revisando el archivo…", async () => {
    reemplazar(paneles.revision, vistaRevision((await api.validar(archivo)).validacion));
    irA("revision");
  }));

  botones.analizar.addEventListener("click", () => ejecutar("Analizando los datos…", async () => {
    reemplazar(paneles.analisis, vistaAnalisis(await api.analizar(archivo)));
    irA("analisis");
  }));

  botones.publicar.addEventListener("click", () => ejecutar("Revisando y publicando…", async () => {
    const respuesta = await api.publicar(archivo);
    reemplazar(paneles.revision, vistaRevision(respuesta.validacion));
    reemplazar(paneles.publicacion, vistaPublicacion(respuesta, { alDescargar: descargar }));
    irA("publicacion");
    await alPublicar();                           // el público ve enseguida los semáforos nuevos
  }));

  async function descargar() {
    await ejecutar("Preparando el archivo…", async () => {
      const blob = await api.descargarPredicciones(archivo);
      const enlace = h("a", { href: URL.createObjectURL(blob), download: `predicciones_${archivo.name.replace(/\.[^.]+$/, "")}.csv` });
      document.body.append(enlace);
      enlace.click();
      enlace.remove();
      URL.revokeObjectURL(enlace.href);
    });
  }
}

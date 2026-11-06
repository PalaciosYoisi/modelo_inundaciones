"""Reportes descargables y archivos de datos combinados.

Los reportes son páginas HTML que se descargan completas (con sus estilos y gráficas adentro), se abren en el navegador y se pueden imprimir
o guardar como PDF. Solo dicen lo que se calculó con los datos: las relaciones son asociaciones y nunca causas. Este módulo no lee ni escribe
archivos: recibe los resultados de la validación, del análisis, de las predicciones o de las versiones, y devuelve el texto del reporte.
"""
import html
import math
import re
import unicodedata

import pandas as pd

from hora import hora_actual

TEXTO_SEMAFORO = {"Verde": "Nivel normal", "Amarillo": "Nivel alto", "Rojo": "Peligro de crecida"}     # las mismas palabras de la página pública


def datos_combinados(predicciones: pd.DataFrame, estaciones: dict, codigo: str | None = None, dias: int | None = None) -> pd.DataFrame:
    """Una fila por estación y hora, con nombres de columna claros. codigo: una sola estación. dias: solo los últimos días con datos."""
    tabla = predicciones[["estacion", "fecha_hora", "nivel_m", "lluvia_mm", "prob_rojo", "semaforo"]]
    ultima = tabla["fecha_hora"].max()
    if codigo:
        tabla = tabla[tabla["estacion"] == codigo]
    if dias:
        tabla = tabla[tabla["fecha_hora"] > ultima - pd.Timedelta(days=dias)]
    tabla = tabla[tabla["nivel_m"].notna() | tabla["lluvia_mm"].notna()].sort_values(["estacion", "fecha_hora"])
    dato = lambda c, campo: estaciones.get(c, {}).get(campo, c if campo == "nombre" else "")
    return pd.DataFrame({
        "Código de estación": tabla["estacion"].values,
        "Estación": [dato(c, "nombre") for c in tabla["estacion"]],
        "Municipio": [dato(c, "municipio") for c in tabla["estacion"]],
        "Fecha y hora": tabla["fecha_hora"].dt.strftime("%Y-%m-%d %H:%M").values,
        "Nivel del río (m)": tabla["nivel_m"].round(3).values,
        "Lluvia (mm)": tabla["lluvia_mm"].round(2).values,
        "Posibilidad de peligro (%)": (tabla["prob_rojo"] * 100).round(1).values,
        "Semáforo": tabla["semaforo"].map(TEXTO_SEMAFORO).values,
    })


def a_csv(tabla: pd.DataFrame) -> bytes:
    """CSV con codificación que Excel abre bien (UTF-8 con marca)."""
    return tabla.to_csv(index=False).encode("utf-8-sig")


def nombre_de_archivo(estaciones: dict, codigo: str | None = None, dias: int | None = None) -> str:
    base = "datos_" + (estaciones.get(codigo, {}).get("nombre", codigo) if codigo else "todas_las_estaciones")
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_") + (f"_{dias}_dias" if dias else "") + ".csv"


# ==============================================================================
# Piezas comunes de los reportes
# ==============================================================================

ESTILOS = """
:root { --azul: #0b3c5d; --agua: #0e7c92; --gris: #f1f5fa; --linea: #d9e2ec; --texto: #14283a; --suave: #5a6b7c;
        --verde: #1f7a4d; --verde-s: #def3e7; --ambar: #8a5a00; --ambar-s: #fdebc4; --rojo: #a12a2e; --rojo-s: #fbe0e1; --info-s: #e1f3f6; }
* { box-sizing: border-box; }
body { margin: 0; font-family: "Segoe UI", system-ui, -apple-system, sans-serif; color: var(--texto); background: #fff; line-height: 1.55; }
main { max-width: 980px; margin: 0 auto; padding: 32px 24px 56px; }
header { border-bottom: 3px solid var(--agua); padding-bottom: 16px; margin-bottom: 24px; }
.marca { margin: 0 0 4px; font-size: .85rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--agua); }
h1 { margin: 0; font-size: 1.8rem; line-height: 1.2; color: var(--azul); }
.sub { margin: 6px 0 0; color: var(--suave); }
h2 { margin: 32px 0 10px; font-size: 1.25rem; color: var(--azul); }
h3 { margin: 22px 0 8px; font-size: 1.05rem; }
p { margin: 0 0 10px; }
.estado { display: flex; gap: 14px; align-items: flex-start; padding: 16px 18px; border-radius: 12px; border: 2px solid var(--c); background: var(--s); margin: 0 0 22px; }
.estado b { display: block; font-size: 1.2rem; color: var(--c); }
.estado p { margin: 4px 0 0; }
.estado--verde { --c: var(--verde); --s: var(--verde-s); } .estado--ambar { --c: var(--ambar); --s: var(--ambar-s); }
.estado--rojo { --c: var(--rojo); --s: var(--rojo-s); } .estado--info { --c: var(--agua); --s: var(--info-s); }
.datos { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 0 0 18px; }
.dato { background: var(--gris); border-radius: 10px; padding: 12px 14px; }
.dato b { display: block; font-size: 1.5rem; line-height: 1.15; color: var(--azul); }
.dato span { font-size: .85rem; color: var(--suave); }
table { border-collapse: collapse; width: 100%; font-size: .92rem; margin: 6px 0 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--linea); vertical-align: top; }
th { background: var(--gris); color: var(--azul); font-weight: 700; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
.tabla { overflow-x: auto; border: 1px solid var(--linea); border-radius: 10px; margin: 6px 0 14px; }
.tabla table { margin: 0; } .tabla tr:last-child td { border-bottom: 0; }
.etiqueta { display: inline-block; padding: 1px 10px; border-radius: 99px; font-size: .78rem; font-weight: 700; background: var(--gris); color: var(--suave); }
.etiqueta--verde { background: var(--verde-s); color: var(--verde); } .etiqueta--ambar { background: var(--ambar-s); color: var(--ambar); }
.etiqueta--rojo { background: var(--rojo-s); color: var(--rojo); }
.nota { background: var(--info-s); border-left: 4px solid var(--agua); border-radius: 6px; padding: 10px 14px; margin: 8px 0 14px; font-size: .92rem; }
.barra { display: flex; height: 14px; border-radius: 99px; overflow: hidden; background: var(--gris); margin: 4px 0 6px; }
.barra i { display: block; height: 100%; }
ul.lista { margin: 0 0 12px; padding-left: 1.2rem; } ul.lista li { margin: 3px 0; }
figure { margin: 0 0 18px; } figure img { max-width: 100%; border: 1px solid var(--linea); border-radius: 8px; }
figcaption { font-size: .85rem; color: var(--suave); }
.figuras { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 14px; }
footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--linea); font-size: .85rem; color: var(--suave); }
@media print { main { padding: 0; } .tabla { overflow: visible; } tr, figure { break-inside: avoid; } h2 { break-after: avoid; } }
"""


class _Html(str):
    """Texto que ya es HTML seguro: no se vuelve a escapar."""


def _e(valor) -> str:
    """Escapa lo que viene de un archivo o de la base para que nunca altere la página."""
    if isinstance(valor, _Html):
        return valor
    return "—" if valor is None else html.escape(str(valor))


def _num(valor, decimales: int = 0) -> str:
    """Número con el formato de aquí: punto para los miles y coma para los decimales."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "—"
    return f"{valor:,.{decimales}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _pct(fraccion, decimales: int = 0) -> str:
    return "—" if fraccion is None else f"{_num(fraccion * 100, decimales)} %"


def _fecha(valor, con_hora: bool = True) -> str:
    momento = pd.to_datetime(valor, errors="coerce")
    if pd.isna(momento):
        return "—" if valor in (None, "") else str(valor)
    return momento.strftime("%d/%m/%Y %H:%M" if con_hora else "%d/%m/%Y")


def _tabla(encabezados: list, filas: list, numericas: tuple = ()) -> str:
    """Tabla con encabezados y filas de celdas (texto o _Html). numericas: posiciones de las columnas que van alineadas a la derecha."""
    clase = lambda i: ' class="n"' if i in numericas else ""
    cabeza = "".join(f"<th{clase(i)}>{_e(t)}</th>" for i, t in enumerate(encabezados))
    cuerpo = "".join("<tr>" + "".join(f"<td{clase(i)}>{_e(c)}</td>" for i, c in enumerate(fila)) + "</tr>" for fila in filas)
    return f'<div class="tabla"><table><thead><tr>{cabeza}</tr></thead><tbody>{cuerpo}</tbody></table></div>'


def _datos(items: list) -> str:
    return '<div class="datos">' + "".join(f'<div class="dato"><b>{_e(valor)}</b><span>{_e(texto)}</span></div>' for valor, texto in items) + "</div>"


def _estado(clase: str, titulo: str, texto: str) -> str:
    return f'<div class="estado estado--{clase}"><div><b>{_e(titulo)}</b><p>{_e(texto)}</p></div></div>'


def _etiqueta(texto: str, clase: str = "") -> _Html:
    return _Html(f'<span class="etiqueta etiqueta--{clase}">{_e(texto)}</span>')


def nombre_de_reporte(tipo: str, archivo: str) -> str:
    """Nombre del archivo que se descarga: reporte_<tipo>_<nombre del archivo>.html, solo con letras, números y guiones bajos."""
    base = unicodedata.normalize("NFKD", archivo.rsplit(".", 1)[0]).encode("ascii", "ignore").decode("ascii")
    return f"reporte_{tipo}_" + (re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_") or "archivo") + ".html"


def _lista(textos: list) -> str:
    return '<ul class="lista">' + "".join(f"<li>{_e(t)}</li>" for t in textos) + "</ul>" if textos else ""


def _pagina(titulo: str, subtitulo: str, cuerpo: str) -> str:
    """La página completa, con todo adentro: se puede guardar, enviar o imprimir sin depender de nada más."""
    generado = hora_actual().strftime("%d/%m/%Y %H:%M")
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_e(titulo)}</title><style>{ESTILOS}</style></head><body><main>"
            f'<header><p class="marca">RíoAlerta · semáforo de crecidas de los ríos del Chocó</p><h1>{_e(titulo)}</h1><p class="sub">{_e(subtitulo)}</p></header>'
            f"{cuerpo}"
            f"<footer>Reporte generado automáticamente por el sistema el {generado} (hora de Colombia). Muestra resultados calculados con los datos cargados; "
            f"las relaciones entre variables son asociaciones y no demuestran causas.</footer></main></body></html>")


# ==============================================================================
# Reporte de compatibilidad
# ==============================================================================

ESTADOS_COMPATIBILIDAD = {
    "compatible": ("verde", "Compatible", "El archivo tiene todo lo que el modelo necesita. Se pueden generar predicciones."),
    "compatible_con_advertencias": ("ambar", "Compatible con advertencias",
                                    "El archivo se puede procesar, pero hay situaciones que conviene conocer antes de usar los resultados."),
    "incompatible": ("rojo", "Incompatible", "No se generaron predicciones: hay problemas que impiden usar el modelo de forma segura. "
                                             "Corrija el archivo y cárguelo de nuevo para repetir la revisión."),
}


def _tabla_de_hallazgos(hallazgos: list) -> str:
    def filas_afectadas(h: dict) -> str:
        if h.get("filas_afectadas") is None:
            return "—"
        ejemplos = f" (por ejemplo, las filas {', '.join(str(f) for f in h['ejemplos_filas'])})" if h.get("ejemplos_filas") else ""
        return f"{_num(h['filas_afectadas'])} ({_num(h.get('porcentaje_filas'), 1)} %){ejemplos}"
    return _tabla(["Variable", "Qué se encontró", "Qué esperaba el modelo", "Qué recibió", "Filas afectadas"],
                  [[h.get("variable"), h.get("mensaje"), h.get("esperado") or "—", h.get("encontrado") or "—", filas_afectadas(h)] for h in hallazgos])


def reporte_compatibilidad(diagnostico: dict, archivo: str, version_modelo: str) -> str:
    """Columnas esperadas y recibidas, tipos de dato, advertencias, errores y estado final de un archivo frente al esquema del modelo.

    diagnostico: lo que devuelve validacion.validar. El reporte describe lo encontrado; no corrige nada.
    """
    resumen = diagnostico["resumen"]
    clase, titulo, texto = ESTADOS_COMPATIBILIDAD[diagnostico["estado"]]
    errores = [h for h in diagnostico["hallazgos"] if h["severidad"] == "error"]
    advertencias = [h for h in diagnostico["hallazgos"] if h["severidad"] != "error"]
    columnas = [[c["nombre"], c["tipo_esperado"], c["tipo_recibido"] or "no existe en el archivo", "Sí" if c["admite_faltantes"] else "No",
                 "—" if c["vacios"] is None else f"{_num(c['vacios'])} ({_num(c['porcentaje_vacios'], 1)} %)",
                 _etiqueta("Presente", "verde") if c["presente"] else _etiqueta("Falta", "rojo")] for c in diagnostico["columnas"]]
    cuerpo = [
        _estado(clase, f"Estado final: {titulo}", texto),
        _datos([(_num(resumen["filas"]), "filas recibidas"), (_num(resumen["columnas_recibidas"]), "columnas recibidas"), (_num(resumen["columnas_requeridas"]), "columnas que pide el modelo"),
                (_num(resumen["errores"]), "errores"), (_num(resumen["advertencias"]), "advertencias")]),
        f"<p><b>Archivo:</b> {_e(archivo)} · <b>Periodo:</b> {_fecha(resumen['desde'])} a {_fecha(resumen['hasta'])} · <b>Estaciones:</b> {_e(', '.join(resumen['estaciones']) or '—')} · "
        f"<b>Modelo:</b> versión {_e(version_modelo)}</p>",
        "<h2>Columnas esperadas y recibidas</h2>",
        _tabla(["Columna que pide el modelo", "Tipo esperado", "Tipo recibido", "¿Admite vacíos?", "Vacíos", "Estado"], columnas),
    ]
    if resumen["columnas_ignoradas"]:
        cuerpo.append(f'<div class="nota">El archivo trae columnas que el modelo no usa y se ignoran: {_e(", ".join(resumen["columnas_ignoradas"]))}.</div>')
    cuerpo.append("<h2>Errores</h2>")
    cuerpo.append(_tabla_de_hallazgos(errores) if errores else "<p>No se encontraron errores.</p>")
    cuerpo.append("<h2>Advertencias</h2>")
    cuerpo.append(_tabla_de_hallazgos(advertencias) if advertencias else "<p>No se encontraron advertencias.</p>")
    cuerpo.append("<h2>Cómo se revisó</h2>")
    cuerpo.append(_lista(["Esquema: que estén las columnas obligatorias y cuáles sobran.", "Tipo de dato: que cada columna sea del tipo que el modelo espera.",
                          "Contenido: valores faltantes, categorías y formatos admitidos.",
                          "Reglas del problema: rangos posibles, por ejemplo, un nivel de río no puede ser cero ni salirse de lo que cada estación ha registrado."]))
    cuerpo.append('<div class="nota">El sistema no corrige ni modifica los datos para forzar una predicción. Si hay errores, se debe corregir el archivo y volver a revisarlo.</div>')
    return _pagina("Reporte de compatibilidad", f"{archivo} · modelo versión {version_modelo}", "".join(cuerpo))


# ==============================================================================
# Reporte de EDA (análisis exploratorio)
# ==============================================================================

def reporte_eda(resultado: dict, figuras: list, archivo: str) -> str:
    """Resumen del dataset, calidad de los datos, estadísticas, gráficas y observaciones objetivas. resultado y figuras: lo que devuelve eda.analizar."""
    r = resultado["resumen"]
    cuerpo = [
        _datos([(_num(r["filas"]), "filas"), (_num(r["columnas"]), "columnas"), (_num(r["filas_duplicadas"]), "filas duplicadas"),
                (f"{_num(r['porcentaje_celdas_faltantes'], 1)} %", "celdas vacías")]),
        "<h2>Observaciones del sistema</h2>", _lista(resultado["observaciones"]),
        "<h2>Variables y calidad de los datos</h2>",
        _tabla(["Variable", "Tipo detectado", "Con dato", "Faltantes", "Valores distintos"],
               [[v["nombre"], v["tipo"], _num(v["datos"]), f"{_num(v['faltantes'])} ({_num(v['porcentaje_faltantes'], 1)} %)", _num(v["valores_unicos"])] for v in resultado["variables"]],
               numericas=(2, 3, 4)),
    ]
    if r.get("usa_muestra_para_graficas"):
        cuerpo.append('<div class="nota">El archivo es muy grande: las gráficas y las correlaciones se calcularon con una muestra.</div>')
    if resultado["numericas"]:
        cuerpo += ["<h2>Variables numéricas</h2>",
                   _tabla(["Variable", "Media", "Desv.", "Mínimo", "Mediana", "Máximo", "Atípicos"],
                          [[e["nombre"], _num(e["media"], 2), _num(e["desviacion"], 2), _num(e["minimo"], 2), _num(e["mediana"], 2), _num(e["maximo"], 2),
                            f"{_num(e['atipicos']['cantidad'])} ({_num(e['atipicos']['porcentaje'], 1)} %)"] for e in resultado["numericas"]], numericas=(1, 2, 3, 4, 5, 6)),
                   f'<div class="nota">Atípicos según el criterio {_e(resultado["numericas"][0]["atipicos"]["criterio"])}. Un valor atípico no es necesariamente un error.</div>']
    for c in resultado["categoricas"]:
        cuerpo += [f"<h2>{_e(c['nombre'])} ({_num(c['unicos'])} categorías)</h2>",
                   _tabla(["Valor", "Registros", "Proporción"], [[f["valor"], _num(f["cantidad"]), _pct(f["proporcion"], 1)] for f in c["mas_frecuentes"]], numericas=(1, 2))]
    if resultado["temporales"]:
        cuerpo += ["<h2>Fechas y horas</h2>",
                   _tabla(["Variable", "Desde", "Hasta", "Días cubiertos", "Interrupciones"],
                          [[t["nombre"], _fecha(t["desde"]), _fecha(t["hasta"]), _num(t["dias_cubiertos"], 1), _num(t["huecos"])] for t in resultado["temporales"]], numericas=(3, 4))]
    pares = resultado["relaciones"]["pares"]
    if pares:
        cuerpo += ["<h2>Relaciones entre variables</h2>", f'<div class="nota">{_e(resultado["relaciones"]["criterio"])}. {_e(resultado["relaciones"]["aviso"])}</div>',
                   _tabla(["Variable A", "Variable B", "Spearman"], [[p["a"], p["b"], _num(p["rho"], 2)] for p in pares], numericas=(2,))]
    if figuras:
        cuerpo += ["<h2>Gráficas</h2>", '<div class="figuras">' + "".join(
            f'<figure><img alt="{_e(f["titulo"])}" src="data:image/png;base64,{f["imagen_base64"]}"><figcaption>{_e(f["titulo"])}</figcaption></figure>' for f in figuras) + "</div>"]
    return _pagina("Reporte de análisis de los datos", archivo, "".join(cuerpo))

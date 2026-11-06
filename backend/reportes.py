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


# ==============================================================================
# Reporte de predicciones
# ==============================================================================

COLORES_SEMAFORO = {"Verde": "#7bc67e", "Amarillo": "#f7d154", "Rojo": "#e06666"}
ESTADOS_DE_FILA = {"predicho": "Con predicción", "sin_historia_suficiente": "Sin historia suficiente", "sin_nivel": "Sin nivel", "estacion_desconocida": "Estación desconocida"}
MAX_FILAS_REPORTE = 20


def reporte_predicciones(salida: pd.DataFrame, resumen: dict, archivo: str, version_modelo: str, diagnostico: dict) -> str:
    """Identificación de cada registro, su predicción, su probabilidad y un resumen general.

    salida: pipeline.tabla_de_salida (una fila por registro del archivo, con la fila de origen). resumen: pipeline.resumen. diagnostico: validacion.validar.
    """
    total = max(sum(resumen["distribucion"].values()), 1)
    barra = "".join(f'<i style="width:{n / total * 100:.2f}%;background:{COLORES_SEMAFORO[c]}" title="{c}: {n}"></i>' for c, n in resumen["distribucion"].items() if n)
    leyenda = " · ".join(f"{TEXTO_SEMAFORO[c]}: {_num(n)} ({_pct(n / total)})" for c, n in resumen["distribucion"].items())
    actuales = resumen["actual"]["estaciones"]
    cuerpo = [
        _estado("info", "La pregunta que responde el modelo", resumen["pregunta"] + " El semáforo marca rojo cuando la probabilidad de rojo llega a "
                f"{_pct(resumen['umbral_probabilidad_rojo'])}."),
        _datos([(_num(resumen["predicciones"] + resumen["sin_historia_suficiente"] + resumen["sin_nivel"]), "registros del archivo"), (_num(resumen["predicciones"]), "predicciones generadas"),
                (_num(resumen["sin_historia_suficiente"]), "sin historia suficiente"), (_num(resumen["sin_nivel"]), "sin nivel del río")]),
        f"<p><b>Archivo:</b> {_e(archivo)} · <b>Modelo:</b> versión {_e(version_modelo)} · <b>Estado de la revisión previa:</b> {_e(diagnostico['estado'].replace('_', ' '))}</p>",
        "<h2>Resumen general</h2>", f'<div class="barra">{barra}</div><p>{_e(leyenda)}</p>',
    ]
    if actuales:
        cuerpo += ["<h2>Semáforo más reciente de cada estación</h2>",
                   _tabla(["Estación", "Municipio", "Última hora con nivel", "Nivel (m)", "Probabilidad de rojo", "Semáforo"],
                          [[e["nombre"], e["municipio"], _fecha(e["ultima_hora"]), _num(e["nivel_m"], 2), _pct(e["prob_rojo"]),
                            _etiqueta(TEXTO_SEMAFORO.get(e["semaforo"], e["semaforo"] or "—"), {"Verde": "verde", "Amarillo": "ambar", "Rojo": "rojo"}.get(e["semaforo"], ""))] for e in actuales],
                          numericas=(3, 4))]
    con_prediccion = salida[salida["prob_rojo"].notna()]
    if len(con_prediccion):
        por_estacion = con_prediccion.groupby("estacion").agg(horas=("prob_rojo", "size"), maxima=("prob_rojo", "max"), rojas=("semaforo", lambda s: int((s == "Rojo").sum())))
        cuerpo += ["<h2>Resumen por estación</h2>",
                   _tabla(["Estación", "Horas con predicción", "Horas en rojo", "Mayor probabilidad de rojo"],
                          [[codigo, _num(f.horas), _num(f.rojas), _pct(f.maxima, 1)] for codigo, f in por_estacion.sort_values("maxima", ascending=False).iterrows()], numericas=(1, 2, 3))]
    mostrar = con_prediccion.sort_values(["prob_rojo", "fecha_hora"], ascending=[False, False]).head(MAX_FILAS_REPORTE)
    if len(mostrar):
        cuerpo.append(f"<h2>Las {_num(len(mostrar))} horas con mayor probabilidad de rojo</h2>")
        cuerpo.append(_tabla(["Estación", "Fecha y hora", "Nivel (m)", "Lluvia (mm)", "Probabilidad de rojo", "Semáforo"],
                             [[f.estacion, _fecha(f.fecha_hora), _num(f.nivel_m, 2), _num(f.lluvia_mm, 1), _pct(f.prob_rojo, 1), TEXTO_SEMAFORO.get(f.semaforo, "—") if f.semaforo else "—"]
                              for f in mostrar.itertuples(index=False)], numericas=(2, 3, 4)))
    cuerpo.append(f'<div class="nota">Aquí solo se muestran las horas de mayor atención para que el reporte se pueda leer. '
                  f'Las predicciones de los {_num(len(salida))} registros están en el archivo CSV de predicciones.</div>')
    cuerpo.append("<h2>Cómo leer este reporte</h2>")
    cuerpo.append(_lista(["La probabilidad de rojo es lo que el modelo estima, no una certeza: indica qué tan parecida es la situación a las que antes terminaron en crecida.",
                          "«Sin historia suficiente»: el modelo necesita al menos 7 días de datos anteriores de la estación para predecir.",
                          "«Sin nivel»: esa hora no trae el nivel del río, así que no se puede predecir."]))
    return _pagina("Reporte de predicciones", f"{archivo} · modelo versión {version_modelo}", "".join(cuerpo))


# ==============================================================================
# Reporte de actualización del modelo
# ==============================================================================

METRICAS_DEL_REPORTE = [  # nombre en los metadatos, cómo se lee, cómo se muestra, ¿más es mejor?
    ("Color acertado", "Veces que el color del semáforo coincidió con lo que pasó", "pct", True),
    ("Rojos detectados", "Crecidas (rojo) que el modelo avisó", "pct", True),
    ("Alertas rojas correctas", "Alertas rojas que de verdad eran crecidas", "pct", True),
    ("F1 del rojo", "Puntaje del rojo (de 0 a 1; 1 sería perfecto)", "dec", True),
    ("Rojos no detectados", "Crecidas que el modelo no avisó", "ent", False),
    ("Falsas alarmas", "Alertas rojas que no eran crecidas", "ent", False),
]
ESTADOS_DE_VERSION = {"activo": ("En uso", "verde"), "candidato": ("Por decidir", "ambar"), "historico": ("Anterior", "")}
RAZONES = {"contra": "En contra", "cuidado": "Con cuidado", "favor": "A favor", "nota": "Para tener en cuenta"}


def _estado_de_version(v: dict) -> _Html:
    clave = "descartada" if v.get("estado") == "historico" and v.get("decision") == "descartada" else v.get("estado")
    texto, clase = ("Descartada", "") if clave == "descartada" else ESTADOS_DE_VERSION.get(clave, (clave, ""))
    return _etiqueta(texto, clase)


def _valor_de_metrica(valor, forma: str) -> str:
    return "—" if valor is None else _pct(valor) if forma == "pct" else _num(valor, 2 if forma == "dec" else 0)


def _cambio(antes, despues, mas_es_mejor: bool) -> _Html:
    if antes is None or despues is None:
        return _etiqueta("—")
    if abs(despues - antes) < 0.005:
        return _etiqueta("Igual")
    return _etiqueta("Mejora", "verde") if (despues > antes) == mas_es_mejor else _etiqueta("Empeora", "rojo")


def _ficha_de_version(v: dict) -> list:
    m = v["metadatos"]
    config = m.get("configuracion") or {}
    periodo = m.get("periodo_datos") or [None, None]
    return [_fecha(m.get("fecha_entrenamiento")), f"{_num(m.get('registros_entrenamiento'))} ejemplos", f"{_fecha(periodo[0], False)} a {_fecha(periodo[1], False)}",
            f"{_num(len(m.get('variables_modelo') or []))} variables", f"Bosque aleatorio: {_num(config.get('n_estimators'))} árboles, profundidad máxima {_num(config.get('max_depth'))}",
            _num((m.get("metrica_principal") or {}).get("valor_modelo"), 2), m.get("archivo_modelo") or "—"]


def reporte_actualizacion(anterior: dict, candidata: dict, recomendacion: dict | None = None) -> str:
    """Descripción de los datos nuevos, comparación entre la versión anterior y la candidata, métricas y la decisión registrada.

    anterior y candidata: cada versión como la devuelve versiones.obtener (estado, decisión y metadatos). recomendacion: el consejo del sistema, si lo hay.
    """
    meta = candidata["metadatos"]
    reentrenamiento = meta.get("reentrenamiento") or {}
    analisis = meta.get("analisis_de_datos") or {}
    antes = (anterior["metadatos"].get("metricas_validacion") or {}).get("Bosque aleatorio") or {}
    despues = (meta.get("metricas_validacion") or {}).get("Bosque aleatorio") or {}
    cuerpo = [_datos([(candidata["version"], "versión candidata"), (anterior["version"], "versión con la que se compara"),
                      (_num(reentrenamiento.get("horas_nuevas_con_nivel")), "horas nuevas con nivel"), (_num(meta.get("registros_entrenamiento")), "ejemplos de entrenamiento")]),
              "<h2>Los datos nuevos</h2>"]
    if reentrenamiento.get("datos_nuevos_desde"):
        cuerpo.append(f"<p>Son los datos que llegaron después del {_fecha(reentrenamiento['datos_nuevos_desde'], False)}, la fecha hasta la que llegaban los datos de la versión anterior.</p>")
    compat, nuevos, conjunto = analisis.get("compatibilidad"), (analisis.get("nuevos") or {}).get("resumen"), (analisis.get("conjunto") or {}).get("resumen")
    lineas = []
    if compat:
        n = compat["advertencias"]
        estado_compat = "con errores" if compat["errores"] else f"sin errores y con {n} {'advertencia' if n == 1 else 'advertencias'}"
        lineas.append(f"Compatibilidad con el modelo: {estado_compat} ({_num(compat['filas'])} horas revisadas).")
    for texto, r in (("Datos nuevos", nuevos), ("Conjunto completo con el que se entrenó", conjunto)):
        if r:
            lineas.append(f"{texto}: {_num(r['filas'])} filas, {_num(r['porcentaje_celdas_faltantes'], 1)} % de celdas vacías, {_num(r['filas_duplicadas'])} filas duplicadas.")
    cuerpo.append(_lista(lineas) if lineas else "<p>Esta versión no guardó el análisis de sus datos.</p>")
    en_datos_nuevos = reentrenamiento.get("modelo_anterior_en_datos_nuevos")
    if en_datos_nuevos:
        cuerpo += [f"<h3>Cómo le fue a la versión {_e(anterior['version'])} con los datos nuevos</h3>",
                   _datos([(_num(en_datos_nuevos["momentos"]), "horas analizadas"), (_num(en_datos_nuevos["con_rojo"]), "veces que llegó una crecida"),
                           (_pct(en_datos_nuevos["Rojos detectados"]) if en_datos_nuevos["con_rojo"] else "—", "de esas crecidas las avisó"),
                           (_num(en_datos_nuevos["Falsas alarmas"]), "falsas alarmas")]),
                   '<div class="nota">Son horas que esa versión no vio al entrenarse: es la mejor señal de si sigue funcionando con lo que pasa hoy.</div>']
    cuerpo += [f"<h2>Comparación: versión {_e(anterior['version'])} contra versión {_e(candidata['version'])}</h2>",
               _tabla(["Qué se mide", f"Versión {anterior['version']}", f"Versión {candidata['version']}", "Cambio"],
                      [[texto, _valor_de_metrica(antes.get(clave), forma), _valor_de_metrica(despues.get(clave), forma), _cambio(antes.get(clave), despues.get(clave), mejor)]
                       for clave, texto, forma, mejor in METRICAS_DEL_REPORTE], numericas=(1, 2)),
               '<div class="nota">Las dos versiones se probaron con periodos distintos, porque la nueva incluye los datos más recientes: la tabla es una guía y no una prueba exacta.</div>']
    if recomendacion:
        cuerpo += ["<h2>Lo que aconseja el sistema</h2>", f"<p><b>{_e(recomendacion['titulo'])}.</b> {_e(recomendacion['texto'])}</p>",
                   _tabla(["", "Razón"], [[RAZONES.get(r["tipo"], r["tipo"]), r["texto"]] for r in recomendacion["razones"]])]
    cuerpo += ["<h2>Fichas de las dos versiones</h2>",
               _tabla(["", f"Versión {anterior['version']}", f"Versión {candidata['version']}"],
                      [[titulo, a, b] for titulo, a, b in zip(["Fecha y hora de entrenamiento", "Registros utilizados", "Datos de", "Variables", "Algoritmo y configuración", "Puntaje del rojo", "Archivo del modelo"],
                                                              _ficha_de_version(anterior), _ficha_de_version(candidata))]),
               "<h2>Decisión registrada</h2>"]
    if candidata.get("decidido_por"):
        accion = {"activada": "Se puso en uso", "descartada": "Se descartó"}.get(candidata.get("decision"), "Decisión")
        cuerpo.append(f"<p>{_estado_de_version(candidata)} {accion} la versión {_e(candidata['version'])} el {_fecha(candidata.get('decidido_en'))}, por {_e(candidata['decidido_por'])}.</p>"
                      f"<p><b>Motivo:</b> {_e(candidata.get('justificacion'))}</p>")
    else:
        cuerpo.append(f"<p>{_estado_de_version(candidata)} Aún no hay una decisión registrada sobre la versión {_e(candidata['version'])}. "
                      f"La versión {_e(anterior['version'])} sigue siendo la que está en uso.</p>")
    cuerpo.append('<div class="nota">Ninguna versión se reemplaza sola: las dos se conservan hasta que una persona decide y explica por qué.</div>')
    return _pagina("Reporte de actualización del modelo", f"Versión {candidata['version']} frente a la versión {anterior['version']}", "".join(cuerpo))

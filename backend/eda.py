"""Módulo de EDA: análisis exploratorio automático de cualquier tabla, sea o no compatible con el modelo.

Primero detecta el tipo de cada columna y solo calcula los análisis y gráficas que tienen sentido para esos tipos.
Solo afirma lo que se puede calcular: las relaciones son asociaciones, nunca causas.
"""
import os

os.environ.setdefault("MPLBACKEND", "Agg")        # gráficas sin ventana
import base64
import io
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ==============================================================================
# Gráficas
# ==============================================================================

matplotlib.use("Agg")

AZUL = "#0B3C5D"
CELESTE = "#328CC1"
COLORES = ["#0B3C5D", "#328CC1", "#E67E22", "#27AE60", "#8E44AD", "#C0392B"]


def _a_base64(figura) -> str:
    memoria = io.BytesIO()
    figura.savefig(memoria, format="png", dpi=90, bbox_inches="tight")
    plt.close(figura)
    return base64.b64encode(memoria.getvalue()).decode("ascii")


def _estilo(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.25)


def histograma(serie: pd.Series, nombre: str) -> str:
    figura, ax = plt.subplots(figsize=(5.4, 3.4))
    ax.hist(serie.dropna(), bins=min(40, max(10, int(np.sqrt(len(serie))))), color=CELESTE, edgecolor="white")
    ax.axvline(serie.median(), color=AZUL, linestyle="--", linewidth=1.2, label=f"mediana {serie.median():.3g}")
    ax.set_title(f"Distribución de {nombre}", fontweight="bold", fontsize=10)
    ax.set_ylabel("Registros")
    ax.legend(fontsize=8)
    _estilo(ax)
    return _a_base64(figura)


def barras(frecuencias: pd.Series, nombre: str) -> str:
    figura, ax = plt.subplots(figsize=(5.4, 3.4))
    etiquetas = [str(v)[:22] for v in frecuencias.index]
    ax.barh(etiquetas[::-1], frecuencias.values[::-1], color=CELESTE)
    ax.set_title(f"Frecuencia de {nombre}", fontweight="bold", fontsize=10)
    ax.set_xlabel("Registros")
    _estilo(ax)
    return _a_base64(figura)


def boxplot_por_grupo(tabla: pd.DataFrame, numerica: str, categoria: str) -> str:
    grupos = [(str(g)[:14], v[numerica].dropna().values) for g, v in tabla.groupby(categoria) if v[numerica].notna().any()]
    figura, ax = plt.subplots(figsize=(5.8, 3.6))
    ax.boxplot([v for _, v in grupos], labels=[g for g, _ in grupos], patch_artist=True,
               boxprops={"facecolor": "#A9D6E5"}, medianprops={"color": AZUL})
    ax.set_title(f"{numerica} según {categoria}", fontweight="bold", fontsize=10)
    ax.tick_params(axis="x", labelrotation=30, labelsize=8)
    _estilo(ax)
    return _a_base64(figura)


def mapa_correlaciones(correlacion: pd.DataFrame) -> str:
    n = len(correlacion)
    figura, ax = plt.subplots(figsize=(min(9, 1.0 + 0.7 * n), min(7, 0.8 + 0.6 * n)))
    imagen = ax.imshow(correlacion.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels([c[:14] for c in correlacion.columns], rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels([c[:14] for c in correlacion.index], fontsize=8)
    if n <= 12:
        for i in range(n):
            for j in range(n):
                ax.text(j, i, f"{correlacion.values[i, j]:.2f}", ha="center", va="center", fontsize=7)
    figura.colorbar(imagen, ax=ax, fraction=0.046)
    ax.set_title("Correlación de Spearman", fontweight="bold", fontsize=10)
    return _a_base64(figura)


def dispersion(x: pd.Series, y: pd.Series, nombre_x: str, nombre_y: str) -> str:
    figura, ax = plt.subplots(figsize=(5.4, 3.8))
    ax.scatter(x, y, s=6, alpha=0.35, color=CELESTE)
    ax.set_xlabel(nombre_x)
    ax.set_ylabel(nombre_y)
    ax.set_title(f"{nombre_y} frente a {nombre_x}", fontweight="bold", fontsize=10)
    _estilo(ax)
    return _a_base64(figura)


def serie_de_tiempo(tabla: pd.DataFrame, tiempo: str, numerica: str, grupo: str | None) -> str:
    figura, ax = plt.subplots(figsize=(7.6, 3.4))
    if grupo:
        for i, (g, datos) in enumerate(tabla.groupby(grupo)):
            diario = datos.set_index(tiempo)[numerica].resample("D").mean()
            ax.plot(diario.index, diario.values, linewidth=1.1, color=COLORES[i % len(COLORES)], label=str(g)[:16])
        ax.legend(fontsize=7, ncol=2)
    else:
        diario = tabla.set_index(tiempo)[numerica].resample("D").mean()
        ax.plot(diario.index, diario.values, linewidth=1.2, color=AZUL)
    ax.set_title(f"{numerica} en el tiempo (promedio diario)", fontweight="bold", fontsize=10)
    ax.tick_params(axis="x", labelrotation=25, labelsize=8)
    _estilo(ax)
    return _a_base64(figura)


# ==============================================================================
# Análisis
# ==============================================================================

UMBRAL_CLASIFICACION = 0.95        # proporción mínima de valores que deben encajar en un tipo
MAX_CATEGORIAS = 20
MAX_MUESTRA = 100_000              # sobre este tamaño, gráficas y correlaciones usan una muestra
MAX_GRAFICAS_POR_TIPO = 5
CRITERIO_ATIPICOS = "1.5 × rango intercuartílico (Tukey)"
AVISO_RELACIONES = ("Son asociaciones calculadas con los datos cargados; no demuestran que una variable cause "
                    "cambios en otra.")


# ---------------------------------------------------------------- detección de tipos
def _clasificar(serie: pd.Series):
    """Devuelve (tipo, serie ya convertida). Tipos: numérica, temporal, categórica, texto, vacía, constante."""
    valores = serie.dropna()
    valores = valores[valores.astype(str).str.strip() != ""]
    if valores.empty:
        return "vacía", serie
    if pd.api.types.is_datetime64_any_dtype(serie):
        return "temporal", serie
    if pd.api.types.is_bool_dtype(serie):
        return "categórica", serie.astype(str)
    if pd.api.types.is_numeric_dtype(serie):
        return ("constante" if valores.nunique() == 1 else "numérica"), serie
    if serie.dtype == object or str(serie.dtype) in ("str", "string"):
        cadenas = serie.astype("string").str.strip().replace("", pd.NA)
    else:
        cadenas = serie
    numeros = pd.to_numeric(cadenas, errors="coerce")
    if numeros.notna().sum() / len(valores) >= UMBRAL_CLASIFICACION:
        textos = valores.astype(str).str.strip()
        es_codigo = (textos.str.fullmatch(r"\d+").all() and textos.str.len().min() >= 6 and textos.nunique() <= MAX_CATEGORIAS)
        if es_codigo:
            return "categórica", cadenas
        if numeros.nunique() == 1:
            return "constante", numeros
        return "numérica", numeros
    fechas = pd.to_datetime(cadenas, format="ISO8601", errors="coerce")
    if fechas.notna().sum() / len(valores) >= UMBRAL_CLASIFICACION:
        return "temporal", fechas
    if valores.nunique() == 1:
        return "constante", cadenas
    if valores.nunique() <= MAX_CATEGORIAS or valores.nunique() / len(valores) < 0.05:
        return "categórica", cadenas
    return "texto", cadenas


# ---------------------------------------------------------------- análisis por tipo
def _estadisticas(numeros: pd.Series, nombre: str) -> dict:
    datos = numeros.dropna()
    q1, mediana, q3 = datos.quantile([0.25, 0.5, 0.75])
    rango = q3 - q1
    inferior, superior = q1 - 1.5 * rango, q3 + 1.5 * rango
    atipicos = int(((datos < inferior) | (datos > superior)).sum())
    return {"nombre": nombre, "n": int(datos.count()), "media": datos.mean(), "desviacion": datos.std(), "minimo": datos.min(),
            "p25": q1, "mediana": mediana, "p75": q3, "maximo": datos.max(), "asimetria": datos.skew(),
            "atipicos": {"criterio": CRITERIO_ATIPICOS, "limite_inferior": inferior, "limite_superior": superior,
                         "cantidad": atipicos, "porcentaje": round(atipicos / len(datos) * 100, 2) if len(datos) else 0}}


def _frecuencias(categorias: pd.Series, nombre: str) -> dict:
    cuenta = categorias.astype(str).str.strip().value_counts()
    cuenta = cuenta[cuenta.index != ""]
    total = int(cuenta.sum())
    return {"nombre": nombre, "unicos": int(len(cuenta)), "mas_frecuentes": [
        {"valor": v, "cantidad": int(n), "proporcion": round(n / total, 4)} for v, n in cuenta.head(10).items()]}


def _temporal(fechas: pd.Series, nombre: str) -> dict:
    datos = fechas.dropna().sort_values().drop_duplicates()
    diferencias = datos.diff().dropna()
    tipica = diferencias.mode().iloc[0] if len(diferencias) else None
    huecos = int((diferencias > tipica * 1.5).sum()) if tipica is not None and tipica > pd.Timedelta(0) else 0
    return {"nombre": nombre, "desde": datos.min(), "hasta": datos.max(), "dias_cubiertos": round((datos.max() - datos.min()).total_seconds() / 86400, 1),
            "frecuencia_tipica": str(tipica) if tipica is not None else None, "huecos": huecos}


def _relaciones(numericas: pd.DataFrame, categoria: pd.Series | None) -> dict:
    resultado = {"criterio": "Correlación de Spearman entre variables numéricas", "aviso": AVISO_RELACIONES, "pares": [],
                 "medias_por_categoria": None}
    if numericas.shape[1] >= 2:
        correlacion = numericas.corr(method="spearman")
        pares = []
        columnas = list(correlacion.columns)
        for i, a in enumerate(columnas):
            for b in columnas[i + 1:]:
                rho = correlacion.loc[a, b]
                if pd.notna(rho):
                    pares.append({"a": a, "b": b, "rho": round(float(rho), 3), "n": int(numericas[[a, b]].dropna().shape[0])})
        resultado["pares"] = sorted(pares, key=lambda p: -abs(p["rho"]))[:10]
    if categoria is not None and numericas.shape[1] >= 1:
        medias = numericas.groupby(categoria.astype(str).str.strip()).mean().round(4)
        resultado["medias_por_categoria"] = {"categoria": categoria.name, "valores": medias.reset_index().rename(columns={categoria.name: "valor"}).to_dict(orient="records")}
    return resultado


def _observaciones(resumen, variables, numericas, temporales, relaciones) -> list:
    notas = [f"El archivo tiene {resumen['filas']:,} filas y {resumen['columnas']} columnas."]
    if resumen["filas_duplicadas"]:
        notas.append(f"Hay {resumen['filas_duplicadas']:,} filas completamente duplicadas ({resumen['porcentaje_duplicadas']} %).")
    else:
        notas.append("No hay filas completamente duplicadas.")
    for v in variables:
        if v["porcentaje_faltantes"] >= 5:
            notas.append(f"'{v['nombre']}' tiene {v['porcentaje_faltantes']} % de valores faltantes.")
        if v["tipo"] == "constante":
            notas.append(f"'{v['nombre']}' tiene el mismo valor en todas las filas.")
        if v["tipo"] == "vacía":
            notas.append(f"'{v['nombre']}' no tiene ningún dato.")
    for e in numericas:
        if e["atipicos"]["porcentaje"] >= 1:
            notas.append(f"'{e['nombre']}' tiene {e['atipicos']['cantidad']:,} valores atípicos ({e['atipicos']['porcentaje']} %) "
                         f"según {CRITERIO_ATIPICOS}.")
    for t in temporales:
        interrupciones = f" con {t['huecos']} interrupciones respecto a su frecuencia habitual" if t["huecos"] else ""
        notas.append(f"'{t['nombre']}' cubre de {str(t['desde'])[:16]} a {str(t['hasta'])[:16]} ({t['dias_cubiertos']} días){interrupciones}.")
    for p in relaciones["pares"][:3]:
        if abs(p["rho"]) >= 0.5:
            notas.append(f"Asociación {'positiva' if p['rho'] > 0 else 'negativa'} entre '{p['a']}' y '{p['b']}' "
                         f"(Spearman {p['rho']}); es una asociación, no prueba causalidad.")
    return notas


# ---------------------------------------------------------------- punto de entrada
def analizar(tabla: pd.DataFrame, nombre_archivo: str = "") -> tuple[dict, list]:
    """Devuelve (resultado, figuras). Las figuras son una lista de {id, titulo, imagen_base64}."""
    filas, columnas = tabla.shape
    duplicadas = int(tabla.duplicated().sum())
    muestreado = filas > MAX_MUESTRA
    muestra = tabla.sample(MAX_MUESTRA, random_state=0) if muestreado else tabla

    convertidas, variables = {}, []
    for nombre in tabla.columns:
        tipo, convertida = _clasificar(tabla[nombre])
        convertidas[nombre] = convertida
        faltantes = int((tabla[nombre].isna() | tabla[nombre].fillna("").astype(str).str.strip().eq("")).sum())
        variables.append({"nombre": nombre, "tipo": tipo, "datos": filas - faltantes, "faltantes": faltantes,
                          "porcentaje_faltantes": round(faltantes / filas * 100, 2), "valores_unicos": int(tabla[nombre].nunique())})

    por_tipo = lambda t: [v["nombre"] for v in variables if v["tipo"] == t]
    nombres_num, nombres_cat, nombres_tiempo = por_tipo("numérica"), por_tipo("categórica"), por_tipo("temporal")
    numericas = pd.DataFrame({n: convertidas[n] for n in nombres_num})
    numericas_muestra = numericas.loc[muestra.index] if len(nombres_num) else numericas

    resumen = {"filas": filas, "columnas": columnas, "filas_duplicadas": duplicadas,
               "porcentaje_duplicadas": round(duplicadas / filas * 100, 2),
               "celdas_faltantes": int(sum(v["faltantes"] for v in variables)),
               "porcentaje_celdas_faltantes": round(sum(v["faltantes"] for v in variables) / (filas * columnas) * 100, 2),
               "usa_muestra_para_graficas": muestreado, "tipos": {t: len(por_tipo(t)) for t in ("numérica", "categórica", "temporal", "texto", "constante", "vacía") if por_tipo(t)}}

    estadisticas = [_estadisticas(convertidas[n], n) for n in nombres_num]
    frecuencias = [_frecuencias(convertidas[n], n) for n in nombres_cat]
    temporales = [_temporal(convertidas[n], n) for n in nombres_tiempo]
    categoria_principal = None
    for n in nombres_cat:
        if 2 <= convertidas[n].nunique() <= 8:
            categoria_principal = convertidas[n].rename(n)
            break
    relaciones = _relaciones(numericas_muestra, categoria_principal.loc[muestra.index] if categoria_principal is not None else None)

    # Gráficas: solo las que corresponden a los tipos encontrados
    figuras = []
    for n in nombres_num[:MAX_GRAFICAS_POR_TIPO + 1]:
        figuras.append({"id": f"hist-{n}", "titulo": f"Distribución de {n}", "imagen_base64": histograma(numericas_muestra[n], n)})
    for n in nombres_cat[:MAX_GRAFICAS_POR_TIPO]:
        cuenta = convertidas[n].astype(str).str.strip().value_counts().head(10)
        figuras.append({"id": f"barras-{n}", "titulo": f"Frecuencia de {n}", "imagen_base64": barras(cuenta, n)})
    if categoria_principal is not None and nombres_num:
        for n in nombres_num[:3]:
            datos = pd.DataFrame({n: numericas_muestra[n], categoria_principal.name: categoria_principal.loc[muestra.index]})
            figuras.append({"id": f"box-{n}", "titulo": f"{n} según {categoria_principal.name}", "imagen_base64": boxplot_por_grupo(datos, n, categoria_principal.name)})
    if len(nombres_num) >= 3:
        figuras.append({"id": "correlaciones", "titulo": "Correlación entre variables numéricas",
                        "imagen_base64": mapa_correlaciones(numericas_muestra.corr(method="spearman"))})
    if relaciones["pares"]:
        par = relaciones["pares"][0]
        figuras.append({"id": "dispersion", "titulo": f"{par['b']} frente a {par['a']}",
                        "imagen_base64": dispersion(numericas_muestra[par["a"]], numericas_muestra[par["b"]], par["a"], par["b"])})
    if nombres_tiempo and nombres_num:
        tiempo = nombres_tiempo[0]
        base = pd.DataFrame({tiempo: convertidas[tiempo]})
        for n in nombres_num[:2]:
            base[n] = numericas[n]
        if categoria_principal is not None and categoria_principal.nunique() <= 6:
            base[categoria_principal.name] = categoria_principal
        base = base.dropna(subset=[tiempo])
        for n in nombres_num[:2]:
            figuras.append({"id": f"tiempo-{n}", "titulo": f"{n} en el tiempo",
                            "imagen_base64": serie_de_tiempo(base.dropna(subset=[n]), tiempo, n,
                                                                       categoria_principal.name if categoria_principal is not None and categoria_principal.nunique() <= 6 else None)})

    resultado = {"archivo": nombre_archivo, "resumen": resumen, "variables": variables, "numericas": estadisticas,
                 "categoricas": frecuencias, "temporales": temporales, "relaciones": relaciones}
    resultado["observaciones"] = _observaciones(resumen, variables, estadisticas, temporales, relaciones)
    return resultado, figuras

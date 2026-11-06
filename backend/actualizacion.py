"""Actualización del modelo: reúne los datos acumulados, los revisa, entrena una versión candidata en segundo plano y la guarda en la base de datos.

Solo corre una actualización a la vez y su avance se puede consultar mientras trabaja; al terminar queda el resultado, con la comparación
contra la versión en uso. No cambia cuál versión está en uso: eso lo decide una persona (versiones.activar).
Si el servidor se reinicia a mitad de camino, la actualización se pierde y se puede lanzar de nuevo.

Cargar o consultar datos nunca reentrena. Para saber cuándo conviene hacerlo, `sugerencia` mira lo que hay guardado: cuánta información
nueva llegó desde el último entrenamiento, qué tan completa es y si trae situaciones poco frecuentes, como las crecidas.
"""
import math
import threading
from datetime import datetime

import numpy as np
import pandas as pd

import lotes
import reentrenamiento
import versiones
from base_datos import ErrorBaseDatos, conexion
from eda import analizar
from hora import hora_actual
from ingesta import CARPETA_MODELOS, DATOS_INICIALES, PUBLICADO_ANTERIOR
from pipeline import a_percentil
from publicacion import Publicacion
from validacion import validar

ETAPA_DATOS = "revisión de los datos"
ETAPA_GUARDADO = "guardado de la versión"
TRAMOS = {                                                      # qué parte de la barra de avance ocupa cada etapa
    ETAPA_DATOS: (0.00, 0.10),
    reentrenamiento.ETAPA_ACTUAL: (0.10, 0.15),
    reentrenamiento.ETAPA_VALIDACION: (0.15, 0.70),
    reentrenamiento.ETAPA_ENTRENAMIENTO: (0.70, 0.95),
    ETAPA_GUARDADO: (0.95, 1.00),
}
_cerrojo = threading.Lock()
_trabajo: dict | None = None                                    # la última actualización, en curso o terminada

# Cuándo conviene reentrenar: se mira la información guardada, no el calendario
MIN_PROPORCION = 0.05           # lo nuevo debe equivaler al menos al 5 % de los ejemplos con que se entrenó la versión en uso
CASI_PROPORCION = 0.02          # a partir del 2 % ya hay algo, pero todavía no alcanza
MIN_COBERTURA_NIVEL = 0.60      # una estación aporta si trae nivel en más del 60 % de las horas del periodo nuevo
MIN_COBERTURA_LLUVIA = 0.40     # por debajo de esto se avisa que casi no hay lluvia (el modelo la necesita para anticipar)
MIN_HORAS_ROJAS = 24            # un día en rojo ya es una situación poco frecuente que vale la pena aprender
CAIDA_EN_DATOS_NUEVOS = 0.10    # cuánto puede bajar el puntaje de la versión en uso con datos recientes antes de considerarla en problemas
DECISIONES = {
    "actualizar": ("Ya conviene actualizar el modelo", "Hay información nueva suficiente y completa para que una versión nueva aprenda algo distinto."),
    "casi": ("Falta un poco", "Ya hay datos nuevos, pero todavía no alcanzan. Conviene esperar un poco más o completar lo que falta."),
    "esperar": ("Todavía no hace falta", "Hay muy poca información nueva desde el último entrenamiento."),
}
ORDEN = {"contra": 0, "cuidado": 1, "favor": 2, "nota": 3}


class ErrorDeActualizacion(Exception):
    """No se puede actualizar el modelo ahora. El mensaje es apto para mostrarlo a una persona."""


# ------------------------------------------------------------------ los datos
def _datos_acumulados(codigos: list[str]) -> pd.DataFrame:
    """Todo lo guardado: las lecturas, los lotes confirmados (del más nuevo al más viejo) y los datos iniciales. Si una hora está en varios, vale el más nuevo."""
    partes = [Publicacion(DATOS_INICIALES, PUBLICADO_ANTERIOR).tabla(codigos, completa=True)]
    for lote in lotes.listar("confirmado", limite=10_000):
        try:
            partes.append(lotes.datos(lote["id"]))
        except FileNotFoundError:
            continue
    partes.append(reentrenamiento.leer_datos(DATOS_INICIALES, codigos))
    return reentrenamiento.combinar(*(p[p["estacion"].isin(codigos)][reentrenamiento.COLUMNAS] for p in partes))


def _a_json(objeto):
    """Lo que sale de pandas y numpy, convertido a tipos que JSON acepta (los vacíos pasan a null)."""
    if isinstance(objeto, dict):
        return {str(k): _a_json(v) for k, v in objeto.items()}
    if isinstance(objeto, (list, tuple)):
        return [_a_json(v) for v in objeto]
    if isinstance(objeto, np.generic):
        objeto = objeto.item()
    if isinstance(objeto, float) and (math.isnan(objeto) or math.isinf(objeto)):
        return None
    return objeto if objeto is None or isinstance(objeto, (str, int, float, bool)) else str(objeto)


def _revisar_datos(nuevos: pd.DataFrame, conjunto: pd.DataFrame, metadatos: dict, avance) -> dict:
    """Antes de entrenar: que los datos nuevos sigan siendo compatibles con el modelo y un análisis de los nuevos y del conjunto completo."""
    revision = {"nuevos": None, "conjunto": None, "compatibilidad": None}
    avance(ETAPA_DATOS, 0.1)
    if len(nuevos):
        texto = nuevos.assign(fecha_hora=nuevos["fecha_hora"].dt.strftime("%Y-%m-%d %H:%M:%S"))
        diagnostico = validar(texto, metadatos, exigir_historia=False)
        revision["compatibilidad"] = {"estado": diagnostico["estado"], **{k: diagnostico["resumen"][k] for k in ("filas", "errores", "advertencias")},
                                      "hallazgos": [{k: h.get(k) for k in ("severidad", "variable", "mensaje")} for h in diagnostico["hallazgos"]][:20]}
        if diagnostico["resumen"]["errores"]:
            primeros = " ".join(h["mensaje"] for h in diagnostico["hallazgos"] if h["severidad"] == "error")[:300]
            raise ErrorDeActualizacion(f"Los datos nuevos no son compatibles con el modelo, por eso no se entrenó. {primeros}")
    for paso, (clave, tabla, nombre) in enumerate((("nuevos", nuevos, "datos nuevos"), ("conjunto", conjunto, "conjunto actualizado"))):
        avance(ETAPA_DATOS, 0.3 + 0.35 * paso)
        if len(tabla):
            resultado, _ = analizar(tabla, nombre, con_figuras=False)
            revision[clave] = {"resumen": resultado["resumen"], "observaciones": resultado["observaciones"]}
    return _a_json(revision)


# ------------------------------------------------------------------ cuándo conviene reentrenar
def _miles(n: float) -> str:
    return f"{round(n):,}".replace(",", ".")


def _coma(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def sugerencia(pipeline, medir: bool = False) -> dict:
    """Qué aconseja el sistema sobre reentrenar, mirando la información guardada desde el último entrenamiento y las razones de su consejo.

    pipeline: la versión en uso. medir: además mide cómo le va a esa versión con los datos nuevos (tarda unos segundos).
    """
    metadatos, parametros = pipeline.metadatos, pipeline.parametros
    nombres = {c: e["nombre"] for c, e in metadatos["estaciones"].items()}
    fin = pd.Timestamp(metadatos["periodo_datos"][1])
    with conexion() as cursor:
        cursor.execute("SELECT estacion, fecha_hora, nivel_m, lluvia_mm FROM lecturas WHERE fecha_hora > %s ORDER BY estacion, fecha_hora", (fin.to_pydatetime(),))
        nuevas = pd.DataFrame(cursor.fetchall(), columns=["estacion", "fecha_hora", "nivel_m", "lluvia_mm"])
        cursor.execute("SELECT COUNT(*) AS n FROM lotes WHERE estado = 'confirmado' AND decidido_en >= %s", (datetime.fromisoformat(metadatos["fecha_entrenamiento"]),))
        lotes_nuevos = int(cursor.fetchone()["n"])
    nuevas["fecha_hora"] = pd.to_datetime(nuevas["fecha_hora"])
    for columna in ("nivel_m", "lluvia_mm"):
        nuevas[columna] = pd.to_numeric(nuevas[columna], errors="coerce")

    por_estacion, utiles, rojas, amarillas, horas_con_nivel = [], 0, 0, 0, 0
    for codigo in pipeline.codigos:
        g = nuevas[nuevas["estacion"] == codigo]
        con_nivel = g[g["nivel_m"].notna()]
        ficha = {"estacion": codigo, "nombre": nombres[codigo], "horas_con_nivel": len(con_nivel), "cobertura_nivel": None, "cobertura_lluvia": None, "ultimo_dato": None, "horas_en_rojo": 0}
        if len(con_nivel):
            ultima = g["fecha_hora"].max()
            periodo = max(int((ultima - fin) / pd.Timedelta(hours=1)), 1)               # horas desde el último entrenamiento hasta su último dato
            ficha.update(cobertura_nivel=len(con_nivel) / periodo, cobertura_lluvia=int(g["lluvia_mm"].notna().sum()) / periodo, ultimo_dato=str(ultima))
            percentiles = a_percentil(con_nivel["nivel_m"].values, pipeline.referencias[codigo])
            ficha["horas_en_rojo"] = int((percentiles >= parametros.umbral_rojo).sum())
            rojas += ficha["horas_en_rojo"]
            amarillas += int(((percentiles >= parametros.umbral_amarillo) & (percentiles < parametros.umbral_rojo)).sum())
            utiles += int((con_nivel["fecha_hora"] <= ultima - pd.Timedelta(hours=parametros.horizonte_h)).sum())     # las 24 horas siguientes ya se conocen
            horas_con_nivel += len(con_nivel)
        por_estacion.append(ficha)

    ejemplos = math.ceil(utiles / parametros.paso_horas)
    proporcion = ejemplos / max(int(metadatos.get("registros_entrenamiento") or 0), 1)
    buenas = [e for e in por_estacion if (e["cobertura_nivel"] or 0) >= MIN_COBERTURA_NIVEL]
    sin_datos = [e for e in por_estacion if not e["horas_con_nivel"]]
    razones = []
    agregar = lambda tipo, texto: razones.append({"tipo": tipo, "texto": texto})
    pct = lambda x: f"{round(x * 100)} %"

    if horas_con_nivel:
        dias = max(1, round(max((pd.Timestamp(e["ultimo_dato"]) - fin).total_seconds() for e in por_estacion if e["ultimo_dato"]) / 86400))
        texto = f"Hay {_miles(horas_con_nivel)} horas nuevas con nivel (datos de unos {dias} días). Equivalen al {pct(proporcion)} de los ejemplos con que se entrenó la versión en uso."
        agregar("favor" if proporcion >= MIN_PROPORCION else "cuidado", texto + ("" if proporcion >= MIN_PROPORCION else f" Conviene que lleguen al {pct(MIN_PROPORCION)} como mínimo."))
    else:
        dias = 0
        agregar("cuidado", "Todavía no hay datos nuevos con nivel desde el último entrenamiento.")
    if horas_con_nivel:
        if len(buenas) >= max(2, math.ceil(len(por_estacion) / 2)):
            agregar("favor", f"{len(buenas)} de {len(por_estacion)} estaciones traen nivel en más del {pct(MIN_COBERTURA_NIVEL)} de las horas: {', '.join(e['nombre'] for e in buenas)}.")
        else:
            agregar("cuidado", f"Solo {len(buenas)} de {len(por_estacion)} estaciones traen datos casi completos. Las demás tienen muchos huecos o no han enviado datos.")
    for e in sin_datos:
        if horas_con_nivel:
            agregar("cuidado", f"{e['nombre']} no ha enviado datos nuevos desde el último entrenamiento.")
    for e in por_estacion:
        if e["horas_con_nivel"] and (e["cobertura_lluvia"] or 0) < MIN_COBERTURA_LLUVIA:
            agregar("cuidado", f"{e['nombre']} casi no tiene lluvia en los datos nuevos (solo el {pct(e['cobertura_lluvia'] or 0)} de las horas): sin lluvia, el modelo anticipa peor las crecidas.")
    if horas_con_nivel:
        if rojas >= MIN_HORAS_ROJAS:
            agregar("favor", f"Hubo {_miles(rojas)} horas en rojo en los datos nuevos: son situaciones poco frecuentes que ayudan al modelo a aprender.")
        elif rojas:
            agregar("nota", f"Solo hubo {rojas} horas en rojo en los datos nuevos.")
        else:
            agregar("nota", "En los datos nuevos no hubo crecidas.")
        agregar("nota", f"Vienen de {lotes_nuevos} lotes confirmados desde el último entrenamiento.")

    medicion, fallando = None, False
    if medir:
        medicion = reentrenamiento.evaluar_modelo_actual(_datos_acumulados(pipeline.codigos), metadatos, pipeline.modelo, fin)
        original = (metadatos.get("metrica_principal") or {}).get("valor_modelo")
        if medicion is None:
            agregar("nota", "Todavía no hay suficientes horas nuevas con sus 24 horas siguientes conocidas para medir la versión en uso.")
        elif not medicion["con_rojo"]:
            agregar("nota", "En los datos nuevos no hubo crecidas, así que no se puede medir si la versión en uso las avisa.")
        elif original is not None and medicion["F1 del rojo"] < original - CAIDA_EN_DATOS_NUEVOS:
            fallando = True
            agregar("favor", f"La versión en uso está fallando con los datos recientes (puntaje {_coma(medicion['F1 del rojo'])} contra {_coma(original)} cuando se entrenó): conviene renovarla.")
        else:
            agregar("nota", f"La versión en uso sigue funcionando con los datos recientes (puntaje {_coma(medicion['F1 del rojo'])}).")

    if fallando or (proporcion >= MIN_PROPORCION and len(buenas) >= max(2, math.ceil(len(por_estacion) / 2))) \
            or (proporcion >= CASI_PROPORCION and rojas >= MIN_HORAS_ROJAS and buenas):
        accion = "actualizar"
    elif proporcion >= CASI_PROPORCION:
        accion = "casi"
    else:
        accion = "esperar"
    razones.sort(key=lambda x: ORDEN[x["tipo"]])
    titulo, texto = DECISIONES[accion]
    return _a_json({"accion": accion, "titulo": titulo, "texto": texto, "razones": razones, "medicion": medicion,
                    "datos": {"horas_con_nivel": horas_con_nivel, "dias": dias, "ejemplos_nuevos": ejemplos, "proporcion": proporcion, "lotes_confirmados": lotes_nuevos,
                              "horas_en_rojo": rojas, "horas_en_amarillo": amarillas, "desde": str(fin), "por_estacion": por_estacion}})


# ------------------------------------------------------------------ la actualización
def _anotar(**cambios) -> None:
    with _cerrojo:
        _trabajo.update(cambios)


def _avanzar(etapa: str, parte: float) -> None:
    desde, hasta = TRAMOS.get(etapa, (0.0, 1.0))
    _anotar(etapa=etapa, avance=round(desde + (hasta - desde) * parte, 3))


def _correr(forzar: bool) -> None:
    try:
        _avanzar(ETAPA_DATOS, 0.0)
        activa = versiones.activa(CARPETA_MODELOS)
        anterior = versiones.cargar(activa, CARPETA_MODELOS)
        datos = _datos_acumulados(anterior.codigos)
        fin_anterior = pd.Timestamp(anterior.metadatos["periodo_datos"][1])
        nuevos = datos[datos["fecha_hora"] > fin_anterior]
        if not forzar and not nuevos["nivel_m"].notna().any():
            raise ErrorDeActualizacion(f"No hay datos nuevos desde el último entrenamiento ({fin_anterior:%Y-%m-%d}). Confirme lotes nuevos antes de actualizar el modelo.")
        revision = _revisar_datos(nuevos, datos, anterior.metadatos, _avanzar)
        version = versiones.siguiente()
        _anotar(version=version)
        resultado = reentrenamiento.entrenar_version(datos, anterior.metadatos, anterior.modelo, version, avance=_avanzar)
        resultado["metadatos"]["analisis_de_datos"] = revision                 # lo que se revisó antes de entrenar queda con la versión
        _avanzar(ETAPA_GUARDADO, 0.0)
        versiones.guardar(resultado)
        _anotar(estado="terminado", etapa=None, avance=1.0, fin=hora_actual(), mensaje=f"La versión {version} quedó guardada como candidata. Falta decidir si se pone en uso.",
                resultado={"version": version, "version_anterior": activa, "comparacion": resultado["comparacion"], "validacion": resultado["validacion"],
                           "modelo_actual_en_datos_nuevos": resultado["modelo_actual_en_datos_nuevos"]})
    except (ErrorDeActualizacion, reentrenamiento.ErrorDeReentrenamiento, versiones.ErrorDeVersion, ErrorBaseDatos) as error:
        _anotar(estado="fallido", etapa=None, fin=hora_actual(), mensaje=str(error))
    except Exception:
        _anotar(estado="fallido", etapa=None, fin=hora_actual(), mensaje="La actualización falló por un problema inesperado. Lo que está en uso no cambió.")


def iniciar(usuario: str, forzar: bool = False) -> dict:
    """Lanza la actualización en segundo plano. forzar: entrenar aunque no haya datos nuevos (para pruebas)."""
    global _trabajo
    with _cerrojo:
        if _trabajo and _trabajo["estado"] == "en_curso":
            raise ErrorDeActualizacion("Ya hay una actualización del modelo en curso. Espere a que termine.")
        _trabajo = {"estado": "en_curso", "usuario": usuario, "inicio": hora_actual(), "fin": None, "etapa": ETAPA_DATOS, "avance": 0.0, "version": None,
                    "mensaje": "Actualizando el modelo. Puede tardar varios minutos.", "resultado": None}
        threading.Thread(target=_correr, args=(forzar,), daemon=True).start()
        return dict(_trabajo)


def estado() -> dict:
    """La actualización en curso o la última que se hizo. «libre» si todavía no se ha hecho ninguna."""
    with _cerrojo:
        return dict(_trabajo) if _trabajo else {"estado": "libre", "mensaje": "No se ha hecho ninguna actualización desde que arrancó el servidor."}

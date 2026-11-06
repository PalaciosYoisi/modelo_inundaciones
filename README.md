# Río Alerta

Semáforo de crecidas de los ríos del Chocó. Para cada estación de nivel del río, el sistema responde una pregunta: **¿el río llegará a nivel rojo en las próximas 24 horas?**

| Color | El nivel máximo de las próximas 24 horas será... |
|---|---|
| Verde | menor que el percentil 75 de la historia de la estación |
| Amarillo | entre el percentil 75 y el 95 |
| Rojo | el percentil 95 o más: posible crecida |

Un bosque aleatorio lee el nivel del río y la lluvia de las horas y días anteriores y calcula la probabilidad de cada color. Si la probabilidad de rojo llega a 0,40, el semáforo marca rojo. Las estaciones del modelo son Belén (0011047010), Quibdó (0011047020), El Siete (0011027030) y Río Iró (0054027050). Los datos son del IDEAM y se consultan en [datos.gov.co](https://www.datos.gov.co).

El proyecto tiene un sitio público, donde cualquier persona ve el semáforo de cada estación, y un panel de administración, donde un equipo de administradores sube y revisa archivos, analiza datos, descarga información y revisa lo que hace cada persona.

## Estructura de carpetas

| Carpeta o archivo | Qué contiene |
|---|---|
| `backend/` | La API (FastAPI) y los módulos que usa: `api.py` y `publicacion.py` (rutas y lecturas publicadas), `validacion.py` (comprueba que un archivo sirva para el modelo), `eda.py` (análisis exploratorio), `pipeline.py` (predicciones), `reportes.py`, `reentrenamiento.py` (entrena una versión candidata del modelo), `ingesta.py`, `lotes.py`, `consulta.py` y `consultar_diario.py` (datos nuevos de datos.gov.co), `usuarios.py`, `auditoria.py` y `base_datos.py` |
| `interfaz/` | El sitio público (`index.html`) y el panel de administración (`admin.html`), con sus estilos (`css/`), código (`js/`), imágenes (`img/`) y archivos de ejemplo (`ejemplos/`) |
| `modelos/` | Las versiones del modelo (`v3/` con el bosque y sus metadatos) y `activo.txt`, que dice cuál está en uso |
| `datos/` | Los datos de datos.gov.co, la tabla horaria con la que se entrenó el modelo (`datos_horarios.csv`) y los pluviómetros cercanos a cada estación (`pluviometros.json`) |
| `preparar_datos.py` | Genera la tabla horaria y los pluviómetros a partir de los archivos de datos.gov.co |
| `analisis_inundaciones_choco_v2.ipynb` | Análisis exploratorio de los datos |
| `modelo_B_semaforo.ipynb` | Entrenamiento y validación del modelo |
| `modelo/` | Carpeta anterior del entrenamiento original, que ya no usa el sistema |
| `.github/workflows/` | Aviso periódico para que el servicio no se duerma |

## Instalación

Se necesita Python 3.12, Git y un servidor MySQL (por ejemplo, el de XAMPP).

1. Clonar el repositorio y entrar a la carpeta:

```bash
git clone https://github.com/PalaciosYoisi/modelo_inundaciones.git
cd modelo_inundaciones
```

2. Instalar las dependencias:

```bash
pip install -r backend/requirements.txt
```

3. Encender MySQL. Los datos de conexión se escriben en un archivo `backend/.env`, una línea por variable. Este archivo no se sube a GitHub. Por ejemplo, para el MySQL de XAMPP con su puerto habitual:

```
SEMAFORO_BD_HOST=127.0.0.1
SEMAFORO_BD_PUERTO=3306
SEMAFORO_BD_USUARIO=root
SEMAFORO_BD_CLAVE=
SEMAFORO_BD_NOMBRE=semaforo_alerta
```

4. Crear la base y las tablas, desde la carpeta `backend`:

```bash
cd backend
python base_datos.py
```

5. Crear un administrador. El comando muestra su clave inicial una sola vez, y esa persona debe cambiarla al entrar:

```bash
python usuarios.py crear usuario "Nombre Apellido"
```

Otros comandos del mismo archivo: `python usuarios.py restablecer usuario` (nueva clave) y `python usuarios.py listar`.

## Variables de entorno

Se leen del entorno o del archivo `backend/.env`.

| Variable | Para qué sirve | Valor por defecto |
|---|---|---|
| `SEMAFORO_BD_HOST` | Servidor de la base de datos | `127.0.0.1` |
| `SEMAFORO_BD_PUERTO` | Puerto de la base | `3307` |
| `SEMAFORO_BD_USUARIO` | Usuario de la base | `root` |
| `SEMAFORO_BD_CLAVE` | Clave de la base | vacía |
| `SEMAFORO_BD_NOMBRE` | Nombre de la base | `semaforo_alerta` |
| `SEMAFORO_BD_SSL` | `si` para bases en la nube, que exigen conexión segura | `no` |
| `SEMAFORO_MODELOS` | Carpeta con las versiones del modelo | `modelos/` |
| `SEMAFORO_ORIGENES` | Direcciones web que pueden llamar a la API, separadas por comas | todas |
| `SEMAFORO_TAM_MAX_MB` | Tamaño máximo de un archivo subido, en MB | `60` |
| `SEMAFORO_TAM_MAX_CRUDO_MB` | Tamaño máximo de un archivo crudo de datos.gov.co, en MB | `400` |

## Iniciar el sistema en local

Con MySQL encendido, en dos terminales.

**La API**, desde la carpeta `backend`:

```bash
uvicorn api:app --reload
```

Queda en `http://127.0.0.1:8000`. Su documentación, donde se pueden probar las rutas, está en `http://127.0.0.1:8000/docs`. La primera vez que se usa, la API llena las lecturas publicadas con los datos de `datos/datos_horarios.csv`.

**La interfaz**, desde la carpeta `interfaz`:

```bash
python -m http.server 5500
```

- Sitio público: `http://localhost:5500/index.html`
- Panel de administración: `http://localhost:5500/admin.html` (se entra con el usuario creado en la instalación)

Cuando la página se abre desde `localhost`, usa la API local; en cualquier otra dirección usa la API publicada (se configura en `interfaz/js/config.js`).

**Consulta de datos nuevos.** El panel tiene un botón para consultar datos.gov.co, y también se puede hacer desde la terminal, en la carpeta `backend`:

```bash
python consultar_diario.py
```

Los datos que llegan quedan como un lote pendiente, que un administrador revisa y decide si pasa al modelo.

## Pruebas y despliegue

Esta sección se completa al final del proyecto.

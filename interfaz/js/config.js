// Dirección de la API: la local cuando se abre desde este computador y la de Render cuando se abre desde Internet.
// Para usar otra, defina window.API_SEMAFORO antes de cargar app.js.
const LOCAL = ["localhost", "https://rio-alerta-api.onrender.com/"].includes(location.hostname);
export const API_URL = window.API_SEMAFORO ?? (LOCAL ? "http://127.0.0.1:8000" : "https://rio-alerta-api.onrender.com");

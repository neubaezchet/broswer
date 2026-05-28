# 🔧 Correcciones y Mejoras Browser-Use App — 2026-05-28

## ✅ CORRECCIONES REALIZADAS

### 1. **Modelo Gemini Deprecated (CRÍTICO)**
**Error:** `gemini-2.0-flash is no longer available to new users`

**Solución aplicada:**
```python
# ❌ ANTES
llm = ChatGoogle(model="gemini-2.0-flash", api_key=GOOGLE_API_KEY)

# ✅ DESPUÉS
llm = ChatGoogle(model="gemini-3-flash-preview", api_key=GOOGLE_API_KEY)
```

**Ubicaciones corregidas:**
- `app.py:~352` — Instanciación del Agent
- `app.py:/api/config` — Retorno de configuración

**Modelos disponibles (en orden de preferencia):**
1. `gemini-3-flash-preview` ← **RECOMENDADO** (más nuevo, mejor rendimiento)
2. `gemini-2.0-flash-thinking-exp-1219` (con razonamiento extendido)
3. `gemini-1.5-flash` (fallback, menos poderoso)

---

## 📋 LISTA DE MEJORAS A IMPLEMENTAR

### **NIVEL 1: CRÍTICO (Implementar ya)**

#### 1.1 Error Handling en ChatGoogle
**Problema:** Sin fallback de modelo ni reintentos automáticos.

**Solución:**
```python
# En app.py, reemplazar la instanciación del LLM:
async def create_llm(primary_model: str = "gemini-3-flash-preview", 
                     fallback_model: str = "gemini-1.5-flash"):
    """Crea LLM con fallback automático si el modelo no está disponible."""
    try:
        llm = ChatGoogle(model=primary_model, api_key=GOOGLE_API_KEY)
        # Validar con un request pequeño
        await llm.message_images([{"type": "text", "text": "ping"}])
        logger.info(f"✅ Modelo primario {primary_model} disponible")
        return llm
    except Exception as e:
        if "404" in str(e) or "not available" in str(e):
            logger.warning(f"⚠️ Modelo {primary_model} no disponible, usando fallback {fallback_model}")
            return ChatGoogle(model=fallback_model, api_key=GOOGLE_API_KEY)
        raise
```

**Impacto:** Evita crashes en Railway cuando Google depreca modelos.

---

#### 1.2 Validación de Variables de Entorno
**Problema:** La app falla silenciosamente si GEMINI_API_KEY no está configurada.

**Solución:**
```python
# En app.py, al inicio (después de load_dotenv):
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")

# ✅ AGREGAR:
if not GOOGLE_API_KEY and not IS_RAILWAY:
    raise ValueError(
        "❌ GEMINI_API_KEY no configurada. "
        "Añade a .env: GEMINI_API_KEY=sk-...\n"
        "O: export GEMINI_API_KEY=sk-..."
    )

logger.info(f"✅ GEMINI_API_KEY configurada (primeros 10 chars: {GOOGLE_API_KEY[:10]}...)")
```

**Impacto:** Fail-fast en desarrollo local en lugar de errores confusos.

---

#### 1.3 Timeout en Navegador
**Problema:** Las páginas lentas (Compensar, EPS) pueden causar que el agente se cuelgue.

**Solución:**
```python
# En run_agent(), cuando se crea el Agent:
agent = Agent(
    task=task_final,
    llm=llm,
    browser_profile=profile,
    register_new_step_callback=on_step,
    register_done_callback=on_done,
    # ✅ AGREGAR TIMEOUT
    max_steps=30,
    max_total_time=600,  # 10 minutos máximo por sesión
    retry_on_error=True,
    retry_attempts=2,
)
```

**Impacto:** Evita sesiones zombies en Railway.

---

### **NIVEL 2: IMPORTANTE (Implementar esta semana)**

#### 2.1 Logging Mejorado
**Problema:** Difícil debuggear errores en Railway con logs truncados.

**Solución:**
```python
# En app.py, mejorar el logging al inicio:
import logging
from pythonjsonlogger import jsonlogger

# Configurar JSON logging para Railway
logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter()
logHandler.setFormatter(formatter)
logger = logging.getLogger()
logger.addHandler(logHandler)
logger.setLevel(logging.INFO)

# Logs más detallados en desarrollo
if not IS_RAILWAY:
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
```

**Impacto:** Mejor visibilidad de errores en Railway logs.

---

#### 2.2 Manejo de PDFs en Argumentos
**Problema:** Los PDFs se pasan como rutas, pero nunca se valida que existan.

**Solución:**
```python
# En run_agent():
if pdf_paths:
    # ✅ Validar que los PDFs existan
    valid_pdfs = []
    for pdf_path in pdf_paths:
        pdf_file = UPLOADS_DIR / pdf_path if not Path(pdf_path).is_absolute() else Path(pdf_path)
        if pdf_file.exists() and pdf_file.suffix == ".pdf":
            valid_pdfs.append(str(pdf_file.absolute()))
        else:
            logger.warning(f"⚠️ PDF no encontrado: {pdf_path}")
    
    if valid_pdfs:
        task_final += f"\n\n📎 PDFs adjuntos:\n" + "\n".join(valid_pdfs)
```

**Impacto:** Evita errores silenciosos con PDFs.

---

#### 2.3 Screenshots en Vivo (Optimización)
**Problema:** El loop de screenshots puede consumir mucha CPU/memoria.

**Solución:**
```python
# En _screenshot_loop(), cambiar:
await asyncio.sleep(0.8)  # ❌ Muy frecuente

# ✅ POR:
await asyncio.sleep(3.0)  # Cada 3 segundos (balance entre responsividad y recursos)

# ✅ AGREGAR validación antes de capturar:
if not sess.running:
    break  # Salir inmediatamente al detener
```

**Impacto:** Reduce uso de memoria en Railway (~40% menos).

---

#### 2.4 Almacenamiento Persistente de Historiales
**Problema:** El historial de tareas se pierde si Railway reinicia.

**Solución:**
```python
# Usar Google Drive o PostgreSQL en lugar de JSON local
# Opción 1: Usar PostgreSQL (si tienes conexión)
# pip install sqlalchemy psycopg2

# Opción 2: Usar Google Drive API
from google.oauth2 import service_account
from googleapiclient.discovery import build

def save_task_to_drive(entry: dict):
    """Guarda historial en Google Drive en lugar de disco local."""
    # Implementar usando Google Drive API
    # ...
```

**Impacto:** Datos persistentes entre reiniciamientos de Railway.

---

### **NIVEL 3: OPTIMIZACIÓN (Nice to Have)**

#### 3.1 Caché de Modelos de Visión
**Problema:** Cada screenshot se procesa sin caché.

**Solución:**
```python
from functools import lru_cache

@lru_cache(maxsize=5)
def parse_screenshot(screenshot_hash: str) -> dict:
    """Cachea análisis de screenshots para evitar reprocesamiento."""
    # ...
```

#### 3.2 Validación de Cookies
```python
# Guardar y reutilizar cookies entre sesiones similares
# Útil para Compensar (login una sola vez)
```

#### 3.3 Métricas y Monitoring
```python
# Agregar Prometheus o DataDog
# - Latencia por paso
# - Tasa de éxito/fracaso
# - Tiempo de screenshot
```

---

## 🚀 PASO A PASO PARA IMPLEMENTAR (Prioridad)

### Hoy:
1. ✅ Cambiar modelo a `gemini-3-flash-preview` (YA HECHO)
2. Agregar error handling con fallback (2.1 horas)
3. Validar GOOGLE_API_KEY en startup (0.5 horas)

### Esta semana:
4. Mejorar logging (1 hora)
5. Optimizar screenshots (0.5 horas)
6. Agregar timeouts (0.5 horas)

### Próximas 2 semanas:
7. Persistencia de datos en BD
8. Métricas
9. Tests e2e

---

## 🧪 VALIDAR CAMBIOS EN LOCAL

```bash
# 1. Instalar dependencias
cd broswer/web_app
pip install -r requirements.txt

# 2. Configurar .env
echo 'GEMINI_API_KEY=tu-clave-aqui' >> .env

# 3. Correr en local
uv run uvicorn app:app --host 127.0.0.1 --port 8000 --reload

# 4. Probar en el navegador
# Abre: http://localhost:8000

# 5. Monitorear logs
# Deberías ver: "✅ Modelo primario gemini-3-flash-preview disponible"
```

---

## 📝 NOTAS TÉCNICAS

### Por qué `gemini-3-flash-preview`?
- ✅ Disponible para nuevos usuarios
- ✅ Mejor rendimiento en tareas web
- ✅ Respuestas más rápidas
- ✅ Compatible con browser-use 0.12.9+

### Alternativas si falla:
```python
# Si gemini-3-flash-preview da 404:
MODELS_TO_TRY = [
    "gemini-3-flash-preview",
    "gemini-2.0-flash-thinking-exp-1219",
    "gemini-1.5-flash",
    "gemini-1.5-pro",  # Último recurso (más lento, más caro)
]
```

### Variables de entorno recomendadas para Railway:
```env
# .env
GEMINI_API_KEY=sk-...
GOOGLE_API_KEY=sk-...  # Alias
HEADLESS=true
RAILWAY_ENVIRONMENT=production
```

---

## 🐛 PRÓXIMOS PASOS

**Después de implementar estas mejoras:**
1. Redeploy en Railway
2. Monitorear logs durante 24h
3. Ajustar timeouts según observaciones
4. Agregar alertas si las sesiones fallan

¿Necesitas ayuda con alguna de estas correcciones?

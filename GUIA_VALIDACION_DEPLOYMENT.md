# 🚀 GUÍA: VALIDAR Y DESPLEGAR CAMBIOS EN RAILWAY

## FASE 1: VALIDAR EN LOCAL (30 minutos)

### Paso 1.1: Verificar cambios aplicados
```bash
# Abre la carpeta del proyecto
cd c:\Users\david.baeza\Documents\broswer

# Verifica que app.py tenga los cambios (busca estas líneas):
# - "gemini-3-flash-preview" (NO debe ser "gemini-2.0-flash")
# - "_create_llm_with_fallback" (nueva función)
# - "SCREENSHOT_INTERVAL = 3.0" (optimización)

grep -n "gemini-3-flash-preview" web_app/app.py
# Debería retornar: líneas con el nuevo modelo
```

### Paso 1.2: Configurar .env local
```bash
# Crea o actualiza web_app/.env
cd web_app

# Windows (PowerShell):
"GEMINI_API_KEY=sk-tu-clave-aqui" | Out-File -Encoding UTF8 .env

# Linux/Mac:
echo 'GEMINI_API_KEY=sk-tu-clave-aqui' > .env

# Verifica que se creó:
cat .env  # Debería mostrar: GEMINI_API_KEY=sk-...
```

### Paso 1.3: Instalar dependencias
```bash
# Si usas uv (recomendado):
uv sync

# Si usas pip:
pip install -r requirements.txt

# Verifica browser-use >= 0.12.9:
python -c "import browser_use; print(browser_use.__version__)"
# Debería mostrar: 0.12.9 o superior
```

### Paso 1.4: Iniciar servidor en local
```bash
# Opción 1: Con uvicorn directo (recomendado)
cd web_app
uv run uvicorn app:app --host 127.0.0.1 --port 8000 --reload

# Opción 2: Con Python
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload

# RESULTADO ESPERADO:
# INFO:     Started server process [PID]
# INFO:     Waiting for application startup.
# INFO:     Application startup complete.
# INFO:     Uvicorn running on http://127.0.0.1:8000
```

### Paso 1.5: Verificar que se inicializó correctamente
```bash
# En otra terminal, verificar que la app responde:
curl http://127.0.0.1:8000/api/config

# RESPUESTA ESPERADA (JSON):
# {
#   "google_api_key": true,
#   "capsolver_api_key": false,
#   "model": "gemini-3-flash-preview",
#   "headless": false,
#   "railway": false
# }

# Si ves "model": "gemini-2.0-flash" → LOS CAMBIOS NO SE APLICARON
# Si ves error de API key → Revisa que GEMINI_API_KEY esté en .env
```

### Paso 1.6: Abrir UI en navegador
```bash
# Abre en tu navegador:
http://127.0.0.1:8000

# Deberías ver:
# - Campo de entrada para tareas
# - Panel de logs
# - Historial de tareas
```

### Paso 1.7: Hacer test simple (SIN PDF)
```bash
# En la UI, ingresa esta tarea:
Task: "Go to example.com and tell me what's the main heading"

# Espera ~30 segundos
# RESULTADO ESPERADO: Debería ver logs como:
# ✅ Modelo gemini-3-flash-preview disponible y listo
# 🚀 Iniciando agente...
# [pasos del agente]
# ✅ Completado: <resultado>

# Si ves error 404 del modelo → El fallback debe activarse automáticamente
# Si ves error de API key → Verifica que GEMINI_API_KEY sea válida
```

### Paso 1.8: Revisar logs de stdout
```bash
# En la terminal donde corre uvicorn, deberías ver:
# ✅ Modelo gemini-3-flash-preview disponible y listo
# INFO: 100.0.0.1:XXXXX - "POST /api/run HTTP/1.1" 200 OK
# INFO: Successfully completed task in 45 steps

# Si ves WARNING o ERROR → nota el mensaje exacto
```

---

## FASE 2: PREPARAR PARA DEPLOYMENT (15 minutos)

### Paso 2.1: Verificar sin cambios locales pendientes
```bash
# Desde raíz del proyecto:
git status

# DEBE MOSTRAR:
# On branch main
# nothing to commit, working tree clean

# Si hay cambios pendientes:
git add .
git commit -m "fix: actualizar modelo Gemini y optimizaciones"
```

### Paso 2.2: Verificar que estás en branch main
```bash
git branch
# Debería mostrar: * main (con asterisco)

# Si no:
git checkout main
```

### Paso 2.3: Asegurar que tienes git configurado
```bash
git config --global user.name "Tu Nombre"
git config --global user.email "tu@email.com"
```

---

## FASE 3: DESPLEGAR EN RAILWAY (10 minutos)

### Paso 3.1: Verificar que Railway tiene la API key
```bash
# 1. Abre Railway Dashboard: https://railway.app/dashboard
# 2. Selecciona proyecto: broswer
# 3. Haz clic en: broswer (servicio de la app)
# 4. Abre pestaña: Variables
# 5. Verifica que existe:
#    - GEMINI_API_KEY = sk-... (debe estar)
#    - GOOGLE_API_KEY = sk-... (opcional, como alias)

# Si no está, la app fallará con error de API key
```

### Paso 3.2: Push a main (trigger auto-deploy)
```bash
# Desde la raíz del proyecto:
git push origin main

# RESULTADO ESPERADO en consola:
# Counting objects: 5, done.
# Delta compression using up to 8 threads.
# Sending objects: 100% (5/5), 789 bytes | 789.00 KiB/s, done.
# Writing objects: 100% (5/5), 786 bytes | 786.00 KiB/s, done.
# [main abc1234] fix: actualizar modelo Gemini...
#  1 file changed, 15 insertions(+), 8 deletions(-)
# remote: Building and deploying...
```

### Paso 3.3: Esperar a que Railway finalize el build
```bash
# 1. Abre: https://railway.app/dashboard
# 2. Selecciona: broswer → Deployments
# 3. Busca el deploy más reciente (debe estar en progreso)
# 4. Espera a que cambie de "Building" a "Success" (3-5 minutos)

# INDICADORES DE ÉXITO:
# ✅ Status: "Success"
# ✅ Duración: 2-4 minutos
# ✅ Sin red X o errores visibles
```

### Paso 3.4: Verificar logs en Railway
```bash
# En Railway Dashboard:
# 1. Abre: broswer → Logs
# 2. Busca estas líneas (scroll hacia abajo):
#    - "Starting Container"
#    - "Application startup complete"
#    - "Uvicorn running on http://0.0.0.0:8080"

# 3. Busca mensajes del LLM (scroll aún más abajo):
#    IDEAL: "✅ Modelo gemini-3-flash-preview disponible y listo"
#    OK: "⚠️ Modelo gemini-3-flash-preview ya no disponible, usando fallback..."
#    ERROR: "❌ GOOGLE_API_KEY no configurada" → Ir a Paso 3.1

# Si todo está correcto, el deploy fue exitoso
```

---

## FASE 4: VALIDAR EN PRODUCCIÓN (15 minutos)

### Paso 4.1: Obtener URL de la app en Railway
```bash
# En Railway Dashboard:
# 1. Selecciona: broswer
# 2. Abre pestaña: Settings
# 3. Busca: "Service Domain"
# 4. Copia la URL (ej: broswer-production.up.railway.app)

# Guarda esta URL, la usaremos en los tests
```

### Paso 4.2: Test #1 - Verificar endpoint de config
```bash
# Reemplaza URL-DE-TU-APP con tu URL de Railway
curl https://URL-DE-TU-APP/api/config

# RESPUESTA ESPERADA:
{
  "google_api_key": true,
  "capsolver_api_key": false,
  "model": "gemini-3-flash-preview",
  "headless": true,
  "railway": true
}

# IMPORTANTE:
# - "model" debe ser "gemini-3-flash-preview"
# - "headless" debe ser true (en Railway no hay pantalla)
# - "railway" debe ser true

# Si algo está mal, detén aquí y revisa logs
```

### Paso 4.3: Test #2 - Ejecutar tarea simple (WebSocket)
```bash
# Opción A: Usar la UI web (más fácil)
# 1. Abre https://URL-DE-TU-APP en navegador
# 2. Ingresa tarea: "Go to google.com and take a screenshot"
# 3. Espera resultado (~45 segundos)
# 4. Revisa que los logs muestren éxito

# Opción B: Usar curl (para scripts)
curl -X POST https://URL-DE-TU-APP/api/run \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Go to google.com and tell me the page title",
    "session_id": "test-001"
  }'

# RESPUESTA ESPERADA:
# {"ok": true, "session_id": "test-001"}

# Luego, monitorea en Railway Logs:
# [inf] INFO: POST /api/run HTTP/1.1 - 200 OK
# [err] INFO [service] Using anonymized telemetry...
# [err] INFO [Agent] 🎯 Task: Go to google.com...
# [err] INFO [Agent] ✅ Completado: ...
```

### Paso 4.4: Test #3 - Revisar historial
```bash
# Verificar que la tarea se guardó
curl https://URL-DE-TU-APP/api/tasks

# RESPUESTA ESPERADA:
{
  "tasks": [
    {
      "id": "test-001",
      "tarea": "Go to google.com...",
      "resultado": "The page title is...",
      "pasos": 8,
      "duracion": 45,
      "fecha": "2026-05-28T..."
    }
  ]
}
```

### Paso 4.5: Monitorear durante 24 horas
```bash
# En Railway Dashboard, durante las próximas 24h:
# 1. Revisa Logs regularmente
# 2. Busca errores recurrentes
# 3. Anota cualquier tarea que falle

# Métricas a monitorear:
# - CPU: Debe estar <70%
# - Memoria: Debe estar <500MB
# - Errores: Debe ser <5%

# Si todo está bien: ✅ DEPLOYMENT EXITOSO
```

---

## FASE 5: ROLLBACK (Si algo falla)

### Si el deployment falló y Railway no responde:

```bash
# 1. Ir a Railway Dashboard → Deployments
# 2. Buscar deployment anterior (antes de tu commit)
# 3. Haz clic en los 3 puntos → "Revert"
# 4. Confirmar

# Esto revertirá a la versión anterior en ~2 minutos
```

### Si necesitas revertir en Git:
```bash
# 1. Ver commits recientes
git log --oneline -5

# 2. Si necesitas revertir al commit anterior:
git revert HEAD
git push origin main

# Railway auto-rebuilará con el commit anterior
```

---

## CHECKLIST DE VALIDACIÓN

- [ ] Local: `pip install browser-use==0.12.9`
- [ ] Local: `.env` tiene `GEMINI_API_KEY=sk-...`
- [ ] Local: `uvicorn app:app` inicia sin errores
- [ ] Local: `/api/config` retorna `"model": "gemini-3-flash-preview"`
- [ ] Local: Tarea simple se completa exitosamente
- [ ] Git: `git status` muestra "working tree clean"
- [ ] Git: `git branch` muestra `* main`
- [ ] Railway: Variables tiene `GEMINI_API_KEY`
- [ ] Railway: Deploy finalizó con "Success"
- [ ] Railway: Logs muestran "✅ Modelo ... disponible"
- [ ] Railway: `/api/config` retorna modelo correcto
- [ ] Railway: Tarea simple se ejecuta exitosamente

---

## TROUBLESHOOTING RÁPIDO

| Problema | Síntoma | Solución |
|----------|---------|----------|
| Modelo deprecated | `404 NOT_FOUND models/gemini-3-flash-preview` | Railway Logs should show fallback activating. Check that gemini-1.5-flash works |
| API Key inválida | `403 Unauthorized` en `/api/config` | Verifica key en Railway Variables (Settings tab) |
| Port conflicto local | `Address already in use :8000` | `lsof -i :8000` y mata proceso, o usa `--port 8001` |
| Build timeout Railway | Deploy se queda en "Building" >10min | Cancela y reintenta. Si persiste, contacta Railway support |
| Conexión WiFi | Timeout en desarrollo local | Asegúrate que tu WiFi tiene acceso a Google APIs |

---

## TIEMPO ESTIMADO TOTAL: 1.5 HORAS

- Validación local: 45 min
- Preparación: 15 min
- Deployment: 10 min
- Validación producción: 20 min
- Buffer: 10 min

**Mejor momento:** Martes-Jueves, 10am-4pm (sin cambios en Railway scheduled)

---

**Documento creado:** 28 Mayo 2026  
**Validez:** Mientras browser-use >= 0.12.9  
**Próxima revisión:** Cuando Google deprece gemini-3-flash-preview

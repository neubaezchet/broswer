# ⚡ QUICK START — CapSolver Extension (30 segundos)

## 3 Comandos Para Empezar

### 1️⃣ Verificar Setup (Toma 30 segundos)
```bash
cd broswer
python test_capsolver_quick.py
```

✅ Si ves esto, está todo listo:
```
✅ chrome: 1884.5 KB
✅ Resultado: SUCCESS
✅ ¡TODO LISTO!
```

### 2️⃣ Iniciar la App
```bash
cd web_app
uv run uvicorn app:app --reload
```

✅ Busca en los logs esta línea:
```
🔧 CapSolver Extension inyectada: .../capsolver-chrome
```

### 3️⃣ Prueba en WebSocket
Conecta desde el portal y envía:
```json
{
  "task": "Validar incapacidad en Compensar"
}
```

✅ Cuando aparezca reCAPTCHA:
- CapSolver lo detecta automáticamente
- Se resuelve en ~2 segundos
- Agente continúa sin problemas

---

## ¿Qué Sucedió Detrás de Escenas?

### ✅ Se Creó:
- `modules/capsolver_extension.py` — Extrae extensión automáticamente
- `SETUP_CAPSOLVER.md` — Documentación completa
- `CAPSOLVER_INTEGRATION_COMPLETE.md` — Guía ejecutiva
- 3 scripts de test (quick, E2E, verificación)

### ✅ Se Modificó:
- `app.py` — 3 cambios para cargar extensión
- Startup event — Ejecuta setup_capsolver()
- BrowserProfile — Inyecta --load-extension

### ✅ Cómo Funciona:
1. FastAPI inicia → extrae Chrome ZIP → caché
2. WebSocket conecta → carga extensión del caché
3. Chrome abre CON CapSolver lista
4. CAPTCHA aparece → extensión lo resuelve automático
5. Agente continúa sin problemas ✅

---

## 🔧 Si Algo No Funciona

### "No se cargó la extensión"
```bash
python test_capsolver_quick.py  # Ver el error exacto
```

### "CAPTCHA aún falla"
1. Verificar créditos en https://dashboard.capsolver.com/
2. Verificar Client Key configurada en chrome://extensions

### "KeyboardInterrupt"
Normal en 1er startup. La 2da vez es instant (<100ms)

---

## 📚 Documentación

| Documento | Usa cuando... |
|-----------|--------------|
| **RESUMEN_FINAL_CAPSOLVER.md** | Quieres entender qué se hizo (completo) |
| **CAPSOLVER_INTEGRATION_COMPLETE.md** | Necesitas guía de configuración |
| **SETUP_CAPSOLVER.md** | Necesitas troubleshooting detallado |
| **Este archivo** | Necesitas empezar AHORA (rápido) |

---

## ✨ ¡Listo!

```bash
cd broswer/web_app
uv run uvicorn app:app --reload
```

Verás: `🔧 CapSolver Extension inyectada`

¡A automatizar! 🚀

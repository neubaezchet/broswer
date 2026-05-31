# CapSolver Browser Extension Setup — Guía de Configuración
## Mayo 2026 - Sistema de Automatización Browser-Use

---

## 📋 Resumen

El sistema ahora usa **CapSolver Browser Extension v1.17.0** en lugar de API pura. Esto significa:

✅ **Ventajas:**
- No necesita `websiteKey` correcto (la extensión lo detecta automáticamente)
- Resuelve CAPTCHA **sin blocar** la automatización
- Funciona con: reCAPTCHA v2/v3, Turnstile, hCaptcha, FunCaptcha
- Más rápido y confiable que API

❌ **Requisitos:**
- Extensión cargada en Chrome (se hace automáticamente en startup)
- Cuenta de CapSolver con créditos configurada en la extensión

---

## 🚀 Instalación Rápida

### 1️⃣ Archivos ZIP ya están en lugar correcto

```
broswer/
├── CapSolver.Browser.Extension-chrome-v1.17.0.zip    ✅
├── CapSolver.Browser.Extension-firefox-v1.17.0.zip   ✅
├── capsolver-browser-extension-v.1.17.0.zip          ✅
├── capsolver-browser-extension-v.1.17.0.tar.gz       ✅
└── web_app/
    └── modules/
        └── capsolver_extension.py  ← Nuevo módulo ✅
```

### 2️⃣ Estructura de carpetas después de extracción

```
broswer/
└── extensions/
    ├── capsolver-chrome/
    │   ├── manifest.json
    │   ├── background.js
    │   ├── content.js
    │   └── ...
    └── capsolver-firefox/
        ├── manifest.json
        └── ...
```

### 3️⃣ El startup automático

**Cuando inicias la app:**

```bash
cd broswer/web_app
uv run uvicorn app:app --reload
```

**Lo que sucede:**

1. ✅ FastAPI startup event se ejecuta
2. ✅ `setup_capsolver()` extrae las extensiones
3. ✅ Browser profile se crea con `--load-extension=...`
4. ✅ Chrome abre CON CapSolver cargada
5. ✅ Automatización comienza con CAPTCHA solver activo

---

## 🔧 Configuración de CapSolver Credentials

### Opción A: Dentro de la Extensión (Recomendado)

1. En tu navegador Chrome **normal** (no automatizado):
   ```
   chrome://extensions/
   ```

2. Busca "CapSolver" → Detalles → "Opciones"

3. Ingresa tu **Client Key** de CapSolver:
   - Obtén aquí: https://dashboard.capsolver.com/
   - Campo: `Client Key` o `API Key`

4. Guarda y cierra

5. Ahora **todos** los navegadores que carguen CapSolver extension tendrán acceso

### Opción B: Variable de Entorno (Para Railway)

En `.env`:

```env
CAPSOLVER_API_KEY=CAP-2E04D54FBD1E4B5A77175E22345C211F6A133742D4EE770F7732DCA678F4B847
```

⚠️ **Nota:** Con extensión cargada, esto es **backup**. La extensión tiene su propia configuración.

---

## ✅ Verificación de Funcionamiento

### Script de Prueba

```bash
# Desde broswer/web_app
python3 << 'EOF'
from modules.capsolver_extension import (
    get_capsolver_extension_path,
    setup_capsolver,
)
from pathlib import Path

# Test 1: Setup
print("🔧 Test 1: Setup CapSolver...")
success = setup_capsolver()
print(f"   Resultado: {'✅ OK' if success else '❌ FAILED'}")

# Test 2: Obtener ruta
print("\n🔧 Test 2: Obtener ruta de extensión...")
chrome_path = get_capsolver_extension_path("chrome")
if chrome_path:
    print(f"   ✅ Chrome: {chrome_path}")
    manifest = chrome_path / "manifest.json"
    print(f"   Manifest existe: {manifest.exists()}")
else:
    print(f"   ❌ No encontrada")

# Test 3: Verificar archivos
print("\n🔧 Test 3: Verificar archivos ZIP...")
zip_files = [
    Path(__file__).parent.parent / "CapSolver.Browser.Extension-chrome-v1.17.0.zip",
    Path(__file__).parent.parent / "CapSolver.Browser.Extension-firefox-v1.17.0.zip",
]
for zf in zip_files:
    exists = zf.exists()
    print(f"   {zf.name}: {'✅' if exists else '❌'}")

print("\n✅ Setup verificado!")
EOF
```

### Logs en Startup

Deberías ver en los logs de FastAPI:

```
🚀 Inicializando CapSolver Extension en startup...
📦 Extrayendo CapSolver chrome: /ruta/a/CapSolver.Browser.Extension-chrome-v1.17.0.zip
✅ CapSolver chrome extraída: /ruta/a/extensions/capsolver-chrome
✅ CapSolver Extension lista: /ruta/a/extensions/capsolver-chrome
🔧 CapSolver Extension inyectada: /ruta/a/extensions/capsolver-chrome
✅ BrowserUseLLMService listo. Modelo: gemini-2.5-flash
```

---

## 🐛 Troubleshooting

### Problema: "CapSolver Extension no disponible"

**Solución:**

1. Verificar que los archivos ZIP existan:
   ```bash
   ls -la broswer/*.zip broswer/*.tar.gz
   ```

2. Si no existen, descargar:
   - https://www.capsolver.com/extension
   - Colocar en `broswer/`

3. Limpiar cache de extensiones extraídas:
   ```bash
   rm -rf broswer/extensions/
   ```

4. Reiniciar la app

### Problema: CAPTCHA aún falla en Railway

**Verificar:**

1. ¿CapSolver tiene créditos? → https://dashboard.capsolver.com/balance
2. ¿Client Key configurada en la extensión?
3. ¿La extensión se cargó? → Ver logs "CapSolver Extension inyectada"

**Alternativa:** Usar cloud browser:
```python
Browser(use_cloud=True)  # Browser-Use cloud con stealth
```

---

## 📝 Archivos Modificados

| Archivo | Cambios |
|---------|---------|
| `web_app/app.py` | Import + startup + profile con extensión |
| `web_app/modules/capsolver_extension.py` | ✨ NUEVO - Gestor de extensiones |
| `.env` | Ya tiene CAPSOLVER_API_KEY |

---

## 🎯 Flujo Completo

```
Usuario inicia app
        ↓
app.py startup event
        ↓
setup_capsolver() extrae ZIPs
        ↓
run_agent() crea BrowserProfile
        ↓
--load-extension=... inyecta CapSolver
        ↓
Chrome abre CON CapSolver
        ↓
Agente navega a Compensar
        ↓
Aparece reCAPTCHA
        ↓
CapSolver Extension lo resuelve automáticamente ✅
        ↓
Agente continúa con formulario
```

---

## 📞 Soporte

Si CapSolver extension no funciona:

1. **Local (dev):** 
   - Chrome Settings → Extensions → CapSolver → Opciones → configurar API key
   - Ver https://www.capsolver.com/extension

2. **Railway:**
   - Verificar logs: `railway logs -f`
   - Asegurar que ZIP esté en repo
   - Considerar cloud browser

---

**Actualizado:** Mayo 2026 | CapSolver Extension v1.17.0 | Browser-Use 0.12.9

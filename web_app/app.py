"""
Browser Use Web App — FastAPI + WebSockets
==========================================
Arranca con:
    cd web_app
    uv run uvicorn app:app --host 0.0.0.0 --port 8000 --reload

Abre: http://localhost:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, List

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect, File, UploadFile, Query, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Browser-Use ────────────────────────────────────────────────
from browser_use import Agent, Browser, BrowserProfile
from browser_use.llm import ChatGoogle
from browser_use.browser.views import BrowserStateSummary
from browser_use.agent.views import AgentHistoryList, AgentOutput

# ── Módulos extendidos ──────────────────────────────────────────
from modules.site_mapper import map_portal, load_portal_map
from modules.console_executor import execute_direct_api, execute_form_function
from modules.session_pool import SessionPool
from modules.session_manager import SessionManager
from database import init_db, SessionLocal
from models.portals import Portal

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("web_app")

# ── Configuración ──────────────────────────────────────────────
# Acepta GEMINI_API_KEY (igual que el backend) o GOOGLE_API_KEY como alias
GOOGLE_API_KEY    = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")
IS_RAILWAY        = os.getenv("RAILWAY_ENVIRONMENT") is not None or os.getenv("RAILWAY_SERVICE_NAME") is not None
HEADLESS          = IS_RAILWAY or os.getenv("HEADLESS", "false").lower() == "true"
UPLOADS_DIR       = Path(__file__).parent / "uploads"
PDFS_DIR          = Path(__file__).parent / "uploads" / "pdfs"
TASKS_FILE        = Path(__file__).parent / "successful_tasks.json"
STATIC_DIR        = Path(__file__).parent / "static"
UPLOADS_DIR.mkdir(exist_ok=True)
PDFS_DIR.mkdir(exist_ok=True)

# ── Inicializar servicios ──────────────────────────────────────
init_db()  # Crear tablas SQLite
session_manager = SessionManager()
session_pool = SessionPool(max_sessions=20)
pdf_storage = {}  # {session_id: [lista de paths de PDFs]}

# ── Validar configuración ──────────────────────────────────────
if not GOOGLE_API_KEY and not IS_RAILWAY:
    logger.warning(
        "⚠️  GOOGLE_API_KEY no configurada en local.\n"
        "    Necesaria para ejecutar el agente.\n"
        "    Configura en .env: GEMINI_API_KEY=sk-...\n"
        "    O en terminal: export GEMINI_API_KEY=tu-clave"
    )

CHROME_PATHS_WINDOWS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\david.baeza\AppData\Local\Google\Chrome\Application\chrome.exe",
]

def _find_chrome() -> str | None:
    """Busca Chrome: rutas Windows en local, chromium en Linux/Railway."""
    import shutil
    import platform
    if platform.system() == "Windows":
        for path in CHROME_PATHS_WINDOWS:
            if Path(path).exists():
                return path
    # Linux (Railway): chromium instalado por nixpacks/apt
    return (
        shutil.which("google-chrome-stable") or
        shutil.which("google-chrome") or
        shutil.which("chromium-browser") or
        shutil.which("chromium")
    )

CHROME_EXE = _find_chrome()
logger.warning(f"Chrome: {CHROME_EXE} | Headless: {HEADLESS} | Railway: {IS_RAILWAY}")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ══════════════════════════════════════════════════════════════════
# WebSocket Manager
# ══════════════════════════════════════════════════════════════════

class ConnectionManager:
    def __init__(self):
        self.active: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self.active[session_id] = ws

    def disconnect(self, session_id: str):
        self.active.pop(session_id, None)

    async def send(self, session_id: str, data: dict):
        ws = self.active.get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(session_id)

    async def broadcast(self, data: dict):
        for sid in list(self.active):
            await self.send(sid, data)


manager = ConnectionManager()

# ══════════════════════════════════════════════════════════════════
# Session state
# ══════════════════════════════════════════════════════════════════

class AgentSession:
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.running: bool = False
        self.steps: int = 0
        self.start_time: float = 0
        self.current_url: str = ""
        self.session_id: str = ""
        self.agent: Any = None   # referencia al Agent en ejecución

    def reset(self, session_id: str):
        self.running = True
        self.steps = 0
        self.start_time = time.time()
        self.current_url = ""
        self.session_id = session_id

    def elapsed(self) -> int:
        return int(time.time() - self.start_time) if self.start_time else 0


sessions: dict[str, AgentSession] = {}


# ══════════════════════════════════════════════════════════════════
# CapSolver helper
# ══════════════════════════════════════════════════════════════════

async def solve_captcha(captcha_type: str, site_key: str, page_url: str) -> str | None:
    """
    Resuelve CAPTCHA via CapSolver API. Retorna None si no hay API key o falla.
    
    Soporta:
    - reCAPTCHA v2 (checkbox)
    - reCAPTCHA v3 (invisible)
    - hCaptcha
    """
    if not CAPSOLVER_API_KEY:
        logger.warning("⚠️ CAPSOLVER_API_KEY no configurada. CAPTCHAs no serán resueltos.")
        return None
    
    task_types = {
        "recaptchav2": "ReCaptchaV2TaskProxyless",
        "recaptchav3": "ReCaptchaV3TaskProxyless",
        "hcaptcha":    "HCaptchaTaskProxyless",
    }
    task_type = task_types.get(captcha_type.lower(), "ReCaptchaV2TaskProxyless")
    
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            logger.info(f"🔐 Enviando CAPTCHA a CapSolver: {captcha_type}")
            
            # Crear tarea
            r = await client.post("https://api.capsolver.com/createTask", json={
                "clientKey": CAPSOLVER_API_KEY,
                "task": {"type": task_type, "websiteURL": page_url, "websiteKey": site_key},
            })
            data = r.json()
            task_id = data.get("taskId")
            
            if not task_id:
                logger.error(f"❌ CapSolver error: {data}")
                return None
            
            logger.info(f"⏳ Esperando resolución de CAPTCHA (ID: {task_id})")
            
            # Esperar resultado (máx 60s)
            for attempt in range(30):
                await asyncio.sleep(2)
                r2 = await client.post("https://api.capsolver.com/getTaskResult", json={
                    "clientKey": CAPSOLVER_API_KEY,
                    "taskId": task_id,
                })
                result = r2.json()
                
                if result.get("status") == "ready":
                    solution = result.get("solution", {}).get("gRecaptchaResponse")
                    if solution:
                        logger.info(f"✅ CAPTCHA resuelto en {(attempt + 1) * 2}s")
                        return solution
                    
            logger.warning("⚠️ Timeout esperando resolución de CAPTCHA")
            return None
    except Exception as e:
        logger.error(f"❌ Error resolviendo CAPTCHA: {e}")
        return None


async def inject_captcha_solution(browser_session: Any, solution: str, captcha_type: str) -> bool:
    """
    Inyecta la solución del CAPTCHA en la página.
    Funciona para reCAPTCHA v2 y v3, hCaptcha.
    """
    try:
        page = getattr(browser_session, "_page", None) or getattr(browser_session, "page", None)
        if not page:
            logger.warning("⚠️ No hay página disponible para inyectar CAPTCHA")
            return False
        
        logger.info(f"💉 Inyectando solución de {captcha_type}")
        
        # Para reCAPTCHA v2/v3
        if "recaptcha" in captcha_type.lower():
            await page.evaluate(f"""
                () => {{
                    document.getElementById('g-recaptcha-response').innerHTML = `{solution}`;
                    if (typeof ___grecaptcha_cfg !== 'undefined') {{
                        Object.entries(___grecaptcha_cfg.clients).forEach(([key, client]) => {{
                            if (client.callback) {{
                                client.callback(`{solution}`);
                            }}
                        }});
                    }}
                }}
            """)
        # Para hCaptcha
        elif "hcaptcha" in captcha_type.lower():
            await page.evaluate(f"""
                () => {{
                    if (typeof hcaptcha !== 'undefined') {{
                        hcaptcha.getResponse().then(token => {{
                            document.getElementById('h-captcha-response').innerHTML = `{solution}`;
                        }});
                    }}
                }}
            """)
        
        # Trigger submit si hay botón
        await page.evaluate("""
            () => {
                const submitBtn = document.querySelector('button[type="submit"], input[type="submit"]');
                if (submitBtn) {
                    submitBtn.click();
                    return true;
                }
                return false;
            }
        """)
        
        logger.info(f"✅ CAPTCHA inyectado correctamente")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Error inyectando CAPTCHA: {e}")
        return False


# ══════════════════════════════════════════════════════════════════
# Successful tasks persistence
# ══════════════════════════════════════════════════════════════════

def load_tasks() -> list[dict]:
    if TASKS_FILE.exists():
        try:
            return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def save_task(entry: dict):
    tasks = load_tasks()
    tasks.insert(0, entry)
    tasks = tasks[:50]  # Máx 50 tareas
    TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# Screenshot loop — pantalla en vivo entre pasos del agente
# ══════════════════════════════════════════════════════════════════

# ── Clase singleton para LLM (exactamente como GeminiPlanoService en backend) ──────
class BrowserUseLLMService:
    """
    Replica exacta del patrón BACKENDBETATWILEND/app/gemini_plano_service.py
    para seleccionar modelo Gemini disponible en startup.
    
    IMPORTANTE: Mayo 2026 - Modelos vigentes:
    ✅ gemini-2.5-flash (RECOMENDADO - API estable)
    ✅ gemini-3-flash-preview (Más nuevo - use si 2.5 falla)
    ✅ gemini-1.5-flash (Fallback)
    ❌ gemini-2.0-flash (DEPRECATED - 404 NOT_FOUND)
    """
    
    # Lista de fallback - SOLO MODELOS VIGENTES EN 2026
    MODELS_FALLBACK = [
        os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),  # allow env override
        "gemini-2.5-flash",          # PRIMARY (stable, available)
        "gemini-3-flash-preview",    # FALLBACK 1 (newer model)
        "gemini-1.5-flash",          # FALLBACK 2
        "gemini-1.5-flash-8b",       # FALLBACK 3 (lightweight)
    ]
    MODELS_FALLBACK = list(dict.fromkeys(MODELS_FALLBACK))  # Remove dups, keep order
    
    def __init__(self):
        """Inicializar: detectar modelo disponible en startup (como backend)."""
        if not GOOGLE_API_KEY:
            raise ValueError(
                "❌ GOOGLE_API_KEY no configurada.\n"
                "   Configura en Railway: GEMINI_API_KEY=sk-...\n"
                "   O en local: export GEMINI_API_KEY=tu-clave"
            )
        
        # Detectar modelo disponible (en startup, no en cada llamada)
        self.model = self._detectar_modelo_disponible()
        logger.info(f"✅ BrowserUseLLMService listo. Modelo: {self.model}")
    
    def _detectar_modelo_disponible(self) -> str:
        """Prueba modelos en startup, retorna el primero disponible (como backend)."""
        for modelo in self.MODELS_FALLBACK:
            try:
                logger.info(f"🔍 Probando modelo: {modelo}")
                llm = ChatGoogle(model=modelo, api_key=GOOGLE_API_KEY)
                logger.info(f"✅ Modelo '{modelo}' disponible")
                return modelo
            except Exception as e:
                err_str = str(e)
                is_not_available = (
                    "404" in err_str or 
                    "NOT_FOUND" in err_str or 
                    "not available" in err_str or 
                    "no longer available" in err_str
                )
                
                if is_not_available:
                    logger.warning(f"⚠️ Modelo '{modelo}' no disponible (404), probando siguiente...")
                    continue
                else:
                    logger.warning(f"⚠️ Modelo '{modelo}' error: {e}, probando siguiente...")
                    continue
        
        logger.error(f"❌ Ningún modelo disponible. Retornando '{self.MODELS_FALLBACK[0]}' como fallback final.")
        return self.MODELS_FALLBACK[0]
    
    def get_llm(self) -> ChatGoogle:
        """Retorna una instancia de ChatGoogle con el modelo detectado."""
        return ChatGoogle(model=self.model, api_key=GOOGLE_API_KEY)


# ── Instancia global (como gemini_plano en backend) ──────────────────────────
try:
    llm_service = BrowserUseLLMService()
except Exception as e:
    logger.error(f"❌ Error inicializando BrowserUseLLMService: {e}")
    llm_service = None


def _get_llm_for_agent() -> ChatGoogle:
    """Obtener LLM para el agente (usa modelo detectado en startup)."""
    if llm_service is None:
        raise ValueError("❌ LLM no inicializado. Verifica GEMINI_API_KEY.")
    
    # Monkeypatch Browser-Use Agent para usar nuestro modelo
    import browser_use.agent.service
    original_init = browser_use.agent.service.Agent.__init__
    
    active_model = llm_service.model
    
    def patched_init(agent_self, *args, **kwargs):
        if 'llm' not in kwargs and len(args) < 2:
            from browser_use.llm.google.chat import ChatGoogle as BUChatGoogle
            kwargs['llm'] = BUChatGoogle(model=active_model, api_key=GOOGLE_API_KEY)
        return original_init(agent_self, *args, **kwargs)
    
    browser_use.agent.service.Agent.__init__ = patched_init
    return llm_service.get_llm()


def _get_fallback_llm() -> ChatGoogle | None:
    """Retorna LLM fallback para cuando el principal falla por 503 o similar."""
    if llm_service is None:
        return None
    
    # Obtener el próximo modelo disponible (no el activo)
    current = llm_service.model
    for modelo in llm_service.MODELS_FALLBACK:
        if modelo != current:
            try:
                logger.info(f"🔄 Configurando fallback LLM: {modelo}")
                return ChatGoogle(model=modelo, api_key=GOOGLE_API_KEY)
            except Exception as e:
                logger.warning(f"⚠️ Fallback model {modelo} error: {e}")
                continue
    
    logger.warning("⚠️ No se pudo configurar fallback LLM")
    return None


async def _screenshot_loop(session_id: str):
    """
    Captura y emite screenshots EN TIEMPO REAL del navegador.
    Accede directamente a la página de Playwright.
    Espera a que browser_session esté listo antes de intentar capturar.
    """
    import base64
    
    sess = sessions.get(session_id)
    if not sess:
        return
    
    # Esperar a que el agente esté completamente inicializado
    for _ in range(30):  # Esperar máx 30s
        if sess.agent and sess.agent.browser_session:
            break
        await asyncio.sleep(1)
    
    logger.info(f"📸 Screenshot loop iniciado para {session_id}")
    
    while sess and sess.running:
        try:
            agent = sess.agent
            if not agent or not agent.browser_session:
                await asyncio.sleep(1)
                continue
            
            try:
                # ── Obtener la página actual de Playwright ──
                page = await agent.browser_session.get_current_page()
                if page:
                    try:
                        # Capturar screenshot DIRECTO de Playwright
                        screenshot_bytes = await page.screenshot(full_page=False, timeout=5000)
                        if screenshot_bytes:
                            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
                            
                            # Emitir screenshot por WebSocket
                            try:
                                url = await page.url
                            except:
                                url = sess.current_url or ""
                            
                            await manager.send(session_id, {
                                "tipo": "screenshot",
                                "screenshot": screenshot_b64,
                                "url": url,
                            })
                            logger.debug(f"📸 Screenshot emitido: {len(screenshot_b64)} chars base64")
                    except asyncio.TimeoutError:
                        logger.debug(f"⚠️ Screenshot timeout (página lenta)")
                    except Exception as e:
                        logger.debug(f"⚠️ Error tomando screenshot: {type(e).__name__}: {e}")
            except Exception as e:
                logger.debug(f"⚠️ Error obteniendo página: {type(e).__name__}: {e}")
        except Exception as e:
            logger.debug(f"⚠️ Error en screenshot loop: {e}")
        
        # Intervalo realista para captura (2s = 0.5 fps es suficiente para UI)
        await asyncio.sleep(2.0)


# ══════════════════════════════════════════════════════════════════
# Agent runner
# ══════════════════════════════════════════════════════════════════

async def run_agent(session_id: str, task: str, pdf_paths: list[str] | None = None):
    """Corre el agente y hace streaming de cada paso via WebSocket."""
    sess = sessions[session_id]

    async def _emit(tipo: str, mensaje: str, extra: dict | None = None):
        payload: dict[str, Any] = {
            "tipo": tipo,
            "mensaje": mensaje,
            "url": sess.current_url,
            "pasos": sess.steps,
            "elapsed": sess.elapsed(),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        if extra:
            payload.update(extra)
        await manager.send(session_id, payload)

    await _emit("info", f"🚀 Iniciando agente para: {task}")

    # ── Anti-detección via BrowserProfile ──────────────────────
    profile_kwargs: dict = {
        "user_agent": random.choice(USER_AGENTS),
        "headless": HEADLESS,   # False local (visible), True en Railway
        "wait_for_network_idle_page_load_time": 20.0,  # SPAs lentos (Compensar, EPS)
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-sandbox",                  # Requerido en Railway/Docker
            "--disable-dev-shm-usage",       # Requerido en Railway/Docker
        ],
    }
    if CHROME_EXE:
        profile_kwargs["executable_path"] = CHROME_EXE

    profile = BrowserProfile(**profile_kwargs)

    # ── Contexto del PDF si se subió ───────────────────────────
    task_final = task
    if pdf_paths:
        task_final += f"\n\nARCHIVOS PDF DISPONIBLES para consulta: {', '.join(pdf_paths)}"
        await _emit("info", f"📎 {len(pdf_paths)} PDF(s) adjunto(s) a la tarea")

    # ── Callbacks ──────────────────────────────────────────────
    async def on_step(browser_state: BrowserStateSummary, output: AgentOutput, step_n: int):
        sess.steps = step_n
        # Extraer URL actual
        url = getattr(browser_state, "url", "") or ""
        sess.current_url = url

        # Extraer info del paso
        state = output.current_state
        next_goal  = getattr(state, "next_goal",  "") or ""
        memory     = getattr(state, "memory",     "") or ""
        evaluation = getattr(state, "evaluation_previous_goal", "") or ""

        # Determinar tipo de log
        tipo = "action"
        if evaluation and "success" in evaluation.lower():
            tipo = "success"
        elif evaluation and ("fail" in evaluation.lower() or "error" in evaluation.lower()):
            tipo = "warn"

        mensaje = next_goal or f"Ejecutando paso {step_n}"
        if evaluation:
            mensaje = f"{evaluation} → {next_goal}" if next_goal else evaluation

        # 🔐 DETECCIÓN AUTOMÁTICA DE CAPTCHA
        captcha_keywords = ["captcha", "recaptcha", "hcaptcha", "robot", "verify", "check"]
        mensaje_lower = mensaje.lower() + (memory or "").lower()
        
        if any(kw in mensaje_lower for kw in captcha_keywords):
            tipo = "warn"
            mensaje = f"🔒 CAPTCHA detectado: {mensaje}"
            logger.warning(f"🔒 CAPTCHA detectado en paso {step_n}: {mensaje}")
            await _emit(tipo, mensaje, {
                "step_n": step_n,
                "url": url,
                "captcha_detected": True,
            })
            
            # Intentar resolver automáticamente si tenemos CapSolver
            if CAPSOLVER_API_KEY:
                await _emit("info", "🔐 Intentando resolver CAPTCHA con CapSolver...")
                solution = await solve_captcha("recaptchav2", "dummy", url)
                if solution:
                    await _emit("success", "✅ CAPTCHA resuelto por CapSolver")
                    # Inyectar en página
                    if sess.agent and sess.agent.browser_session:
                        await inject_captcha_solution(sess.agent.browser_session, solution, "recaptchav2")
                else:
                    await _emit("warn", "⚠️ No se pudo resolver CAPTCHA, esperando...")
            else:
                await _emit("warn", "⚠️ CAPSOLVER_API_KEY no configurada. No se puede resolver CAPTCHA.")
        else:
            # Log normal sin CAPTCHA
            await _emit(tipo, mensaje, {
                "step_n": step_n,
                "url": url,
                "memory": memory,
            })

        # Delay aleatorio anti-detección (0.5s – 2s) entre pasos
        await asyncio.sleep(random.uniform(0.5, 2.0))

        # Screenshot para la pantalla en vivo
        screenshot = getattr(browser_state, "screenshot", None)
        if screenshot:
            # Asegurar que es base64 string
            if isinstance(screenshot, bytes):
                import base64
                screenshot = base64.b64encode(screenshot).decode()
            
            if isinstance(screenshot, str) and screenshot.strip():
                await manager.send(session_id, {
                    "tipo": "screenshot",
                    "screenshot": screenshot,
                    "url": url,
                    "step_n": step_n,
                })
                logger.debug(f"📸 Screenshot emitido en paso {step_n}")

    async def on_done(history: AgentHistoryList):
        resultado = history.final_result() or ""
        sess.running = False

        # Detectar CAPTCHAs en el resultado
        captcha_keywords = ["captcha", "recaptcha", "hcaptcha", "robot", "verify"]
        captcha_encontrado = any(
            kw in resultado.lower()
            for kw in captcha_keywords
        )

        # Determinar si fue exitoso
        success = bool(resultado) and not any(
            error_kw in resultado.lower() 
            for error_kw in ["error", "failed", "no se pudo", "unable"]
        )

        if success:
            mensaje = f"✅ Completado: {resultado[:200]}"
            tipo = "success"
            logger.info(f"✅ ÉXITO: {resultado[:200]}")
        elif captcha_encontrado:
            mensaje = f"⚠️ CAPTCHA bloqueó la tarea: {resultado[:200]}"
            tipo = "warn"
            logger.warning(f"⚠️ CAPTCHA: {resultado[:200]}")
        else:
            mensaje = f"❌ Falló: {resultado[:200] if resultado else 'Sin resultado'}"
            tipo = "error"
            logger.error(f"❌ FALLÓ: {resultado[:200]}")

        await _emit(tipo, mensaje, {
            "resultado": resultado,
            "captcha": captcha_encontrado,
            "success": success,
        })

        # Guardar tarea (exitosa O fallida, para análisis)
        if resultado:
            save_task({
                "id": session_id,
                "tarea": task,
                "resultado": resultado[:500],
                "pasos": sess.steps,
                "duracion": sess.elapsed(),
                "fecha": datetime.now().isoformat(),
                "exitoso": success,
                "captcha": captcha_encontrado,
            })
            msg = "Tarea guardada en historial"
            await manager.send(session_id, {"tipo": "task_saved", "mensaje": msg})

    # ── System Prompt para potenciar el agente ────────────────────────
    SYSTEM_PROMPT = """
Eres un agente de automatización web experto. Tu objetivo es completar tareas web de forma rápida, precisa y resistente a errores.

INSTRUCCIONES CRÍTICAS:
1. ANÁLISIS ANTES DE ACTUAR
   - Observa toda la página antes de hacer clicks
   - Identifica formularios, botones, campos
   - Planifica los pasos antes de ejecutar

2. LLENADO DE FORMULARIOS
   - Lee TODOS los campos visibles
   - Llena campos con datos lógicos y realistas
   - Si un campo es obligatorio pero no tienes datos, intenta inferir del contexto
   - Usa datos típicos: nombres reales, emails válidos, teléfonos realistas

3. MANEJO DE ERRORES
   - Si un campo rechaza tu entrada, intenta un formato diferente
   - Si hay validaciones, adapta tu entrada
   - Si hay error de red, espera y reintentar
   - NO te rindas en el primer intento

4. NAVEGACIÓN
   - Haz click en botones "Next", "Continue", "Submit" cuando sea necesario
   - Maneja múltiples páginas de formularios
   - Espera a que las páginas carguen completamente

5. DETECCIÓN DE PROBLEMAS
   - Si ves "CAPTCHA", "reCAPTCHA", "hCaptcha" → el sistema lo resolverá automáticamente
   - Si ves "error", "invalid", "required" → análiza qué falta
   - Si no puedes llenar un campo → reporta claramente qué pasó

6. ÉXITO
   - Una tarea se completó cuando:
     a) Se envió un formulario exitosamente
     b) Viste confirmación (página de "gracias", "success", número de referencia)
     c) Recibiste un email de confirmación
     d) Los datos aparecen en una siguiente página

7. REPORTE FINAL
   - Reporta EXACTAMENTE qué se completó
   - Si falló algo, explica qué error viste y por qué no pudiste continuar
   - Si hubo CAPTCHA, menciona que fue resuelto
"""

    # ── Crear y correr el agente ───────────────────────────────
    # Usar LLM detectado en startup (modelo seleccionado automáticamente)
    try:
        llm = _get_llm_for_agent()
        fallback_llm = _get_fallback_llm()  # ← FALLBACK para cuando falla por 503
    except ValueError as e:
        await _emit("error", f"❌ Error de configuración: {str(e)}")
        sess.running = False
        return

    # IMPORTANTE: Browser-Use 0.12.9 NO soporta fallback_llm nativamente
    # Solo pasamos el LLM principal al Agent
    agent = Agent(
        task=task_final,
        llm=llm,  # ← Solo LLM principal
        # NO PASAR fallback_llm (no es parámetro nativo)
        browser_profile=profile,
        system_prompt=SYSTEM_PROMPT,  # ← SYSTEM PROMPT MEJORADO
        register_new_step_callback=on_step,
        register_done_callback=on_done,
        max_steps=30,
        max_total_time=600,  # 10 minutos máximo por sesión
    )
    sess.agent = agent  # guardar referencia para screenshots y clicks

    # Loop continuo de screenshots (entre pasos)
    asyncio.create_task(_screenshot_loop(session_id))

    try:
        await _emit("info", "🌐 Abriendo navegador...")
        logger.info(f"🚀 Iniciando agente | Modelo: {llm.model} | Fallback: {fallback_llm.model if fallback_llm else 'None'}")
        
        await agent.run(max_steps=30)
        
        logger.info(f"✅ Agente completó | Pasos: {sess.steps} | Duración: {sess.elapsed()}s")
        
    except asyncio.CancelledError:
        await _emit("warn", "⛔ Agente detenido por el usuario")
        sess.running = False
        logger.warning("⛔ Agente cancelado por usuario")
        
    except Exception as e:
        await _emit("error", f"❌ Error del agente: {str(e)}")
        sess.running = False
        logger.error(f"❌ Excepción en agente: {e}", exc_info=True)
        
    finally:
        sess.running = False


# ══════════════════════════════════════════════════════════════════
# Modelos Pydantic
# ══════════════════════════════════════════════════════════════════

class PDFInfo(BaseModel):
    filename: str
    path: str
    size_mb: float
    uploaded_at: str
    mime_type: str = "application/pdf"


class SessionPDFs(BaseModel):
    session_id: str
    pdfs: List[PDFInfo] = []
    total_size_mb: float = 0.0


class TaskWithPDFs(BaseModel):
    task: str
    pdf_ids: List[str] = []  # IDs de PDFs a incluir


# ══════════════════════════════════════════════════════════════════
# FastAPI app
# ══════════════════════════════════════════════════════════════════

app = FastAPI(title="Browser Use Web App")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ── WebSocket ──────────────────────────────────────────────────
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await manager.connect(session_id, websocket)
    sessions.setdefault(session_id, AgentSession())
    try:
        while True:
            await websocket.receive_text()  # Mantener vivo
    except WebSocketDisconnect:
        manager.disconnect(session_id)


# ── Ejecutar agente ────────────────────────────────────────────
@app.post("/api/run")
async def api_run(body: dict):
    session_id = body.get("session_id", str(uuid.uuid4()))
    task       = body.get("task", "").strip()
    pdf_paths  = body.get("pdf_paths", [])

    if not task:
        return JSONResponse({"error": "Tarea vacía"}, status_code=400)
    if not GOOGLE_API_KEY:
        return JSONResponse({"error": "GOOGLE_API_KEY no configurada"}, status_code=500)

    # Cancelar sesión anterior si existe
    sess = sessions.get(session_id)
    if sess and sess.task and not sess.task.done():
        sess.task.cancel()
        await asyncio.sleep(0.3)

    sess = AgentSession()
    sess.reset(session_id)
    sessions[session_id] = sess

    task_obj = asyncio.create_task(run_agent(session_id, task, pdf_paths))
    sess.task = task_obj

    return {"ok": True, "session_id": session_id}


# ── Detener agente ─────────────────────────────────────────────
@app.post("/api/stop")
async def api_stop(body: dict):
    session_id = body.get("session_id", "")
    sess = sessions.get(session_id)
    if sess and sess.task and not sess.task.done():
        sess.task.cancel()
        sess.running = False
        return {"ok": True, "mensaje": "Agente detenido"}
    return {"ok": False, "mensaje": "No hay agente activo"}


@app.post("/api/pause")
async def api_pause(body: dict):
    """Pausa la ejecución del agente."""
    session_id = body.get("session_id", "")
    sess = sessions.get(session_id)
    if sess and sess.running:
        sess.running = False
        await manager.send(session_id, {
            "tipo": "info",
            "msg": "⏸️ Agente pausado - lista anotaciones activas"
        })
        return {"ok": True, "mensaje": "Agente pausado"}
    return {"ok": False, "mensaje": "No hay agente activo"}


@app.post("/api/resume")
async def api_resume(body: dict):
    """Reanuda la ejecución del agente."""
    session_id = body.get("session_id", "")
    sess = sessions.get(session_id)
    if sess:
        sess.running = True
        await manager.send(session_id, {
            "tipo": "success",
            "msg": "▶️ Ejecución reanudada"
        })
        return {"ok": True, "mensaje": "Agente reanudado"}
    return {"ok": False, "mensaje": "No hay sesión activa"}


# ── Historial de tareas ────────────────────────────────────────
@app.get("/api/tasks")
async def api_tasks():
    return {"tasks": load_tasks()}


# ── Estado de una sesión ───────────────────────────────────────
@app.get("/api/status/{session_id}")
async def api_status(session_id: str):
    sess = sessions.get(session_id)
    if not sess:
        return {"running": False, "steps": 0, "elapsed": 0}
    return {
        "running": sess.running,
        "steps": sess.steps,
        "elapsed": sess.elapsed(),
        "url": sess.current_url,
    }


# ── Config ─────────────────────────────────────────────────────
@app.get("/api/config")
async def api_config():
    return {
        "google_api_key": bool(GOOGLE_API_KEY),
        "capsolver_api_key": bool(CAPSOLVER_API_KEY),
        "model": "gemini-3-flash-preview",
        "headless": HEADLESS,
        "railway": IS_RAILWAY,
    }


# ── Interacción con el navegador en vivo ──────────────────────
@app.post("/api/interact")
async def api_interact(body: dict):
    """Reenvía clicks/teclado del usuario al navegador del agente."""
    session_id = body.get("session_id", "")
    action     = body.get("action", "click")   # click | type
    x          = int(body.get("x", 0))
    y          = int(body.get("y", 0))
    text       = body.get("text", "")

    sess = sessions.get(session_id)
    if not sess or not sess.agent:
        return JSONResponse({"ok": False, "error": "Sin agente activo"}, status_code=404)

    try:
        bs = getattr(sess.agent, "browser_session", None)
        if bs:
            if action == "click":
                # Método 1: execute_javascript
                if hasattr(bs, "execute_javascript"):
                    await bs.execute_javascript(
                        f"(()=>{{const e=document.elementFromPoint({x},{y});if(e)e.click();}})()"
                    )
                    return {"ok": True}
                # Método 2: Playwright mouse
                page = getattr(bs, "_page", None) or getattr(bs, "page", None)
                if page:
                    await page.mouse.click(x, y)
                    return {"ok": True}
            elif action == "type":
                page = getattr(bs, "_page", None) or getattr(bs, "page", None)
                if page:
                    await page.keyboard.type(text)
                    return {"ok": True}
    except Exception as e:
        logger.warning(f"interact error: {e}")

    return {"ok": False, "error": "No se pudo interactuar"}


# ══════════════════════════════════════════════════════════════════
# PDFs Management — Upload, list, delete
# ══════════════════════════════════════════════════════════════════

@app.post("/api/upload-pdf")
async def upload_pdf(
    session_id: str = Query(...),
    file: UploadFile = File(...),
):
    """
    Sube un PDF a la sesión actual.
    
    Uso:
        curl -X POST "http://localhost:8000/api/upload-pdf?session_id=123" \
             -F "file=@documento.pdf"
    """
    
    try:
        if not file.filename.endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Solo se aceptan archivos PDF")
        
        # Generar nombre único
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_filename = f"{timestamp}_{file.filename}"
        file_path = PDFS_DIR / unique_filename
        
        # Guardar archivo
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        size_mb = len(content) / (1024 * 1024)
        
        # Registrar en storage
        if session_id not in pdf_storage:
            pdf_storage[session_id] = []
        
        pdf_info = PDFInfo(
            filename=file.filename,
            path=str(file_path),
            size_mb=round(size_mb, 2),
            uploaded_at=datetime.utcnow().isoformat(),
        )
        
        pdf_storage[session_id].append(pdf_info)
        
        logger.info(f"📄 PDF subido: {file.filename} ({size_mb:.2f}MB)")
        
        return {
            "ok": True,
            "message": f"PDF '{file.filename}' subido exitosamente",
            "pdf_id": unique_filename,
            "size_mb": pdf_info.size_mb,
        }
        
    except Exception as e:
        logger.error(f"❌ Error subiendo PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pdfs/{session_id}")
async def get_session_pdfs(session_id: str):
    """Obtiene lista de PDFs en la sesión."""
    
    pdfs = pdf_storage.get(session_id, [])
    total_size = sum(pdf.size_mb for pdf in pdfs)
    
    return SessionPDFs(
        session_id=session_id,
        pdfs=pdfs,
        total_size_mb=round(total_size, 2),
    )


@app.delete("/api/pdfs/{session_id}/{pdf_id}")
async def delete_pdf(session_id: str, pdf_id: str):
    """Elimina un PDF de la sesión."""
    
    try:
        pdfs = pdf_storage.get(session_id, [])
        
        for i, pdf in enumerate(pdfs):
            if pdf.filename == pdf_id or pdf.path.endswith(pdf_id):
                # Eliminar archivo físico
                path = Path(pdf.path)
                if path.exists():
                    path.unlink()
                
                # Eliminar de lista
                pdfs.pop(i)
                logger.info(f"🗑️ PDF eliminado: {pdf_id}")
                
                return {"ok": True, "message": f"PDF '{pdf_id}' eliminado"}
        
        raise HTTPException(status_code=404, detail="PDF no encontrado")
        
    except Exception as e:
        logger.error(f"❌ Error eliminando PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pdfs/{session_id}/{pdf_id}/download")
async def download_pdf(session_id: str, pdf_id: str):
    """Descarga un PDF."""
    
    try:
        pdfs = pdf_storage.get(session_id, [])
        
        for pdf in pdfs:
            if pdf.filename == pdf_id or pdf.path.endswith(pdf_id):
                path = Path(pdf.path)
                if path.exists():
                    return FileResponse(
                        path,
                        media_type="application/pdf",
                        filename=pdf.filename,
                    )
        
        raise HTTPException(status_code=404, detail="PDF no encontrado")
        
    except Exception as e:
        logger.error(f"❌ Error descargando PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/run-with-pdfs")
async def run_with_pdfs(body: dict):
    """
    Ejecuta tarea con PDFs adjuntos.
    
    Request:
    {
        "task": "Procesar estos documentos",
        "session_id": "123",
        "pdf_ids": ["documento1.pdf", "documento2.pdf"]
    }
    """
    
    session_id = body.get("session_id", str(uuid.uuid4()))
    task = body.get("task", "").strip()
    pdf_ids = body.get("pdf_ids", [])
    
    if not task:
        return JSONResponse({"error": "Tarea vacía"}, status_code=400)
    
    # Obtener rutas de PDFs
    pdf_paths = []
    pdfs = pdf_storage.get(session_id, [])
    
    for pdf_id in pdf_ids:
        for pdf in pdfs:
            if pdf.filename == pdf_id or pdf.path.endswith(pdf_id):
                pdf_paths.append(pdf.path)
                break
    
    if pdf_ids and not pdf_paths:
        return JSONResponse({"error": "PDFs no encontrados"}, status_code=404)
    
    # Agregar PDFs al task
    if pdf_paths:
        task += f"\n\n📎 ARCHIVOS ADJUNTOS:\n" + "\n".join(pdf_paths)
    
    # Ejecutar agente
    if not GOOGLE_API_KEY:
        return JSONResponse({"error": "GOOGLE_API_KEY no configurada"}, status_code=500)
    
    sess = AgentSession()
    sess.reset(session_id)
    sessions[session_id] = sess
    
    task_obj = asyncio.create_task(run_agent(session_id, task, pdf_paths))
    sess.task = task_obj
    
    logger.info(f"🚀 Tarea iniciada con {len(pdf_paths)} PDFs")
    
    return {"ok": True, "session_id": session_id, "pdf_count": len(pdf_paths)}

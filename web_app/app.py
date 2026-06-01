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

# ── Google genai (para detección de modelos en startup) ────────
from google import genai

# ── Browser-Use ────────────────────────────────────────────────
from browser_use import Agent, BrowserProfile
from browser_use.llm import ChatGoogle
from browser_use.browser.views import BrowserStateSummary
from browser_use.agent.views import AgentHistoryList, AgentOutput

# ── Módulos extendidos ──────────────────────────────────────────
from modules.site_mapper import map_portal, load_portal_map, install_network_interceptor
from modules.console_executor import execute_direct_api, execute_form_function
from modules.session_pool import SessionPool
from modules.session_manager import SessionManager
from modules.diagnostics import run_diagnostics, get_strategy_config
from modules.captcha_solver import handle_captcha, solve as solve_captcha_v2
from modules.smart_executor import dismiss_blocking_element
from modules.portal_memory import portal_memory
from modules.shadow_recorder import start_shadow, stop_shadow, is_shadow_active
from modules.supervisor_agent import supervisor
from modules.capsolver_extension import (
    setup_capsolver,
    get_capsolver_extension_path,
)
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
        self.agent: Any = None                # instancia browser-use Agent
        self.pause_event: asyncio.Event = asyncio.Event()  # set=corriendo, clear=pausado
        self.human_actions: list[str] = []  # acciones grabadas mientras está pausado
        self.guidance_messages: list[str] = []  # instrucciones del operador en tiempo real
        self.action_log: list[dict] = []    # registro completo de acciones ejecutadas
        self.form_fields: list[dict] = []   # campos de formulario llenados

    def reset(self, session_id: str):
        self.running = True
        self.steps = 0
        self.start_time = time.time()
        self.current_url = ""
        self.session_id = session_id
        self.pause_event.set()   # arrancar sin pausa
        self.human_actions = []

    def elapsed(self) -> int:
        return int(time.time() - self.start_time) if self.start_time else 0

    @property
    def paused(self) -> bool:
        return not self.pause_event.is_set()


sessions: dict[str, AgentSession] = {}


# ══════════════════════════════════════════════════════════════════
# CapSolver helper
# ══════════════════════════════════════════════════════════════════

# solve_captcha e inject_captcha_solution reemplazados por modules/captcha_solver.py
# Las funciones handle_captcha() y solve() de ese módulo se usan directamente en on_step.


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

# ── Servicio LLM (browser-use + Gemini 3.1 Pro) ───────────────
class BrowserUseLLMService:
    MODELS_FALLBACK = list(dict.fromkeys([
        os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview"),
        "gemini-3.1-pro-preview",
        "gemini-3.5-flash",
        "gemini-2.5-flash",
    ]))

    def __init__(self):
        if not GOOGLE_API_KEY:
            raise ValueError("GEMINI_API_KEY no configurada")
        self.model = self._detect()
        logger.warning(f"✅ LLM listo: {self.model}")

    def _detect(self) -> str:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        for m in self.MODELS_FALLBACK:
            try:
                logger.info(f"🔍 Probando: {m}")
                client.models.generate_content(model=m, contents="hi")
                logger.info(f"✅ '{m}' disponible")
                return m
            except Exception as e:
                logger.warning(f"⚠️ '{m}': {str(e)[:80]}")
        return self.MODELS_FALLBACK[-1]

    def get_llm(self) -> ChatGoogle:
        return ChatGoogle(
            model=self.model,
            api_key=GOOGLE_API_KEY,
            thinking_level="high",  # máximo razonamiento Gemini 3 Pro
        )


try:
    llm_service = BrowserUseLLMService()
except Exception as e:
    logger.error(f"❌ Error inicializando LLM: {e}")
    llm_service = None


async def _get_field_label(page: Any) -> str:
    """Obtiene el label del campo activo en el formulario via JS."""
    try:
        return await page.evaluate("""
            () => {
                const el = document.activeElement;
                if (!el) return 'campo';
                if (el.id) {
                    const lbl = document.querySelector(`label[for="${el.id}"]`);
                    if (lbl) return lbl.innerText.trim().replace(/[*:\\n]/g, ' ').trim();
                }
                if (el.placeholder) return el.placeholder;
                if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
                const parent = el.closest('label');
                if (parent) return parent.innerText.trim().split('\\n')[0].replace(/[*:]/g, '').trim();
                return el.name || el.type || 'campo';
            }
        """)
    except Exception:
        return "campo"


def _build_reentry_json(task: str, action_log: list[dict], form_fields: list[dict]) -> dict:
    """Construye el JSON de reentrada para repetir la tarea automáticamente."""
    entry_url = next(
        (a["args"].get("url", "") for a in action_log if a.get("accion") == "navigate"),
        ""
    )
    return {
        "url_entrada": entry_url,
        "campos_formulario": [
            {"campo": f["campo"], "valor": f["valor"]} for f in form_fields
        ],
        "pasos_resumen": [
            {"paso": a["turno"], "accion": a["accion"],
             "args": {k: v for k, v in a.get("args", {}).items() if k != "safety_decision"}}
            for a in action_log
            if a.get("accion") not in ("wait_5_seconds",)
        ],
    }


SYSTEM_PROMPT = """
Eres un agente experto en automatización web para radicación de incapacidades médicas colombianas (EPS).
Controlas el navegador directamente viendo screenshots y ejecutando acciones precisas.

FLUJO PARA RADIAR INCAPACIDAD:
1. Navega al portal EPS indicado
2. Inicia sesión con las credenciales proporcionadas
3. Ubica la sección de radicación de incapacidades
4. Busca al empleado por número de documento
5. Ingresa los datos: fecha inicio, días, diagnóstico, tipo de incapacidad
6. Adjunta el PDF soporte si se proporcionó
7. Envía el formulario y captura el número de radicado

REGLAS:
- Analiza cada screenshot antes de actuar
- Si ves un CAPTCHA, resuélvelo interactuando con él directamente
- Si una acción falla, intenta una alternativa (scroll, hover, tecla Tab)
- Reporta el número de radicado exacto al finalizar
- Si la sesión expira, vuelve a iniciar sesión
"""


async def _execute_computer_use_action_UNUSED(page: Any, fname: str, args: dict, sw: int, sh: int) -> dict:
    """Retenida por referencia histórica — no se usa en el flujo browser-use."""

    def _x(v): return int(v / 1000 * sw)
    def _y(v): return int(v / 1000 * sh)

    try:
        if fname == "open_web_browser":
            pass
        elif fname == "wait_5_seconds":
            await asyncio.sleep(5)
        elif fname == "navigate":
            await page.goto(args["url"], wait_until="domcontentloaded", timeout=20000)
        elif fname == "search":
            await page.goto("https://www.google.com", wait_until="domcontentloaded")
        elif fname == "go_back":
            await page.go_back(timeout=8000)
        elif fname == "go_forward":
            await page.go_forward(timeout=8000)
        elif fname == "click_at":
            await page.mouse.click(_x(args["x"]), _y(args["y"]))
        elif fname == "hover_at":
            await page.mouse.move(_x(args["x"]), _y(args["y"]))
        elif fname == "type_text_at":
            x, y        = _x(args["x"]), _y(args["y"])
            text        = str(args.get("text", ""))
            clear_before = args.get("clear_before_typing", True)
            press_enter  = args.get("press_enter", True)

            # 1. Click para enfocar el campo
            await page.mouse.click(x, y)
            await asyncio.sleep(0.3)

            # 2. Llenar via JS nativo (soporta React / Angular / Vue)
            filled = await page.evaluate(
                """([x, y, text, clear]) => {
                    const hit = document.elementFromPoint(x, y) || document.activeElement;
                    if (!hit) return false;
                    const inp = ['INPUT','TEXTAREA','SELECT'].includes(hit.tagName)
                        ? hit : hit.closest('input,textarea,select,[contenteditable]');
                    if (!inp) return false;
                    inp.focus();
                    const proto = inp.tagName === 'TEXTAREA'
                        ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (setter && setter.set) {
                        if (clear) setter.set.call(inp, '');
                        setter.set.call(inp, text);
                    } else {
                        if (clear) inp.value = '';
                        inp.value = text;
                    }
                    ['input','change'].forEach(t =>
                        inp.dispatchEvent(new Event(t, {bubbles:true, cancelable:true})));
                    inp.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));
                    return true;
                }""",
                [x, y, text, clear_before]
            )

            if not filled:
                # Fallback: teclado
                if clear_before:
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Delete")
                    await asyncio.sleep(0.1)
                await page.keyboard.type(text, delay=50)

            if press_enter:
                await asyncio.sleep(0.2)
                await page.keyboard.press("Enter")
        elif fname == "key_combination":
            await page.keyboard.press(args["keys"])
        elif fname == "scroll_document":
            direction = args.get("direction", "down")
            if direction == "down":
                await page.evaluate("window.scrollBy(0, window.innerHeight * 0.75)")
            elif direction == "up":
                await page.evaluate("window.scrollBy(0, -window.innerHeight * 0.75)")
            elif direction == "right":
                await page.evaluate("window.scrollBy(window.innerWidth * 0.75, 0)")
            elif direction == "left":
                await page.evaluate("window.scrollBy(-window.innerWidth * 0.75, 0)")
        elif fname == "scroll_at":
            x, y = _x(args["x"]), _y(args["y"])
            direction = args.get("direction", "down")
            mag = args.get("magnitude", 800)
            scale = mag / 1000
            dx = dy = 0
            if direction == "down":   dy =  int(sh * scale)
            elif direction == "up":   dy = -int(sh * scale)
            elif direction == "right": dx =  int(sw * scale)
            elif direction == "left":  dx = -int(sw * scale)
            await page.mouse.wheel(dx, dy)
        elif fname == "drag_and_drop":
            sx, sy = _x(args["x"]), _y(args["y"])
            dx, dy = _x(args["destination_x"]), _y(args["destination_y"])
            await page.mouse.move(sx, sy)
            await page.mouse.down()
            await asyncio.sleep(0.3)
            await page.mouse.move(dx, dy, steps=15)
            await page.mouse.up()
        else:
            logger.warning(f"⚠️ Acción no implementada: {fname}")
    except Exception as e:
        logger.warning(f"⚠️ Error en {fname}: {e}")
        return {"error": str(e)}

    # Pausa breve para que la UI reaccione
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=4000)
    except Exception:
        pass
    await asyncio.sleep(0.4)
    return {}


async def _scan_and_emit_form_fields(page: Any, session_id: str, url: str):
    """Detecta campos de formulario en el DOM y los emite para la pestaña Campos."""
    try:
        await asyncio.sleep(1.8)  # Esperar que el DOM renderice completamente
        fields = await page.evaluate("""
            () => {
                const sel = [
                    'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image])',
                    'textarea', 'select'
                ].join(',');
                return Array.from(document.querySelectorAll(sel)).slice(0,30).map(el => {
                    let label = '';
                    if (el.id) {
                        const lbl = document.querySelector('label[for="' + el.id + '"]');
                        if (lbl) label = lbl.innerText.trim().replace(/[*:\\n\\t]/g,' ').trim().replace(/\\s+/g,' ');
                    }
                    if (!label) label = el.getAttribute('aria-label') || '';
                    if (!label) label = el.placeholder || '';
                    if (!label) {
                        const p = el.closest('label');
                        if (p) label = p.innerText.trim().split('\\n')[0];
                    }
                    if (!label) label = el.name || el.type || 'campo';
                    const r = el.getBoundingClientRect();
                    return {
                        label: label.replace(/[*:]/g,'').trim(),
                        type:  el.type || el.tagName.toLowerCase(),
                        required: el.required || !!el.getAttribute('required'),
                        visible: r.width > 0 && r.height > 0,
                    };
                }).filter(f => f.visible && f.label.length > 0);
            }
        """)
        if fields:
            await manager.send(session_id, {
                "tipo": "form_detected",
                "fields": fields,
                "url": url,
            })
    except Exception as e:
        logger.debug(f"form_scan failed: {e}")


# ══════════════════════════════════════════════════════════════════
# Agent runner — browser-use + Gemini 3.1 Pro
# ══════════════════════════════════════════════════════════════════

async def run_agent(session_id: str, task: str, pdf_paths: list[str] | None = None):
    """Corre el agente browser-use con Gemini 3.1 Pro y hace streaming via WebSocket."""
    import base64
    sess = sessions[session_id]
    supervisor.register_agent(session_id=session_id, task=task)

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

    if llm_service is None:
        await _emit("error", "❌ LLM no inicializado. Verifica GEMINI_API_KEY.")
        sess.running = False
        return

    llm = llm_service.get_llm()
    await _emit("info", f"🚀 Iniciando agente | Modelo: {llm_service.model}")

    # ── Browser profile ──────────────────────────────────────────
    profile_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-first-run",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    profile_kwargs: dict = {
        "user_agent": random.choice(USER_AGENTS),
        "headless": HEADLESS,
        "wait_for_network_idle_page_load_time": 20.0,
        "args": profile_args,
    }
    if CHROME_EXE:
        profile_kwargs["executable_path"] = CHROME_EXE
    profile = BrowserProfile(**profile_kwargs)

    # ── Task con PDFs ────────────────────────────────────────────
    task_final = task
    if pdf_paths:
        task_final += f"\n\nARCHIVOS PDF DISPONIBLES para consulta: {', '.join(pdf_paths)}"
        await _emit("info", f"📎 {len(pdf_paths)} PDF(s) adjunto(s)")

    # ── Callbacks ────────────────────────────────────────────────
    async def on_step(browser_state: BrowserStateSummary, output: AgentOutput, step_n: int):
        # ── Si el usuario paró, abortar inmediatamente ────────────
        if not sess.running:
            raise asyncio.CancelledError("Detenido por el usuario")

        sess.steps = step_n
        url = getattr(browser_state, "url", "") or ""
        sess.current_url = url
        supervisor.update_agent(session_id=session_id, step_current=step_n, step_total=30, status="running")

        # ── Inyectar guidance pendiente en el historial del agente ─
        if sess.guidance_messages and sess.agent:
            guidance = " | ".join(sess.guidance_messages)
            sess.guidance_messages.clear()
            await _emit("info", f"💬 Instrucción del operador: {guidance}", {"step_n": step_n})
            try:
                # Inyectar en el message manager de browser-use
                from browser_use.llm.messages import HumanMessage as BUHumanMessage
                msg = BUHumanMessage(content=f"⚡ Instrucción del operador (actuar ahora): {guidance}")
                if hasattr(sess.agent, '_message_manager') and hasattr(sess.agent._message_manager, '_messages'):
                    sess.agent._message_manager._messages.append(msg)
            except Exception as ge:
                logger.debug(f"guidance inject: {ge}")

        # ── Extraer info del paso ────────────────────────────────
        state = output.current_state
        next_goal  = getattr(state, "next_goal",  "") or ""
        memory     = getattr(state, "memory",     "") or ""
        evaluation = getattr(state, "evaluation_previous_goal", "") or ""

        tipo = "action"
        if evaluation and "success" in evaluation.lower():   tipo = "success"
        elif evaluation and any(w in evaluation.lower() for w in ("fail", "error", "unable")): tipo = "warn"

        mensaje = next_goal or f"Ejecutando paso {step_n}"
        if evaluation:
            mensaje = f"{evaluation} → {next_goal}" if next_goal else evaluation

        # ── Detección y resolución de CAPTCHA ───────────────────
        captcha_keywords = ["captcha", "recaptcha", "hcaptcha", "robot", "verify"]
        texto_lower = (mensaje + " " + memory).lower()
        if any(kw in texto_lower for kw in captcha_keywords):
            await _emit("warn", f"🔒 CAPTCHA detectado en paso {step_n}", {"step_n": step_n, "url": url, "captcha_detected": True})
            if CAPSOLVER_API_KEY and sess.agent and sess.agent.browser_session:
                await _emit("info", "🔐 Resolviendo CAPTCHA con CapSolver API...")
                try:
                    cpage = await sess.agent.browser_session.get_current_page()
                    if cpage:
                        # Detección directa sin depender de diagnostics.py (que puede fallar)
                        captcha_info = await cpage.evaluate("""
                            () => {
                                // reCAPTCHA v2 iframe
                                const iframe = document.querySelector('iframe[src*="recaptcha/api2"]');
                                if (iframe) {
                                    const m = iframe.src.match(/[?&]k=([^&]+)/);
                                    return m ? {type:'recaptcha_v2', site_key:m[1]} : null;
                                }
                                // reCAPTCHA div
                                const div = document.querySelector('.g-recaptcha,[data-sitekey]');
                                if (div) return {type:'recaptcha_v2', site_key: div.getAttribute('data-sitekey')};
                                // hCaptcha
                                const hc = document.querySelector('[data-hcaptcha-sitekey],.h-captcha');
                                if (hc) return {type:'hcaptcha', site_key: hc.getAttribute('data-sitekey') || hc.getAttribute('data-hcaptcha-sitekey')};
                                return null;
                            }
                        """)
                        if captcha_info and captcha_info.get("site_key"):
                            from modules.captcha_solver import solve as capsolver_solve, inject as capsolver_inject
                            token = await capsolver_solve(
                                captcha_info["type"], captcha_info["site_key"],
                                cpage.url, CAPSOLVER_API_KEY
                            )
                            if token:
                                await capsolver_inject(cpage, token, captcha_info["type"])
                                await _emit("success", "✅ CAPTCHA resuelto e inyectado")
                            else:
                                await _emit("warn", "⚠️ CapSolver no pudo resolver el token")
                        else:
                            await _emit("warn", "⚠️ CAPTCHA en pantalla pero no se encontró site_key — el bot intentará continuar")
                except Exception as ce:
                    await _emit("warn", f"⚠️ Error CapSolver: {ce}")
            else:
                await _emit("warn", "⚠️ CAPSOLVER_API_KEY no configurada — CAPTCHA bloqueará la tarea")
        else:
            await _emit(tipo, mensaje, {"step_n": step_n, "url": url, "memory": memory})

        # ── Registrar en action_log ──────────────────────────────
        sess.action_log.append({
            "paso": step_n,
            "objetivo": next_goal[:200],
            "url": url,
            "timestamp": datetime.now().isoformat(),
        })

        # ── Detectar campos del formulario en la página ──────────
        if sess.agent and sess.agent.browser_session:
            try:
                page = await sess.agent.browser_session.get_current_page()
                if page and url and not url.startswith("about:"):
                    asyncio.create_task(_scan_and_emit_form_fields(page, session_id, url))
            except Exception:
                pass

        # ── Screenshot ──────────────────────────────────────────
        screenshot = getattr(browser_state, "screenshot", None)
        if screenshot:
            if isinstance(screenshot, bytes):
                screenshot = base64.b64encode(screenshot).decode()
            if isinstance(screenshot, str) and screenshot.strip():
                await manager.send(session_id, {
                    "tipo": "screenshot", "screenshot": screenshot,
                    "url": url, "step_n": step_n,
                })

        # Anti-detección
        await asyncio.sleep(random.gauss(1.0, 0.3))

        # ── Pausa ────────────────────────────────────────────────
        if sess.paused:
            await _emit("info", "⏸ Agente pausado — escríbeme qué hacer", {"paused": True})
            await sess.pause_event.wait()
            # Si pararon mientras estaba pausado, abortar
            if not sess.running:
                raise asyncio.CancelledError("Detenido durante pausa")
            # Inyectar guidance al reanudar
            if sess.guidance_messages and sess.agent:
                guidance = " | ".join(sess.guidance_messages)
                sess.guidance_messages.clear()
                await _emit("info", f"💬 Instrucción: {guidance}")
                try:
                    from browser_use.llm.messages import HumanMessage as BUHumanMessage
                    msg = BUHumanMessage(content=f"⚡ Instrucción del operador: {guidance}")
                    if hasattr(sess.agent, '_message_manager') and hasattr(sess.agent._message_manager, '_messages'):
                        sess.agent._message_manager._messages.append(msg)
                except Exception:
                    pass
            await _emit("action", "▶ Agente reanudado", {"paused": False})

    async def on_done(history: AgentHistoryList):
        # Si fue detenido por el usuario, no procesar como completado
        if not sess.running and sess.task and sess.task.cancelled():
            return

        resultado = history.final_result() or ""
        sess.running = False
        success = bool(resultado) and not any(
            w in resultado.lower() for w in ["error", "failed", "no se pudo", "unable"]
        )
        supervisor.finish_agent(session_id=session_id, success=success)

        tipo = "success" if success else "error"
        await _emit(tipo, f"{'✅' if success else '❌'} {resultado[:300]}", {"resultado": resultado})

        # Detectar campos llenados del historial de browser-use
        try:
            for action in history.action_results():
                action_dict = action.model_dump() if hasattr(action, 'model_dump') else {}
                extracted = action_dict.get('extracted_content', '') or ''
                if extracted and len(extracted) < 200:
                    pass  # info extra si necesitamos
        except Exception:
            pass

        reentry = _build_reentry_json(task, sess.action_log, sess.form_fields)
        save_task({
            "id": session_id, "tarea": task, "resultado": resultado[:500],
            "pasos": sess.steps, "duracion": sess.elapsed(),
            "fecha": datetime.now().isoformat(), "exitoso": success,
            "action_log": sess.action_log,
            "form_fields": sess.form_fields,
            "reentry": reentry,
            "modelo": llm_service.model if llm_service else "unknown",
        })
        await manager.send(session_id, {
            "tipo": "task_saved", "mensaje": "Tarea guardada",
            "form_fields": sess.form_fields,
            "action_log": sess.action_log,
            "reentry": reentry,
        })

        if success and sess.current_url:
            try:
                from urllib.parse import urlparse
                portal_host = urlparse(sess.current_url).netloc
                if portal_host:
                    portal_memory.save_flow(
                        portal=portal_host, task_description=task,
                        steps=[{"step": i+1} for i in range(sess.steps)],
                        duration_seconds=float(sess.elapsed()),
                    )
            except Exception:
                pass

    # ── Crear y correr el agente ─────────────────────────────────
    agent = Agent(
        task=task_final,
        llm=llm,
        browser_profile=profile,
        system_prompt=SYSTEM_PROMPT,
        register_new_step_callback=on_step,
        register_done_callback=on_done,
        max_steps=30,
        max_total_time=600,
    )
    sess.agent = agent

    try:
        await _emit("info", "🌐 Abriendo navegador...")
        logger.info(f"🚀 Agente browser-use | Modelo: {llm_service.model} | thinking: high")
        await agent.run(max_steps=30)
        logger.info(f"✅ Agente completó | Pasos: {sess.steps} | Duración: {sess.elapsed()}s")
    except asyncio.CancelledError:
        await _emit("warn", "⛔ Agente detenido por el usuario")
        sess.running = False
    except Exception as e:
        await _emit("error", f"❌ Error del agente: {str(e)}")
        sess.running = False
        logger.error(f"❌ run_agent: {e}", exc_info=True)
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


# ── Startup: Inicializar CapSolver Extension ───────────────────
@app.on_event("startup")
async def startup_capsolver():
    """Inicializa CapSolver en startup (una sola vez)."""
    logger.info("🚀 Inicializando CapSolver Extension en startup...")
    try:
        success = setup_capsolver()
        if success:
            capsolver_path = get_capsolver_extension_path("chrome")
            logger.info(f"✅ CapSolver Extension lista: {capsolver_path}")
        else:
            logger.warning("⚠️ CapSolver Extension no disponible (seguirá sin ella)")
    except Exception as e:
        logger.error(f"❌ Error inicializando CapSolver: {e}")


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
    if not sess:
        return {"ok": False, "mensaje": "Sesión no encontrada"}

    sess.running = False

    # 0. Desbloquear pausa si está esperando — evita que quede colgado
    if not sess.pause_event.is_set():
        sess.pause_event.set()

    # 1. Intentar parada limpia del agente browser-use
    if sess.agent:
        try:
            if hasattr(sess.agent, "stop"):
                await sess.agent.stop()
        except Exception:
            pass
        # Cerrar browser session
        try:
            bs = getattr(sess.agent, "browser_session", None)
            if bs:
                if hasattr(bs, "close"):   await bs.close()
                elif hasattr(bs, "stop"):  await bs.stop()
        except Exception as e:
            logger.warning(f"⚠️ Error cerrando browser: {e}")

    # 2. Cancelar el asyncio task (dispara CancelledError en agent.run)
    if sess.task and not sess.task.done():
        sess.task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(sess.task), timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    # 3. Notificar al frontend por WebSocket
    await manager.send(session_id, {
        "tipo": "stopped",
        "mensaje": "⏹ Agente detenido por el usuario",
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    })

    logger.info(f"⏹ Agente detenido: {session_id}")
    return {"ok": True, "mensaje": "Agente detenido"}


@app.post("/api/pause")
async def api_pause(body: dict):
    """
    Pausa el agente ENTRE pasos y habilita control manual del navegador.
    El agente termina el paso actual y luego espera en on_step.
    """
    session_id = body.get("session_id", "")
    sess = sessions.get(session_id)
    if not sess or not sess.running:
        return {"ok": False, "mensaje": "No hay agente activo"}
    if sess.paused:
        return {"ok": False, "mensaje": "Ya está pausado"}

    sess.pause_event.clear()   # señal de pausa
    sess.human_actions = []
    await manager.send(session_id, {
        "tipo": "info",
        "mensaje": "⏸ Pausando después del paso actual — podés interactuar con el navegador en vivo",
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "paused": True,
    })
    return {"ok": True, "mensaje": "Agente pausado"}


@app.post("/api/resume")
async def api_resume(body: dict):
    """Reanuda el agente. Cualquier clic hecho durante la pausa queda registrado."""
    session_id = body.get("session_id", "")
    sess = sessions.get(session_id)
    if not sess:
        return {"ok": False, "mensaje": "No hay sesión activa"}
    if not sess.paused:
        return {"ok": False, "mensaje": "El agente no está pausado"}

    n_actions = len(sess.human_actions)
    sess.pause_event.set()   # desbloquea on_step
    await manager.send(session_id, {
        "tipo": "success",
        "mensaje": f"▶ Reanudando{'  (' + str(n_actions) + ' acción(es) tuyas grabadas)' if n_actions else ''}",
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "paused": False,
    })
    return {"ok": True, "mensaje": "Agente reanudado", "human_actions": n_actions}


# ── Instrucción en tiempo real al bot ─────────────────────────
@app.post("/api/guide")
async def api_guide(body: dict):
    """
    Envía una instrucción de texto al bot mientras ejecuta.
    Si está pausado, lo reanuda automáticamente con la instrucción.
    Ejemplo: {"session_id": "sess-xxx", "message": "haz clic en el botón Enviar"}
    """
    session_id = body.get("session_id", "")
    message = body.get("message", "").strip()
    sess = sessions.get(session_id)

    if not sess:
        return JSONResponse({"ok": False, "error": "Sesión no encontrada"}, status_code=404)
    if not message:
        return JSONResponse({"ok": False, "error": "Mensaje vacío"}, status_code=400)
    if not sess.running:
        return JSONResponse({"ok": False, "error": "El agente no está corriendo"}, status_code=400)

    sess.guidance_messages.append(message)

    # Si está pausado, reanudar automáticamente para que procese la instrucción
    if sess.paused:
        sess.pause_event.set()
        await manager.send(session_id, {
            "tipo": "action",
            "mensaje": f"💬 Instrucción recibida: {message[:100]} — reanudando",
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "paused": False,
        })

    logger.info(f"💬 Guidance [{session_id}]: {message[:100]}")
    return {"ok": True, "mensaje": "Instrucción enviada al bot"}


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
        "model": llm_service.model if llm_service else "not configured",
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
        page = await bs.get_current_page() if bs and hasattr(bs, "get_current_page") else None
        if not page:
            return JSONResponse({"ok": False, "error": "Página no disponible"}, status_code=404)

        if action == "click":
            element_desc = f"({x},{y})"
            try:
                el_text = await page.evaluate(
                    f"(()=>{{const e=document.elementFromPoint({x},{y});"
                    f"return e?(e.innerText||e.getAttribute('aria-label')||e.tagName):'?';}})()"
                )
                if el_text and el_text != "?":
                    element_desc = f"'{str(el_text).strip()[:40]}' en ({x},{y})"
            except Exception:
                pass
            # browser-use puede tener page.mouse como propiedad asíncrona
            try:
                mouse = page.mouse
                if asyncio.iscoroutine(mouse):
                    mouse = await mouse
                await mouse.click(x, y)
            except Exception:
                # fallback: click via JS
                await page.evaluate(f"(()=>{{const e=document.elementFromPoint({x},{y}); if(e) e.click();}})()")
            if sess.paused:
                sess.human_actions.append(f"clic en {element_desc}")
                await manager.send(session_id, {"tipo": "action", "mensaje": f"👆 Clic grabado: {element_desc}", "timestamp": datetime.now().strftime("%H:%M:%S")})
            return {"ok": True, "recorded": sess.paused}

        elif action == "type":
            try:
                kb = page.keyboard
                if asyncio.iscoroutine(kb):
                    kb = await kb
                await kb.type(text)
            except Exception:
                pass
            if sess.paused and text:
                sess.human_actions.append(f"escribió '{text[:30]}'")
                await manager.send(session_id, {"tipo": "action", "mensaje": f"⌨️ Texto grabado: '{text[:30]}'", "timestamp": datetime.now().strftime("%H:%M:%S")})
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


# ══════════════════════════════════════════════════════════════════
# Shadow Mode — grabar acciones humanas y guardar como flujos
# ══════════════════════════════════════════════════════════════════

@app.post("/api/shadow/start")
async def api_shadow_start(body: dict):
    """Abre navegador visible y empieza a grabar acciones del usuario."""
    if is_shadow_active():
        return {"ok": False, "error": "Shadow recorder ya está activo"}
    start_url = body.get("start_url", "about:blank")
    ok, msg = await start_shadow(start_url)
    return {"ok": ok, "mensaje": msg}


@app.post("/api/shadow/stop")
async def api_shadow_stop(body: dict):
    """Detiene la grabación y guarda el flujo en portal memory."""
    task_description = body.get("task_description", "").strip()
    if not task_description:
        return {"ok": False, "error": "task_description requerido"}
    ok, actions, portal = await stop_shadow(task_description)
    return {
        "ok": ok,
        "steps": len(actions),
        "portal": portal,
        "mensaje": f"Flujo '{task_description}' guardado ({len(actions)} acciones)" if ok else "Error",
    }


@app.get("/api/shadow/status")
async def api_shadow_status():
    return {"active": is_shadow_active()}


# ══════════════════════════════════════════════════════════════════
# Portal Memory — consultar flujos guardados
# ══════════════════════════════════════════════════════════════════

@app.get("/api/memory/flows")
async def api_memory_flows(portal: str | None = None):
    """Lista todos los flujos guardados, opcionalmente filtrados por portal."""
    flows = portal_memory.list_flows(portal=portal)
    return {"flows": flows, "total": len(flows)}


@app.get("/api/memory/check")
async def api_memory_check(portal: str, task: str):
    """Verifica si existe un flujo conocido para este portal+tarea."""
    flow = portal_memory.get_flow(portal=portal, task_description=task)
    if flow:
        return {
            "found": True,
            "step_count": flow["step_count"],
            "run_count": flow["run_count"],
            "duration_seconds": flow["duration_seconds"],
            "last_used": flow["last_used"],
        }
    return {"found": False}


@app.delete("/api/memory/portal/{portal}")
async def api_memory_delete_portal(portal: str):
    """Elimina todos los flujos de un portal."""
    deleted = portal_memory.delete_portal(portal)
    return {"ok": True, "deleted": deleted}

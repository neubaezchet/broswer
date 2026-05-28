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
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Browser-Use ────────────────────────────────────────────────
from browser_use import Agent, Browser, BrowserProfile
from browser_use.llm import ChatGoogle
from browser_use.browser.views import BrowserStateSummary
from browser_use.agent.views import AgentHistoryList, AgentOutput

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("web_app")

# ── Configuración ──────────────────────────────────────────────
# Acepta GEMINI_API_KEY (igual que el backend) o GOOGLE_API_KEY como alias
GOOGLE_API_KEY    = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")
UPLOADS_DIR       = Path(__file__).parent / "uploads"
TASKS_FILE        = Path(__file__).parent / "successful_tasks.json"
STATIC_DIR        = Path(__file__).parent / "static"
UPLOADS_DIR.mkdir(exist_ok=True)

CHROME_PATHS_WINDOWS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\david.baeza\AppData\Local\Google\Chrome\Application\chrome.exe",
]

# En Railway/Linux el navegador es headless (sin pantalla)
IS_RAILWAY = os.getenv("RAILWAY_ENVIRONMENT") is not None or os.getenv("RAILWAY_SERVICE_NAME") is not None
HEADLESS    = IS_RAILWAY or os.getenv("HEADLESS", "false").lower() == "true"

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
    """Resuelve CAPTCHA via CapSolver API. Retorna None si no hay API key."""
    if not CAPSOLVER_API_KEY:
        return None
    task_types = {
        "recaptchav2": "ReCaptchaV2TaskProxyless",
        "recaptchav3": "ReCaptchaV3TaskProxyless",
        "hcaptcha":    "HCaptchaTaskProxyless",
    }
    task_type = task_types.get(captcha_type.lower(), "ReCaptchaV2TaskProxyless")
    async with httpx.AsyncClient(timeout=120) as client:
        # Crear tarea
        r = await client.post("https://api.capsolver.com/createTask", json={
            "clientKey": CAPSOLVER_API_KEY,
            "task": {"type": task_type, "websiteURL": page_url, "websiteKey": site_key},
        })
        data = r.json()
        task_id = data.get("taskId")
        if not task_id:
            return None
        # Esperar resultado (máx 60s)
        for _ in range(30):
            await asyncio.sleep(2)
            r2 = await client.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": CAPSOLVER_API_KEY, "taskId": task_id,
            })
            result = r2.json()
            if result.get("status") == "ready":
                return result.get("solution", {}).get("gRecaptchaResponse")
    return None


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

async def _screenshot_loop(session_id: str):
    """Captura screenshots continuos del navegador y los emite por WebSocket."""
    import base64
    await asyncio.sleep(1.5)   # esperar a que Chrome abra
    sess = sessions.get(session_id)
    while sess and sess.running:
        try:
            agent = sess.agent
            if agent:
                bs = getattr(agent, "browser_session", None)
                if bs:
                    screenshot: str | None = None

                    # Método 1: take_screenshot() directo
                    if hasattr(bs, "take_screenshot"):
                        screenshot = await bs.take_screenshot()

                    # Método 2: CDP captureScreenshot vía cdp_client interno
                    if not screenshot:
                        cdp = getattr(bs, "_cdp_client", None) or getattr(bs, "cdp_client", None)
                        if cdp:
                            try:
                                result = await cdp.send.Page.captureScreenshot()
                                screenshot = getattr(result, "data", None)
                            except Exception:
                                pass

                    # Método 3: Playwright Page.screenshot()
                    if not screenshot:
                        page = getattr(bs, "_page", None) or getattr(bs, "page", None)
                        if page:
                            try:
                                buf = await page.screenshot(type="png")
                                screenshot = base64.b64encode(buf).decode()
                            except Exception:
                                pass

                    if screenshot:
                        await manager.send(session_id, {
                            "tipo": "screenshot",
                            "screenshot": screenshot,
                            "url": sess.current_url,
                        })
        except Exception:
            pass
        await asyncio.sleep(0.8)


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

        # Delay aleatorio anti-detección (0.5s – 2s) entre pasos
        await asyncio.sleep(random.uniform(0.5, 2.0))

        # Screenshot para la pantalla en vivo
        screenshot = getattr(browser_state, "screenshot", None)

        await _emit(tipo, mensaje, {
            "step_n": step_n,
            "url": url,
            "memory": memory,
            "screenshot": screenshot,
        })

    async def on_done(history: AgentHistoryList):
        resultado = history.final_result() or ""
        sess.running = False

        # Detectar CAPTCHA en el resultado (heurística básica)
        captcha_encontrado = any(
            kw in resultado.lower()
            for kw in ["captcha", "recaptcha", "hcaptcha", "robot", "verify"]
        )

        await _emit("success" if resultado else "warn",
                    f"✅ Completado: {resultado}" if resultado else "⚠️ Sin resultado final",
                    {"resultado": resultado, "captcha": captcha_encontrado})

        # Guardar tarea exitosa
        if resultado:
            save_task({
                "id": session_id,
                "tarea": task,
                "resultado": resultado[:500],
                "pasos": sess.steps,
                "duracion": sess.elapsed(),
                "fecha": datetime.now().isoformat(),
            })
            await manager.send(session_id, {"tipo": "task_saved", "mensaje": "Tarea guardada en historial"})

    # ── Crear y correr el agente ───────────────────────────────
    llm = ChatGoogle(
        model="gemini-2.0-flash",
        api_key=GOOGLE_API_KEY,
    )

    agent = Agent(
        task=task_final,
        llm=llm,
        browser_profile=profile,
        register_new_step_callback=on_step,
        register_done_callback=on_done,
    )
    sess.agent = agent  # guardar referencia para screenshots y clicks

    # Loop continuo de screenshots (entre pasos)
    asyncio.create_task(_screenshot_loop(session_id))

    try:
        await _emit("info", "🌐 Abriendo navegador...")
        await agent.run(max_steps=30)
    except asyncio.CancelledError:
        await _emit("warn", "⛔ Agente detenido por el usuario")
        sess.running = False
    except Exception as e:
        await _emit("error", f"❌ Error: {str(e)[:200]}")
        sess.running = False
        logger.exception("Error en agente")


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


# ── Subir PDF ──────────────────────────────────────────────────
@app.post("/api/upload-pdf")
async def api_upload_pdf(file: UploadFile):
    if not file.filename.endswith(".pdf"):
        return JSONResponse({"error": "Solo se aceptan PDFs"}, status_code=400)
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    dest = UPLOADS_DIR / safe_name
    dest.write_bytes(await file.read())
    return {"ok": True, "filename": safe_name, "path": str(dest), "url": f"/uploads/{safe_name}"}


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
        "model": "gemini-2.0-flash",
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

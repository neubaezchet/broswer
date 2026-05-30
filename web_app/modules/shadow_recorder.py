"""
Shadow Recorder — Graba acciones humanas en el navegador y las guarda como flujos reutilizables.

Flujo de uso:
1. Usuario hace POST /api/shadow/start  → se abre un navegador Playwright visible
2. El usuario navega manualmente (clicks, texto, navegación)
3. JS interceptors registran cada acción en window.__shadow_log__
4. Usuario hace POST /api/shadow/stop   → se guardan las acciones en PortalMemory
5. El agente puede recuperar ese flujo en futuras ejecuciones del mismo portal

Las acciones grabadas son: click (con selector CSS inferido), fill, navigate, scroll.
"""

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Browser, Page

logger = logging.getLogger(__name__)

# JS que se inyecta en cada página para capturar acciones humanas
SHADOW_RECORDER_SCRIPT = """
(() => {
    if (window.__shadow_recorder_active__) return;
    window.__shadow_recorder_active__ = true;
    window.__shadow_log__ = window.__shadow_log__ || [];

    function bestSelector(el) {
        if (el.id) return '#' + el.id;
        if (el.name) return `[name="${el.name}"]`;
        if (el.getAttribute('data-testid')) return `[data-testid="${el.getAttribute('data-testid')}"]`;
        const tag = el.tagName.toLowerCase();
        if (el.className) return tag + '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.');
        return tag;
    }

    // Clicks
    document.addEventListener('click', e => {
        const el = e.target;
        if (!el || el.tagName === 'HTML' || el.tagName === 'BODY') return;
        window.__shadow_log__.push({
            type: 'click',
            selector: bestSelector(el),
            text: el.innerText ? el.innerText.trim().substring(0,60) : null,
            ts: Date.now()
        });
    }, true);

    // Form fills (blur = cuando el usuario termina de escribir)
    document.addEventListener('change', e => {
        const el = e.target;
        if (!el || !['INPUT','TEXTAREA','SELECT'].includes(el.tagName)) return;
        window.__shadow_log__.push({
            type: 'fill',
            selector: bestSelector(el),
            value: el.value,
            input_type: el.type || el.tagName.toLowerCase(),
            ts: Date.now()
        });
    }, true);

    // Navegación
    const origPush = history.pushState.bind(history);
    history.pushState = function(state, title, url) {
        window.__shadow_log__.push({ type: 'navigate', url: String(url), ts: Date.now() });
        return origPush(state, title, url);
    };
})()
"""

COLLECT_SHADOW_SCRIPT = """
(() => {
    const log = window.__shadow_log__ || [];
    window.__shadow_log__ = [];  // reset tras colectar
    return log;
})()
"""


class ShadowSession:
    def __init__(self):
        self.browser: Browser | None = None
        self.page: Page | None = None
        self.actions: list[dict[str, Any]] = []
        self.portal: str = ""
        self._playwright = None
        self._collect_task: asyncio.Task | None = None
        self.active = False

    async def start(self, start_url: str = "about:blank") -> bool:
        """Abre navegador visible e instala interceptors."""
        try:
            self._playwright = await async_playwright().start()
            self.browser = await self._playwright.chromium.launch(headless=False)
            context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="es-CO",
                timezone_id="America/Bogota",
            )
            self.page = await context.new_page()

            # Instalar interceptor en cada nueva página que cargue
            await context.add_init_script(SHADOW_RECORDER_SCRIPT)

            if start_url and start_url != "about:blank":
                await self.page.goto(start_url, wait_until="domcontentloaded")
                self.portal = urlparse(start_url).netloc

            self.active = True
            self.actions = []

            # Polling para recoger acciones cada 2s
            self._collect_task = asyncio.create_task(self._poll_actions())
            logger.info(f"🔴 Shadow recorder iniciado — portal: {self.portal or 'pendiente'}")
            return True

        except Exception as e:
            logger.error(f"❌ Error iniciando shadow recorder: {e}")
            return False

    async def stop(self) -> list[dict[str, Any]]:
        """Detiene la grabación, recolecta acciones pendientes y cierra el navegador."""
        self.active = False

        if self._collect_task:
            self._collect_task.cancel()
            try:
                await self._collect_task
            except asyncio.CancelledError:
                pass

        # Colectar lo que quede en el buffer JS
        if self.page:
            try:
                pending = await self.page.evaluate(COLLECT_SHADOW_SCRIPT)
                if pending:
                    self.actions.extend(pending)
            except Exception:
                pass

        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

        logger.info(f"⏹ Shadow recorder detenido — {len(self.actions)} acción(es) grabadas")
        return self.actions

    async def _poll_actions(self) -> None:
        """Recoge acciones del buffer JS cada 2 segundos."""
        while self.active:
            await asyncio.sleep(2)
            if self.page and not self.page.is_closed():
                try:
                    new_actions = await self.page.evaluate(COLLECT_SHADOW_SCRIPT)
                    if new_actions:
                        # Actualizar portal si no está definido
                        if not self.portal:
                            try:
                                self.portal = urlparse(self.page.url).netloc
                            except Exception:
                                pass
                        self.actions.extend(new_actions)
                        logger.debug(f"🎬 Shadow: +{len(new_actions)} acción(es) (total={len(self.actions)})")
                except Exception:
                    pass


# Una única sesión shadow por proceso
_shadow_session: ShadowSession | None = None


async def start_shadow(start_url: str = "about:blank") -> tuple[bool, str]:
    """
    Inicia el shadow recorder.
    Retorna (ok, mensaje).
    """
    global _shadow_session
    if _shadow_session and _shadow_session.active:
        return False, "Shadow recorder ya está activo"
    _shadow_session = ShadowSession()
    ok = await _shadow_session.start(start_url)
    if ok:
        return True, f"Shadow recorder iniciado"
    return False, "Error iniciando shadow recorder"


async def stop_shadow(task_description: str) -> tuple[bool, list[dict], str]:
    """
    Detiene el shadow recorder y guarda el flujo en PortalMemory.
    Retorna (ok, actions, portal).
    """
    global _shadow_session
    if not _shadow_session or not _shadow_session.active:
        return False, [], ""

    portal = _shadow_session.portal or "unknown"
    actions = await _shadow_session.stop()
    _shadow_session = None

    if actions and task_description:
        from modules.portal_memory import portal_memory
        portal_memory.save_flow(
            portal=portal,
            task_description=task_description,
            steps=actions,
        )
        logger.info(f"✅ Flujo shadow guardado: portal={portal} pasos={len(actions)}")

    return True, actions, portal


def is_shadow_active() -> bool:
    return _shadow_session is not None and _shadow_session.active

"""
Solver de CAPTCHA completo via CapSolver API.

Soporta:
- reCAPTCHA v2 (checkbox)
- reCAPTCHA v3 (invisible, con action)
- reCAPTCHA Enterprise (v2 y v3 enterprise)
- Cloudflare Turnstile
- hCaptcha
- FunCaptcha / Arkose Labs

Flujo:
    captcha_info = await get_captcha_info(page)           # diagnostics.py
    solution = await solve(captcha_info, page_url, key)   # este módulo
    await inject(page, solution, captcha_info["type"])    # inyecta en DOM
"""

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CAPSOLVER_URL = "https://api.capsolver.com"
MAX_RETRIES = 3
POLL_INTERVAL = 2       # segundos entre polls
MAX_POLL_WAIT = 120     # tiempo máximo esperando resolución (segundos)


# ── Mapeo tipo → task type de CapSolver ──────────────────────────────────────
# Referencia: https://docs.capsolver.com/en/guide/captcha/
TASK_TYPES: dict[str, str] = {
    "recaptcha_v2":              "ReCaptchaV2TaskProxyless",
    "recaptcha_v3":              "ReCaptchaV3TaskProxyless",
    # Enterprise: CapSolver usa tipos distintos para v2 y v3 enterprise.
    # Los portales EPS colombianos usan enterprise v3 (render= en el script).
    # Si el diagnóstico detecta enterprise sin versión clara → asumimos v3.
    "recaptcha_enterprise":      "ReCaptchaV3EnterpriseTaskProxyless",
    "recaptcha_v2_enterprise":   "ReCaptchaV2EnterpriseTaskProxyless",
    "recaptcha_v3_enterprise":   "ReCaptchaV3EnterpriseTaskProxyless",
    "turnstile":                 "AntiTurnstileTaskProxyless",
    "hcaptcha":                  "HCaptchaTaskProxyless",
    "funcaptcha_arkose":         "FunCaptchaTaskProxyless",
}


def _build_task(captcha_type: str, site_key: str, page_url: str, extra: dict | None = None) -> dict:
    """Construye el payload de tarea para CapSolver según el tipo de CAPTCHA."""
    task_type = TASK_TYPES.get(captcha_type, "ReCaptchaV2TaskProxyless")
    task: dict[str, Any] = {
        "type": task_type,
        "websiteURL": page_url,
        "websiteKey": site_key,
    }

    # reCAPTCHA v3 requiere pageAction (la acción definida en el sitio)
    if captcha_type in ("recaptcha_v3", "recaptcha_enterprise"):
        task["pageAction"] = (extra or {}).get("action", "submit")
        task["minScore"] = (extra or {}).get("min_score", 0.5)

    if extra:
        for k, v in extra.items():
            if k not in ("action", "min_score") and k not in task:
                task[k] = v

    return task


async def _create_task(client: httpx.AsyncClient, api_key: str, task: dict) -> str | None:
    """Crea una tarea en CapSolver y retorna el taskId."""
    try:
        r = await client.post(
            f"{CAPSOLVER_URL}/createTask",
            json={"clientKey": api_key, "task": task},
            timeout=30,
        )
        data = r.json()

        if data.get("errorId", 0) != 0:
            logger.error(f"❌ CapSolver createTask error: {data.get('errorDescription')}")
            return None

        task_id = data.get("taskId")
        logger.info(f"✅ CapSolver tarea creada: {task_id}")
        return task_id

    except Exception as e:
        logger.error(f"❌ Error creando tarea CapSolver: {e}")
        return None


async def _wait_for_solution(client: httpx.AsyncClient, api_key: str, task_id: str) -> dict | None:
    """Hace polling hasta obtener solución o timeout."""
    polls = MAX_POLL_WAIT // POLL_INTERVAL
    for attempt in range(polls):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            r = await client.post(
                f"{CAPSOLVER_URL}/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=20,
            )
            result = r.json()

            if result.get("status") == "ready":
                logger.info(f"✅ CAPTCHA resuelto en {(attempt + 1) * POLL_INTERVAL}s")
                return result.get("solution", {})

            if result.get("errorId", 0) != 0:
                logger.error(f"❌ CapSolver error: {result.get('errorDescription')}")
                return None

        except Exception as e:
            logger.warning(f"⚠️ Poll {attempt + 1} fallido: {e}")
            continue

    logger.warning(f"⚠️ Timeout esperando CAPTCHA ({MAX_POLL_WAIT}s)")
    return None


async def solve(
    captcha_type: str,
    site_key: str,
    page_url: str,
    api_key: str | None = None,
    extra: dict | None = None,
) -> str | None:
    """
    Resuelve un CAPTCHA vía CapSolver. Reintenta hasta MAX_RETRIES veces.

    Retorna el token/solución como string, o None si falla.
    """
    api_key = api_key or os.getenv("CAPSOLVER_API_KEY", "")
    if not api_key:
        logger.warning("⚠️ CAPSOLVER_API_KEY no configurada")
        return None

    if not site_key:
        logger.warning(f"⚠️ site_key vacío para {captcha_type} — no se puede resolver")
        return None

    task = _build_task(captcha_type, site_key, page_url, extra)
    logger.info(f"🔐 Resolviendo {captcha_type} | key: {site_key[:20]}... | url: {page_url}")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                task_id = await _create_task(client, api_key, task)
                if not task_id:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(3 * attempt)
                        continue
                    return None

                solution = await _wait_for_solution(client, api_key, task_id)
                if solution:
                    # Extraer el token correcto según tipo
                    token = (
                        solution.get("gRecaptchaResponse")   # recaptcha v2/v3/enterprise
                        or solution.get("token")              # turnstile, hcaptcha, funcaptcha
                        or solution.get("captchaOutput")
                    )
                    if token:
                        return token
                    logger.warning(f"⚠️ Solución recibida pero sin token: {solution}")

        except Exception as e:
            logger.error(f"❌ Intento {attempt}/{MAX_RETRIES} fallido: {e}")

        if attempt < MAX_RETRIES:
            backoff = 5 * attempt
            logger.info(f"♻️ Reintentando en {backoff}s...")
            await asyncio.sleep(backoff)

    logger.error(f"❌ No se pudo resolver {captcha_type} después de {MAX_RETRIES} intentos")
    return None


async def inject(page, token: str, captcha_type: str) -> bool:
    """
    Inyecta la solución del CAPTCHA en el DOM de la página.
    Compatible con reCAPTCHA v2/v3/enterprise, hCaptcha, Turnstile.
    """
    logger.info(f"💉 Inyectando solución de {captcha_type}")

    inject_scripts = {
        "recaptcha_v2": f"""
        (() => {{
            const el = document.getElementById('g-recaptcha-response');
            if (el) el.innerHTML = `{token}`;
            // Llamar callbacks registrados
            try {{
                if (typeof ___grecaptcha_cfg !== 'undefined') {{
                    Object.values(___grecaptcha_cfg.clients || {{}}).forEach(client => {{
                        const cb = client.callback || client['callback'];
                        if (typeof cb === 'function') cb(`{token}`);
                    }});
                }}
            }} catch(e) {{}}
            return true;
        }})()
        """,

        "recaptcha_v3": f"""
        (() => {{
            // v3: inyectar en el hidden input
            const inputs = document.querySelectorAll('input[name*="token"], input[name*="recaptcha"]');
            inputs.forEach(el => {{ el.value = `{token}`; }});
            // Disparar callbacks de execute()
            try {{
                if (window.__recaptchaCallbacks) {{
                    window.__recaptchaCallbacks.forEach(cb => {{ if(typeof cb==='function') cb(`{token}`); }});
                }}
            }} catch(e) {{}}
            return true;
        }})()
        """,

        "recaptcha_enterprise": f"""
        (() => {{
            // Enterprise: igual que v3
            const inputs = document.querySelectorAll(
                'input[name*="token"], input[name*="recaptcha"], #g-recaptcha-response'
            );
            inputs.forEach(el => {{ el.value = `{token}`; el.innerHTML = `{token}`; }});
            try {{
                if (typeof ___grecaptcha_cfg !== 'undefined') {{
                    Object.values(___grecaptcha_cfg.clients || {{}}).forEach(client => {{
                        const cb = client.callback;
                        if (typeof cb === 'function') cb(`{token}`);
                    }});
                }}
            }} catch(e) {{}}
            return true;
        }})()
        """,

        "hcaptcha": f"""
        (() => {{
            const el = document.querySelector('[name="h-captcha-response"], #h-captcha-response');
            if (el) {{ el.value = `{token}`; el.innerHTML = `{token}`; }}
            try {{
                if (window.hcaptcha && typeof window.hcaptcha._handleCaptchaResponse === 'function') {{
                    window.hcaptcha._handleCaptchaResponse(`{token}`);
                }}
            }} catch(e) {{}}
            return true;
        }})()
        """,

        "turnstile": f"""
        (() => {{
            const el = document.querySelector('[name="cf-turnstile-response"]');
            if (el) el.value = `{token}`;
            try {{
                if (window.turnstile && typeof window.turnstile._setResponseCallback === 'function') {{
                    window.turnstile._setResponseCallback(`{token}`);
                }}
            }} catch(e) {{}}
            return true;
        }})()
        """,
    }

    # Normalizar tipo para buscar en el mapa
    captcha_key = captcha_type.replace("capsolver_", "")
    script = inject_scripts.get(captcha_key, inject_scripts["recaptcha_v2"])

    try:
        result = await page.evaluate(script)
        if result:
            logger.info(f"✅ CAPTCHA inyectado correctamente ({captcha_type})")
            # Dar tiempo para que el sitio procese
            await asyncio.sleep(1.5)
            return True
        return False
    except Exception as e:
        logger.error(f"❌ Error inyectando CAPTCHA: {e}")
        return False


async def handle_captcha(page, api_key: str | None = None) -> bool:
    """
    All-in-one: detecta, resuelve e inyecta el CAPTCHA en la página actual.
    Retorna True si se resolvió exitosamente.
    """
    from modules.diagnostics import get_captcha_info

    captcha_info = await get_captcha_info(page)
    if not captcha_info:
        logger.info("✅ No hay CAPTCHA en esta página")
        return True

    captcha_type = captcha_info.get("type", "recaptcha_v2")
    site_key = captcha_info.get("site_key")
    page_url = page.url

    logger.info(f"🔒 CAPTCHA detectado: {captcha_type} | site_key: {site_key}")

    token = await solve(captcha_type, site_key, page_url, api_key)
    if not token:
        return False

    return await inject(page, token, captcha_type)

"""
Ejecutor inteligente con fallbacks multinivel.

Cuando el agente necesita completar una acción en el portal,
este módulo lo intenta en este orden:

  Nivel 1 — API directa (fetch desde consola del navegador)
             Si se descubrió el endpoint vía intercepción de red, se llama directo.
             Es lo más rápido y lo que menos detección genera.

  Nivel 2 — UI asistida (llenar formulario con comportamiento humano)
             Usa anti_detection.py para simular humano real.

  Nivel 3 — Fuerza bruta razonada (romper modales/popups que bloquean)
             Intenta múltiples estrategias para desbloquear la interfaz.
             Si nada funciona, ejecuta dismiss vía consola del navegador.
"""

import asyncio
import json
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# NIVEL 1 — API DIRECTA
# ══════════════════════════════════════════════════════════════════════════════

async def _level1_direct_api(
    page,
    api_url: str,
    method: str,
    payload: dict,
    headers: dict | None = None,
) -> dict[str, Any]:
    """
    Llama una API interna del portal directamente desde la consola del navegador.
    Usa las cookies/headers de sesión ya activos en el contexto del navegador.
    """
    logger.info(f"⚡ [Nivel 1] API directa: {method} {api_url}")

    default_headers = {"Content-Type": "application/json"}
    if headers:
        default_headers.update(headers)

    script = f"""
    (async () => {{
        try {{
            const response = await fetch({json.dumps(api_url)}, {{
                method: {json.dumps(method)},
                headers: {json.dumps(default_headers)},
                credentials: 'include',
                body: {json.dumps(json.dumps(payload)) if method.upper() not in ('GET', 'HEAD') else 'undefined'}
            }});

            const contentType = response.headers.get('content-type') || '';
            let data;
            if (contentType.includes('json')) {{
                data = await response.json();
            }} else {{
                data = {{ text: await response.text() }};
            }}

            return {{
                success: response.ok,
                status: response.status,
                data: data
            }};
        }} catch(error) {{
            return {{ success: false, error: error.message }};
        }}
    }})()
    """

    try:
        result = await page.evaluate(script)
        if result.get("success"):
            logger.info(f"✅ [Nivel 1] Éxito: status {result.get('status')}")
        else:
            logger.warning(f"⚠️ [Nivel 1] Fallo: {result.get('error') or result.get('status')}")
        return result
    except Exception as e:
        logger.error(f"❌ [Nivel 1] Error: {e}")
        return {"success": False, "error": str(e), "level": 1}


# ══════════════════════════════════════════════════════════════════════════════
# NIVEL 2 — UI ASISTIDA CON COMPORTAMIENTO HUMANO
# ══════════════════════════════════════════════════════════════════════════════

async def _level2_ui_fill(page, form_data: dict[str, str], submit: bool = True) -> dict[str, Any]:
    """
    Llena el formulario campo por campo simulando comportamiento humano.
    Usa anti_detection.py para typing y clicks.
    """
    logger.info(f"🖱️ [Nivel 2] UI asistida: {len(form_data)} campos")

    from modules.anti_detection import anti_detection

    filled = 0
    errors = []

    for field_name, value in form_data.items():
        # Buscar el selector correcto (por name, id, placeholder)
        selector = await _find_field_selector(page, field_name)
        if not selector:
            errors.append(f"Campo no encontrado: {field_name}")
            continue

        try:
            # Limpiar campo antes de escribir
            await page.evaluate(f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) {{
                    el.focus();
                    el.select();
                    el.value = '';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
            }})()
            """)

            await asyncio.sleep(random.uniform(0.2, 0.5))

            # Escribir con simulación humana
            await anti_detection.human_type(page, selector, str(value))

            # Trigger eventos de validación
            await page.evaluate(f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) {{
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                }}
            }})()
            """)

            filled += 1
            await asyncio.sleep(random.uniform(0.3, 0.9))

        except Exception as e:
            errors.append(f"Error en {field_name}: {e}")
            logger.warning(f"⚠️ Error llenando {field_name}: {e}")

    logger.info(f"📝 [Nivel 2] {filled}/{len(form_data)} campos llenados")

    if submit and filled > 0:
        await asyncio.sleep(random.uniform(0.5, 1.5))
        submitted = await _click_submit(page)
        return {
            "success": submitted and not errors,
            "filled": filled,
            "errors": errors,
            "submitted": submitted,
            "level": 2,
        }

    return {
        "success": filled > 0 and not errors,
        "filled": filled,
        "errors": errors,
        "level": 2,
    }


async def _find_field_selector(page, field_name: str) -> str | None:
    """Busca el selector CSS correcto para un campo dado su nombre."""
    candidates = [
        f'[name="{field_name}"]',
        f'[id="{field_name}"]',
        f'[placeholder*="{field_name}"]',
        f'[aria-label*="{field_name}"]',
        f'[data-field="{field_name}"]',
    ]

    for selector in candidates:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                return selector
        except Exception:
            continue

    return None


async def _click_submit(page) -> bool:
    """Hace click en el botón de submit del formulario activo."""
    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Enviar")',
        'button:has-text("Guardar")',
        'button:has-text("Radicar")',
        'button:has-text("Continuar")',
        'button:has-text("Submit")',
        'button:has-text("Save")',
        'button:has-text("Next")',
        '[data-action="submit"]',
    ]

    from modules.anti_detection import anti_detection

    for selector in submit_selectors:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible() and await el.is_enabled():
                await anti_detection.human_click(page, selector)
                logger.info(f"✅ [Nivel 2] Submit: {selector}")
                return True
        except Exception:
            continue

    logger.warning("⚠️ [Nivel 2] No se encontró botón de submit")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# NIVEL 3 — FUERZA BRUTA (ROMPER MODALES Y POPUPS BLOQUEANTES)
# ══════════════════════════════════════════════════════════════════════════════

async def _level3_brute_force(page, target_description: str = "") -> dict[str, Any]:
    """
    Intenta desbloquear la interfaz usando múltiples estrategias.
    Se usa cuando hay un modal, popup, overlay, cookie banner, o elemento
    que bloquea la interacción con el formulario/botón objetivo.
    """
    logger.info(f"💪 [Nivel 3] Fuerza bruta para desbloquear: {target_description}")

    strategies_tried = []
    success = False

    # ── Estrategia 1: Buscar y hacer click en botones de cierre ───────────────
    close_selectors = [
        '[class*="close"]', '[class*="dismiss"]', '[class*="cancel"]',
        '[aria-label*="close"]', '[aria-label*="cerrar"]',
        'button[class*="modal"] ~ button',
        '.modal-close', '.dialog-close', '.popup-close',
        '[data-dismiss]', '[data-bs-dismiss]',
        'button:has-text("×")', 'button:has-text("✕")',
        'button:has-text("Cerrar")', 'button:has-text("Close")',
        'button:has-text("No gracias")', 'button:has-text("Continuar")',
        'button:has-text("Aceptar")', 'button:has-text("Accept")',
        '.cookie-accept', '.cookie-banner button', '#cookie-accept',
        '[class*="overlay"] button', '[class*="popup"] button',
    ]

    for selector in close_selectors:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                await el.click()
                await asyncio.sleep(0.8)
                logger.info(f"✅ [Nivel 3] Click en: {selector}")
                strategies_tried.append(f"click:{selector}")
                success = True
                break
        except Exception:
            continue

    if success:
        return {"success": True, "strategy": strategies_tried, "level": 3}

    # ── Estrategia 2: Presionar Escape ────────────────────────────────────────
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        strategies_tried.append("escape_key")
        logger.info("⌨️ [Nivel 3] Escape presionado")
    except Exception:
        pass

    # ── Estrategia 3: Click fuera del modal ───────────────────────────────────
    try:
        await page.mouse.click(10, 10)
        await asyncio.sleep(0.5)
        strategies_tried.append("click_outside")
        logger.info("🖱️ [Nivel 3] Click fuera del modal")
    except Exception:
        pass

    # ── Estrategia 4: Scroll para revelar el botón objetivo ──────────────────
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await asyncio.sleep(0.5)
        strategies_tried.append("scroll_to_middle")
    except Exception:
        pass

    # ── Estrategia 5: Eliminar overlays vía JS ───────────────────────────────
    removed = await page.evaluate("""
    (() => {
        const removed = [];
        // Elementos con z-index alto (overlays)
        const all = document.querySelectorAll('*');
        for (const el of all) {
            const style = window.getComputedStyle(el);
            const zIndex = parseInt(style.zIndex) || 0;
            const pos = style.position;
            if (zIndex > 100 && (pos === 'fixed' || pos === 'absolute')) {
                const tag = el.tagName.toLowerCase();
                const cls = el.className;
                // No eliminar el contenido principal
                if (tag !== 'body' && tag !== 'html' && tag !== 'main') {
                    const rect = el.getBoundingClientRect();
                    // Solo si cubre área significativa
                    if (rect.width > 200 && rect.height > 100) {
                        el.style.display = 'none';
                        removed.push(tag + '.' + String(cls).split(' ')[0]);
                    }
                }
            }
        }
        return removed;
    })()
    """)

    if removed:
        logger.info(f"🗑️ [Nivel 3] Overlays eliminados vía JS: {removed}")
        strategies_tried.append(f"js_remove_overlays:{removed}")
        success = True

    # ── Estrategia 6: Desbloquear body scroll ─────────────────────────────────
    try:
        await page.evaluate("""
        (() => {
            document.body.style.overflow = 'auto';
            document.documentElement.style.overflow = 'auto';
            // Remover clase no-scroll si existe
            document.body.classList.remove('no-scroll', 'modal-open', 'overflow-hidden');
        })()
        """)
        strategies_tried.append("unlock_scroll")
    except Exception:
        pass

    # ── Estrategia 7: Esperar que el modal desaparezca solo (máx 3s) ─────────
    if not success:
        logger.info("⏳ [Nivel 3] Esperando que el modal desaparezca...")
        for _ in range(6):
            await asyncio.sleep(0.5)
            try:
                # Verificar si ya no hay overlay visible
                has_overlay = await page.evaluate("""
                (() => {
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        const style = window.getComputedStyle(el);
                        if (parseInt(style.zIndex) > 100 && style.position === 'fixed'
                            && style.display !== 'none' && el.offsetWidth > 200) {
                            return true;
                        }
                    }
                    return false;
                })()
                """)
                if not has_overlay:
                    success = True
                    strategies_tried.append("waited_for_auto_close")
                    break
            except Exception:
                break

    return {
        "success": success,
        "strategies_tried": strategies_tried,
        "level": 3,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ORQUESTADOR — execute_with_fallback
# ══════════════════════════════════════════════════════════════════════════════

async def execute_with_fallback(
    page,
    form_data: dict[str, str],
    api_info: dict | None = None,
    target_description: str = "",
) -> dict[str, Any]:
    """
    Ejecuta una acción en el portal con fallback automático entre niveles:

    Nivel 1: API directa (si api_info está disponible)
    Nivel 2: UI asistida con comportamiento humano
    Nivel 3: Fuerza bruta si hay algo bloqueando

    Args:
        page: Página Playwright activa
        form_data: Datos para llenar el formulario {campo: valor}
        api_info: Info del endpoint descubierto {url, method} (opcional)
        target_description: Descripción de lo que se intenta hacer (para logs)

    Returns:
        dict con success, level_used, y datos del resultado
    """
    logger.info(f"🎯 SmartExecutor: {target_description or 'acción en portal'}")

    # ── Nivel 1: API directa ──────────────────────────────────────────────────
    if api_info and api_info.get("url"):
        result = await _level1_direct_api(
            page,
            api_url=api_info["url"],
            method=api_info.get("method", "POST"),
            payload=form_data,
        )
        if result.get("success"):
            return {**result, "level_used": 1}
        logger.info("📉 Nivel 1 falló → intentando Nivel 2")

    # ── Nivel 2: UI asistida ──────────────────────────────────────────────────
    result = await _level2_ui_fill(page, form_data)
    if result.get("success"):
        return {**result, "level_used": 2}

    # Si el nivel 2 falló, puede ser por un modal/overlay bloqueante
    logger.info("📉 Nivel 2 falló → intentando Nivel 3 (desbloqueo de UI)")

    # ── Nivel 3: Fuerza bruta ─────────────────────────────────────────────────
    unblock_result = await _level3_brute_force(page, target_description)
    if unblock_result.get("success"):
        # Reintentar nivel 2 después de desbloquear
        logger.info("♻️ UI desbloqueada → reintentando Nivel 2")
        result = await _level2_ui_fill(page, form_data)
        if result.get("success"):
            return {**result, "level_used": "2_after_3", "unblock": unblock_result}

    return {
        "success": False,
        "level_used": 3,
        "unblock": unblock_result,
        "message": "Todos los niveles fallaron. Intervención manual requerida.",
    }


async def dismiss_blocking_element(page) -> bool:
    """
    Atajo: solo intenta desbloquear la UI sin ejecutar nada más.
    Útil cuando el agente detecta que algo bloquea la pantalla.
    """
    result = await _level3_brute_force(page, "elemento bloqueante")
    return result.get("success", False)

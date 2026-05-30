"""Mapeo automático de arquitectura de portales."""

import json
import logging
from typing import Any
from sqlalchemy.orm import Session
from models.portals import Portal, Formulario, Endpoint

logger = logging.getLogger(__name__)

# ── Script JS para instalar interceptores de red al inicio de sesión ──────────
_NETWORK_INTERCEPTOR_SCRIPT = """
(() => {
    if (window.__agent_interceptor_v2__) return 'already_installed';
    window.__agent_interceptor_v2__ = true;
    window.__intercepted_apis__ = [];

    const _push = (entry) => {
        window.__intercepted_apis__.push(entry);
        if (window.__intercepted_apis__.length > 500) {
            window.__intercepted_apis__ = window.__intercepted_apis__.slice(-500);
        }
    };

    // Interceptar XHR
    const xhrOpen = XMLHttpRequest.prototype.open;
    const xhrSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url, ...args) {
        this.__intercepted_url__ = String(url);
        this.__intercepted_method__ = method.toUpperCase();
        return xhrOpen.apply(this, [method, url, ...args]);
    };
    XMLHttpRequest.prototype.send = function(body) {
        const url = this.__intercepted_url__ || '';
        if (url && !url.match(/(\.js|\.css|\.png|\.jpg|\.gif|\.svg|telemetry|analytics|posthog|sentry)/i)) {
            _push({
                method: this.__intercepted_method__ || 'GET',
                url,
                source: 'xhr',
                has_body: !!body,
                body_preview: body ? String(body).substring(0, 200) : null
            });
        }
        return xhrSend.apply(this, arguments);
    };

    // Interceptar fetch
    const fetchOrig = window.fetch;
    window.fetch = function(url, opts = {}) {
        const u = String(url);
        const method = ((opts && opts.method) || 'GET').toUpperCase();
        if (!u.match(/(\.js|\.css|\.png|\.jpg|analytics|telemetry|posthog)/i)) {
            let bodyPreview = null;
            try {
                if (opts && opts.body) {
                    bodyPreview = typeof opts.body === 'string'
                        ? opts.body.substring(0, 200)
                        : '[non-string body]';
                }
            } catch(e) {}
            _push({ method, url: u, source: 'fetch', has_body: !!opts?.body, body_preview: bodyPreview });
        }
        return fetchOrig.apply(this, arguments);
    };

    return 'installed';
})()
"""

# ── Script JS para recolectar APIs interceptadas (deduplicadas) ───────────────
_COLLECT_APIS_SCRIPT = """
(() => {
    const apis = window.__intercepted_apis__ || [];
    const seen = new Set();
    return apis.filter(api => {
        const key = api.method + ':' + api.url;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
})()
"""


async def install_network_interceptor(page) -> bool:
    """
    Instala interceptores XHR/fetch en la página actual.
    Debe llamarse justo después de cargar cualquier página (incluso antes del login).
    Desde este momento, todas las llamadas de red quedan registradas en window.__intercepted_apis__.
    """
    try:
        result = await page.evaluate(_NETWORK_INTERCEPTOR_SCRIPT)
        logger.info(f"📡 Interceptor de red: {result}")
        return True
    except Exception as e:
        logger.warning(f"⚠️ No se pudo instalar interceptor de red: {e}")
        return False


async def collect_apis(page) -> list[dict]:
    """Recupera y retorna todas las APIs interceptadas hasta ahora."""
    try:
        apis = await page.evaluate(_COLLECT_APIS_SCRIPT)
        logger.info(f"📊 APIs interceptadas: {len(apis)}")
        return apis
    except Exception as e:
        logger.warning(f"⚠️ Error recolectando APIs: {e}")
        return []


async def preview_button(page, selector: str) -> dict[str, Any]:
    """
    Analiza un botón SIN hacer click real.
    Retorna qué acción realizaría al ser presionado:
    - onclick attrs
    - data-attributes
    - formulario al que pertenece
    - APIs que históricamente dispara (si hay interceptores activos)
    """
    try:
        result = await page.evaluate(f"""
        (() => {{
            const btn = document.querySelector({json.dumps(selector)});
            if (!btn) return {{ found: false }};

            const form = btn.form || btn.closest('form');
            const rect = btn.getBoundingClientRect();

            return {{
                found: true,
                tag: btn.tagName,
                text: btn.innerText?.trim() || btn.value || '',
                type: btn.type || 'button',
                disabled: btn.disabled,
                visible: rect.width > 0 && rect.height > 0,
                onclick: btn.getAttribute('onclick'),
                data_attrs: Object.fromEntries(
                    Array.from(btn.attributes)
                        .filter(a => a.name.startsWith('data-') || a.name.startsWith('aria-'))
                        .map(a => [a.name, a.value])
                ),
                form: form ? {{
                    id: form.id,
                    action: form.action,
                    method: form.method?.toUpperCase() || 'GET',
                    field_names: Array.from(form.elements)
                        .filter(el => el.name && el.type !== 'hidden')
                        .map(el => el.name)
                }} : null,
                position: {{ x: rect.x, y: rect.y, w: rect.width, h: rect.height }}
            }};
        }})()
        """)
        logger.debug(f"🔍 Preview de botón '{selector}': {result.get('text', '?')}")
        return result
    except Exception as e:
        logger.warning(f"⚠️ Error en preview_button: {e}")
        return {"found": False, "error": str(e)}


async def map_portal(
    page,
    portal_name: str,
    db: Session,
) -> dict[str, Any]:
    """
    Extrae TODA la arquitectura del portal después del login.

    Guarda en BD:
    - Formularios y sus campos (con validaciones)
    - Navegación
    - Botones y sus acciones
    - APIs descubiertas por intercepción de red
    """
    logger.info(f"🗺️ Mapeando portal: {portal_name}")

    try:
        # Instalar interceptor si no está ya
        await install_network_interceptor(page)

        structure = await page.evaluate("""
        () => {
            // Formularios completos
            const forms = Array.from(document.forms).map(form => ({
                id: form.id || form.name || null,
                name: form.name || null,
                action: form.action || null,
                method: (form.method || 'GET').toUpperCase(),
                fields: Array.from(form.elements)
                    .filter(el => el.tagName !== 'FIELDSET')
                    .map(field => ({
                        name: field.name || field.id || null,
                        type: field.type || field.tagName.toLowerCase(),
                        id: field.id || null,
                        required: field.required,
                        placeholder: field.placeholder || null,
                        pattern: field.pattern || null,
                        maxLength: field.maxLength > 0 ? field.maxLength : null,
                        options: field.tagName === 'SELECT'
                            ? Array.from(field.options).map(o => ({ text: o.text.trim(), value: o.value }))
                            : null,
                        readonly: field.readOnly,
                        disabled: field.disabled,
                        aria_label: field.getAttribute('aria-label') || null,
                        value: field.type === 'password' ? '[HIDDEN]' : (field.value || '')
                    })),
                hidden_tokens: Array.from(form.querySelectorAll('input[type=hidden]'))
                    .map(h => ({
                        name: h.name,
                        is_csrf: /token|csrf|_token|xsrf|authenticity/i.test(h.name)
                    }))
            }));

            // Navegación
            const navigation = Array.from(document.querySelectorAll(
                'nav a, .nav a, [role="navigation"] a, .menu a, .sidebar a, .navbar a'
            ))
                .map(a => ({ text: a.textContent.trim(), href: a.href, title: a.title || null }))
                .filter(n => n.text.length > 0 && n.href && !n.href.startsWith('javascript'));

            // Botones con análisis de acción
            const buttons = Array.from(document.querySelectorAll(
                'button, input[type="button"], input[type="submit"], [role="button"]'
            ))
                .map(b => {
                    const rect = b.getBoundingClientRect();
                    return {
                        text: (b.textContent || b.value || '').trim(),
                        id: b.id || null,
                        name: b.name || null,
                        type: b.type || 'button',
                        class: b.className,
                        onclick: b.getAttribute('onclick'),
                        data_action: b.dataset.action || null,
                        data_target: b.dataset.target || null,
                        form_id: b.form?.id || null,
                        form_action: b.form?.action || null,
                        visible: rect.width > 0 && rect.height > 0
                    };
                })
                .filter(b => b.text.length > 0);

            // Menús desplegables y tabs
            const menus = Array.from(document.querySelectorAll(
                '[class*="dropdown"], [class*="accordion"], [role="tablist"] [role="tab"]'
            ))
                .map(m => ({
                    text: m.textContent.trim().substring(0, 100),
                    role: m.getAttribute('role'),
                    aria_expanded: m.getAttribute('aria-expanded')
                }))
                .filter(m => m.text.length > 0)
                .slice(0, 30);

            return {
                forms,
                navigation,
                buttons,
                menus,
                title: document.title,
                url: window.location.href,
                timestamp: new Date().toISOString(),
            };
        }
        """)

        # Recolectar APIs interceptadas
        apis = await collect_apis(page)
        structure["apis_discovered"] = apis

        form_count = len(structure.get("forms", []))
        nav_count = len(structure.get("navigation", []))
        api_count = len(apis)
        logger.info(
            f"✅ Estructura extraída: {form_count} formularios | "
            f"{nav_count} rutas nav | {api_count} APIs descubiertas"
        )

        # ── Guardar en BD ─────────────────────────────────────────────────────
        portal = db.query(Portal).filter_by(nombre=portal_name).first()
        if not portal:
            portal = Portal(
                nombre=portal_name,
                url=structure["url"],
                estructura_json=structure
            )
            db.add(portal)
        else:
            portal.estructura_json = structure
            portal.url = structure["url"]

        db.flush()

        # Guardar endpoints descubiertos
        for api in apis:
            url = api.get("url", "")
            method = api.get("method", "GET")
            if not url or len(url) > 500:
                continue
            exists = db.query(Endpoint).filter_by(
                portal_id=portal.id, metodo=method, ruta=url
            ).first()
            if not exists:
                db.add(Endpoint(
                    portal_id=portal.id,
                    metodo=method,
                    ruta=url,
                    parametros_json={"body_preview": api.get("body_preview")},
                ))

        db.commit()
        logger.info(f"💾 Portal '{portal_name}' guardado en BD")

        return structure

    except Exception as e:
        logger.error(f"❌ Error mapeando portal: {e}")
        return {}


async def load_portal_map(portal_name: str, db: Session) -> dict[str, Any] | None:
    """Carga la arquitectura de un portal de BD si ya fue mapeado."""
    portal = db.query(Portal).filter_by(nombre=portal_name).first()
    if portal and portal.estructura_json:
        logger.info(f"📦 Usando mapa guardado de '{portal_name}'")
        return portal.estructura_json
    return None


async def get_form_by_name(portal_name: str, form_name: str, db: Session) -> dict[str, Any] | None:
    """Obtiene un formulario específico de un portal mapeado."""
    portal = db.query(Portal).filter_by(nombre=portal_name).first()
    if portal and portal.estructura_json:
        for form in portal.estructura_json.get("forms", []):
            names = [
                str(form.get("name", "")),
                str(form.get("id", "")),
                str(form.get("action", "")),
            ]
            if any(form_name.lower() in n.lower() for n in names if n):
                return form
    return None


async def get_discovered_apis(portal_name: str, db: Session) -> list[dict]:
    """Retorna las APIs descubiertas para un portal desde la BD."""
    portal = db.query(Portal).filter_by(nombre=portal_name).first()
    if not portal:
        return []
    endpoints = db.query(Endpoint).filter_by(portal_id=portal.id).all()
    return [
        {"method": e.metodo, "url": e.ruta, "params": e.parametros_json}
        for e in endpoints
    ]


async def detect_api_endpoint(page, button_id: str) -> dict[str, str] | None:
    """Detecta qué API llama un botón sin hacer click real (por ID)."""
    return await preview_button(page, f"#{button_id}")

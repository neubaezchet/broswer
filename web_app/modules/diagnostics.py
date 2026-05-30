"""
Diagnóstico de seguridad de portales web.

Corre un script JS completo antes de interactuar con cualquier portal:
- Detecta CAPTCHA: reCAPTCHA v2/v3/enterprise, Turnstile, hCaptcha
- Detecta anti-bot: Akamai, DataDome, PerimeterX, Cloudflare
- Extrae site keys reales del DOM
- Mapea formularios en profundidad (campos, validaciones, CSRF tokens)
- Instala interceptores XHR/fetch para descubrir APIs internas
- Retorna estrategia recomendada
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Script JS completo de diagnóstico ──────────────────────────────────────────
DIAGNOSTIC_SCRIPT = """
(() => {
    const report = {
        url: window.location.href,
        timestamp: new Date().toISOString(),
        captcha: [],
        antibot: [],
        framework: [],
        forms: [],
        apis_intercepted: [],
        security_scripts: [],
        security_iframes: [],
        strategy: "basic_stealth"
    };

    // ── 1. SCRIPTS DE SEGURIDAD ────────────────────────────────────────────────
    const allScripts = Array.from(document.scripts).map(s => s.src).filter(Boolean);
    report.security_scripts = allScripts.filter(src =>
        /(recaptcha|captcha|akamai|datadome|imperva|incapsula|cloudflare|turnstile|perimeterx|funcaptcha|arkose|botb)/i.test(src)
    );

    // ── 2. EXTRACTOR DE SITE KEY ───────────────────────────────────────────────
    const extractSiteKey = () => {
        // Método 1: div con data-sitekey
        const divKey = document.querySelector('[data-sitekey]');
        if (divKey) return divKey.getAttribute('data-sitekey');

        // Método 2: script src con render=
        const rcScript = document.querySelector('script[src*="recaptcha"], script[src*="turnstile"]');
        if (rcScript) {
            const m = rcScript.src.match(/[?&](?:render|sitekey)=([^&]+)/);
            if (m) return m[1];
        }

        // Método 3: ___grecaptcha_cfg global
        try {
            if (typeof ___grecaptcha_cfg !== 'undefined') {
                const clients = Object.values(___grecaptcha_cfg.clients || {});
                for (const client of clients) {
                    const key = client.sitekey || client.k;
                    if (key && key.length > 10) return key;
                }
            }
        } catch(e) {}

        // Método 4: buscar en innerHTML de scripts inline
        const inlineScripts = Array.from(document.scripts)
            .filter(s => !s.src)
            .map(s => s.innerHTML);
        for (const code of inlineScripts) {
            const m = code.match(/['"]([A-Za-z0-9_-]{30,})['"]/);
            if (m && !m[1].includes('/')) return m[1];
        }

        return null;
    };

    // ── 3. DETECCIÓN DE CAPTCHA ────────────────────────────────────────────────
    const isEnterprise = allScripts.some(s => s.includes('recaptcha/enterprise'));

    if (window.grecaptcha || allScripts.some(s => s.includes('recaptcha'))) {
        const siteKey = extractSiteKey();
        // v3 tiene execute(), enterprise puede tener ambos
        const hasExecute = typeof (window.grecaptcha || {}).execute === 'function';
        // Detectar versión exacta por el script URL
        const rcV3Script = allScripts.find(s => s.includes('recaptcha') && s.includes('render='));
        const isV3 = hasExecute || !!rcV3Script;

        let captchaType;
        if (isEnterprise && isV3) {
            captchaType = 'recaptcha_v3_enterprise';
        } else if (isEnterprise) {
            captchaType = 'recaptcha_v2_enterprise';
        } else if (isV3) {
            captchaType = 'recaptcha_v3';
        } else {
            captchaType = 'recaptcha_v2';
        }

        report.captcha.push({
            type: captchaType,
            solver: 'capsolver_' + captchaType,
            site_key: siteKey
        });
    }

    if (window.turnstile || allScripts.some(s => s.includes('turnstile'))) {
        report.captcha.push({
            type: 'turnstile',
            solver: 'capsolver_turnstile',
            site_key: extractSiteKey()
        });
    }

    if (window.hcaptcha || allScripts.some(s => s.includes('hcaptcha'))) {
        report.captcha.push({
            type: 'hcaptcha',
            solver: 'capsolver_hcaptcha',
            site_key: extractSiteKey()
        });
    }

    // ── 4. DETECCIÓN DE ANTI-BOT ──────────────────────────────────────────────
    if (window._cf_chl_opt || allScripts.some(s => s.includes('cloudflare') && s.includes('challenge'))) {
        report.antibot.push({ type: 'cloudflare_challenge', strategy: 'wait_and_retry' });
    }
    if (window.ak_bmsc || document.cookie.includes('ak_bmsc')) {
        report.antibot.push({ type: 'akamai_bot_manager', strategy: 'max_human_simulation' });
    }
    if (window.datadome || window.ddjskey) {
        report.antibot.push({ type: 'datadome', strategy: 'residential_proxy' });
    }
    if (window.PerimeterX || window._pxAppId || window._px) {
        report.antibot.push({ type: 'perimeterx', strategy: 'mouse_movement_simulation' });
    }
    if (window.fpcab) {
        report.antibot.push({ type: 'funcaptcha_arkose', strategy: 'capsolver_funcaptcha' });
    }

    // ── 5. FRAMEWORK JS ───────────────────────────────────────────────────────
    if (window.angular || document.querySelector('[ng-app],[ng-controller]')) report.framework.push('Angular');
    if (window.React || window.__REACT_DEVTOOLS_GLOBAL_HOOK__) report.framework.push('React');
    if (window.Vue) report.framework.push('Vue');
    if (window.jQuery || (window.$ && window.$.fn)) report.framework.push('jQuery');
    if (window.__nuxt__) report.framework.push('Nuxt');
    if (window.next) report.framework.push('Next.js');
    if (window.svelte) report.framework.push('Svelte');

    // ── 6. MAPEO PROFUNDO DE FORMULARIOS ──────────────────────────────────────
    document.querySelectorAll('form').forEach((f, i) => {
        const fields = Array.from(f.elements)
            .filter(el => el.tagName !== 'FIELDSET')
            .map(el => ({
                name: el.name || el.id || null,
                type: el.type || el.tagName.toLowerCase(),
                id: el.id || null,
                required: el.required,
                placeholder: el.placeholder || null,
                pattern: el.pattern || null,
                maxLength: el.maxLength > 0 ? el.maxLength : null,
                min: el.min || null,
                max: el.max || null,
                options: el.tagName === 'SELECT'
                    ? Array.from(el.options).map(o => ({ value: o.value, text: o.text.trim() }))
                    : null,
                aria_label: el.getAttribute('aria-label') || null,
                data_attrs: Object.fromEntries(
                    Array.from(el.attributes)
                        .filter(a => a.name.startsWith('data-'))
                        .map(a => [a.name, a.value])
                )
            }));

        const hidden_tokens = Array.from(f.querySelectorAll('input[type=hidden]'))
            .map(h => ({
                name: h.name,
                is_csrf: /token|csrf|_token|xsrf|authenticity/i.test(h.name),
                value_len: h.value ? h.value.length : 0
            }));

        report.forms.push({
            index: i,
            id: f.id || null,
            name: f.name || null,
            action: f.action || null,
            method: (f.method || 'get').toUpperCase(),
            field_count: fields.filter(f => f.type !== 'hidden').length,
            fields,
            hidden_tokens,
            has_csrf: hidden_tokens.some(t => t.is_csrf)
        });
    });

    // ── 7. IFRAMES SOSPECHOSOS ────────────────────────────────────────────────
    report.security_iframes = Array.from(document.querySelectorAll('iframe'))
        .map(f => f.src)
        .filter(Boolean)
        .filter(src => /(captcha|recaptcha|hcaptcha|challenge|turnstile)/i.test(src));

    // ── 8. INTERCEPTAR XHR + FETCH (instalación única) ───────────────────────
    if (!window.__agent_interceptor_v2__) {
        window.__agent_interceptor_v2__ = true;
        window.__intercepted_apis__ = [];

        const xhrOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url, ...args) {
            const u = String(url);
            if (!u.match(/(\.js|\.css|\.png|\.jpg|\.gif|\.svg|telemetry|analytics|posthog)/i)) {
                window.__intercepted_apis__.push({ method: method.toUpperCase(), url: u, source: 'xhr' });
            }
            return xhrOpen.apply(this, [method, url, ...args]);
        };

        const fetchOrig = window.fetch;
        window.fetch = function(url, opts = {}) {
            const u = String(url);
            const method = ((opts && opts.method) || 'GET').toUpperCase();
            if (!u.match(/(\.js|\.css|\.png|\.jpg|analytics|telemetry)/i)) {
                window.__intercepted_apis__.push({
                    method,
                    url: u,
                    source: 'fetch',
                    has_body: !!(opts && opts.body)
                });
            }
            return fetchOrig.apply(this, arguments);
        };
    }

    report.apis_intercepted = (window.__intercepted_apis__ || []).slice(-100);

    // ── 9. ESTRATEGIA RECOMENDADA ─────────────────────────────────────────────
    if (report.antibot.find(a => a.type === 'akamai_bot_manager')) {
        report.strategy = 'max_human_simulation';
    } else if (report.antibot.find(a => a.type === 'perimeterx')) {
        report.strategy = 'human_simulation_with_proxy';
    } else if (report.antibot.find(a => a.type === 'cloudflare_challenge')) {
        report.strategy = 'residential_proxy_stealth';
    } else if (report.antibot.find(a => a.type === 'datadome')) {
        report.strategy = 'residential_proxy_stealth';
    } else if (report.captcha.length > 0) {
        report.strategy = 'capsolver_' + report.captcha[0].type;
    } else if (report.antibot.length > 0) {
        report.strategy = 'human_simulation';
    } else {
        report.strategy = 'basic_stealth';
    }

    window.__AGENT_DIAGNOSIS__ = report;
    return report;
})()
"""

# Script para recuperar APIs interceptadas (se llama después de que el usuario interactúa)
COLLECT_APIS_SCRIPT = """
(() => {
    const apis = window.__intercepted_apis__ || [];
    // Deduplicar por url+method
    const seen = new Set();
    return apis.filter(api => {
        const key = api.method + ':' + api.url;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
})()
"""


async def run_diagnostics(page) -> dict[str, Any]:
    """
    Ejecuta diagnóstico completo de seguridad en la página actual.
    Retorna reporte estructurado + instala interceptores XHR/fetch para el resto de la sesión.
    """
    try:
        url = page.url
    except Exception:
        url = "unknown"
    logger.info(f"🕵️ Diagnóstico de seguridad: {url}")

    try:
        report = await page.evaluate(DIAGNOSTIC_SCRIPT)

        captcha_found = [c["type"] for c in report.get("captcha", [])]
        antibot_found = [a["type"] for a in report.get("antibot", [])]

        if captcha_found or antibot_found:
            logger.warning(f"⚠️ Seguridad detectada → CAPTCHA: {captcha_found} | Anti-bot: {antibot_found}")
        else:
            logger.info("✅ Sin protecciones detectadas — stealth básico suficiente")

        logger.info(
            f"📊 Framework: {report.get('framework', [])} | "
            f"Formularios: {len(report.get('forms', []))} | "
            f"Estrategia: {report.get('strategy')}"
        )
        return report

    except Exception as e:
        logger.error(f"❌ Error en diagnóstico: {e}")
        return {
            "url": url,
            "captcha": [],
            "antibot": [],
            "framework": [],
            "forms": [],
            "apis_intercepted": [],
            "strategy": "basic_stealth",
            "error": str(e)
        }


async def collect_intercepted_apis(page) -> list[dict]:
    """Recupera todas las APIs interceptadas hasta ahora en esta sesión."""
    try:
        return await page.evaluate(COLLECT_APIS_SCRIPT)
    except Exception:
        return []


async def get_captcha_info(page) -> dict[str, Any] | None:
    """Detecta y retorna info del primer CAPTCHA en la página actual."""
    try:
        report = await run_diagnostics(page)
        captchas = report.get("captcha", [])
        return captchas[0] if captchas else None
    except Exception:
        return None


def get_strategy_config(report: dict) -> dict[str, Any]:
    """Dado un reporte de diagnóstico, retorna parámetros de comportamiento para el agente."""
    strategy = report.get("strategy", "basic_stealth")

    base_configs: dict[str, dict] = {
        "basic_stealth": {
            "typing_speed_wpm": (80, 140),
            "delay_between_actions": (0.5, 2.0),
            "mouse_speed": "normal",
            "use_proxy": False,
            "solve_captcha": False,
        },
        "human_simulation": {
            "typing_speed_wpm": (60, 100),
            "delay_between_actions": (1.0, 3.0),
            "mouse_speed": "slow",
            "use_proxy": False,
            "solve_captcha": False,
        },
        "max_human_simulation": {
            "typing_speed_wpm": (40, 80),
            "delay_between_actions": (2.0, 5.0),
            "mouse_speed": "very_slow",
            "use_proxy": True,
            "solve_captcha": False,
        },
        "human_simulation_with_proxy": {
            "typing_speed_wpm": (50, 90),
            "delay_between_actions": (1.5, 4.0),
            "mouse_speed": "slow",
            "use_proxy": True,
            "solve_captcha": False,
        },
        "residential_proxy_stealth": {
            "typing_speed_wpm": (70, 120),
            "delay_between_actions": (1.0, 3.0),
            "mouse_speed": "normal",
            "use_proxy": True,
            "solve_captcha": False,
        },
    }

    config = base_configs.get(strategy, base_configs["basic_stealth"]).copy()

    # Strategies que empiezan con capsolver_ → necesitan resolver CAPTCHA
    if strategy.startswith("capsolver_"):
        config["solve_captcha"] = True
        config["captcha_type"] = strategy.replace("capsolver_", "")

    return config

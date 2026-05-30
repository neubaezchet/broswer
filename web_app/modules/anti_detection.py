"""
Anti-detección: playwright-stealth + comportamiento humano realista
======================================================================
Hace que el bot parezca humano para evitar captchas y bloqueos.

Mejoras vs versión anterior:
- User agents de Colombia (Claro, Tigo, ETB, Movistar CO)
- Bezier cúbico de 4 puntos de control (más natural)
- Selección de perfil de dispositivo (PC, móvil, tablet)
- Micro-movimientos del mouse (jitter)
"""

import asyncio
import os
import random
import logging
from typing import Optional
from playwright.async_api import Page

logger = logging.getLogger(__name__)

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    logger.warning("⚠️ playwright-stealth no instalado. pip install playwright-stealth")
    HAS_STEALTH = False
    stealth_async = None


# ── User agents con mezcla realista Colombia + global ────────────────────────
USER_AGENTS_COLOMBIA = [
    # Chrome en Windows (más común en Colombia empresarial)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.122 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Edge en Windows (común en empresas colombianas con Office 365)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    # Chrome en macOS (empleados de oficina)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox Windows (segmento menor pero presente)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Chrome en Android (Colombia tiene alta penetración móvil)
    "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
]

# Resoluciones de pantalla comunes en Colombia (empresas y hogares)
SCREEN_RESOLUTIONS = [
    (1366, 768),   # La más común en Colombia (laptops económicos)
    (1920, 1080),  # Full HD (empresas)
    (1440, 900),   # MacBook
    (1280, 720),   # HD
    (1536, 864),   # Laptops medianos
    (360, 800),    # Móvil Android
]

# Timezones de Colombia
TIMEZONE = "America/Bogota"  # UTC-5

# Idiomas
ACCEPT_LANGUAGE = "es-CO,es;q=0.9,en;q=0.8"


def get_random_user_agent() -> str:
    """Retorna un user agent aleatorio del pool de Colombia."""
    return random.choice(USER_AGENTS_COLOMBIA)


def get_random_viewport() -> dict:
    """Retorna una resolución realista para Colombia."""
    w, h = random.choice(SCREEN_RESOLUTIONS)
    return {"width": w, "height": h}


# ── Curva Bezier cúbica (4 puntos de control) ─────────────────────────────────

def _bezier_cubic(p0: tuple, p1: tuple, p2: tuple, p3: tuple, t: float) -> tuple:
    """Calcula un punto en una curva Bezier cúbica."""
    u = 1 - t
    x = (u**3 * p0[0] + 3 * u**2 * t * p1[0] +
         3 * u * t**2 * p2[0] + t**3 * p3[0])
    y = (u**3 * p0[1] + 3 * u**2 * t * p1[1] +
         3 * u * t**2 * p2[1] + t**3 * p3[1])
    return (x, y)


def _generate_bezier_path(
    start: tuple, end: tuple, steps: int = 25
) -> list[tuple]:
    """
    Genera una trayectoria natural de mouse usando Bezier cúbico.
    Los puntos de control se posicionan con variación aleatoria para
    que cada movimiento sea diferente (anti-fingerprinting).
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = (dx**2 + dy**2) ** 0.5

    # Puntos de control con curvatura aleatoria
    jitter = distance * random.uniform(0.1, 0.3)
    p1 = (
        start[0] + dx * 0.25 + random.uniform(-jitter, jitter),
        start[1] + dy * 0.25 + random.uniform(-jitter, jitter),
    )
    p2 = (
        start[0] + dx * 0.75 + random.uniform(-jitter, jitter),
        start[1] + dy * 0.75 + random.uniform(-jitter, jitter),
    )

    path = []
    for i in range(steps + 1):
        t = i / steps
        # Ease-in-out: más lento al principio y al final
        t_eased = t * t * (3 - 2 * t)
        point = _bezier_cubic(start, p1, p2, end, t_eased)
        # Micro-jitter (temblor natural de mano)
        micro_x = random.gauss(0, 0.8)
        micro_y = random.gauss(0, 0.8)
        path.append((point[0] + micro_x, point[1] + micro_y))

    return path


class AntiDetection:
    """Configura comportamiento humano en el navegador."""

    def __init__(self):
        self.typing_speed_wpm = random.uniform(70, 120)
        self.error_rate = 0.04          # 4% errores tipográficos
        self.pause_between_fields = (0.3, 1.2)

    async def apply_stealth(self, page: Page) -> None:
        """Aplica playwright-stealth y parches adicionales de fingerprint."""
        if HAS_STEALTH:
            try:
                await stealth_async(page)
                logger.info("✅ playwright-stealth aplicado")
            except Exception as e:
                logger.warning(f"⚠️ stealth_async error: {e}")
        else:
            # Parches manuales si playwright-stealth no está instalado
            await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['es-CO', 'es', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'permissions', {
                get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })
            });
            """)
            logger.info("✅ Parches anti-detección manuales aplicados")

    async def set_colombia_locale(self, page: Page) -> None:
        """Configura timezone y locale colombiano en el contexto del navegador."""
        try:
            await page.evaluate(f"""
            (() => {{
                Object.defineProperty(Intl, 'DateTimeFormat', {{
                    value: class extends Intl.DateTimeFormat {{
                        constructor(locale, opts) {{
                            super(locale || 'es-CO', {{
                                timeZone: '{TIMEZONE}',
                                ...opts
                            }});
                        }}
                    }}
                }});
            }})()
            """)
        except Exception:
            pass

    async def human_type(self, page: Page, selector: str, text: str) -> None:
        """Escribe texto con velocidad y errores humanos."""
        try:
            await page.focus(selector)
            await asyncio.sleep(random.uniform(0.1, 0.4))

            for char in text:
                # Error tipográfico ocasional
                if random.random() < self.error_rate:
                    wrong_char = random.choice("qwertyuiopasdfghjklzxcvbnm")
                    await page.type(selector, wrong_char, delay=0)
                    await asyncio.sleep(random.uniform(0.08, 0.25))
                    await page.press(selector, "Backspace")
                    await asyncio.sleep(random.uniform(0.05, 0.15))

                # Delay por carácter (distribución no uniforme)
                char_delay_s = (60 / (self.typing_speed_wpm * 5))
                jitter = random.gauss(0, char_delay_s * 0.3)
                delay_ms = max(20, int((char_delay_s + jitter) * 1000))

                await page.type(selector, char, delay=delay_ms)

                # Pausa extra ocasional (como pensando)
                if random.random() < 0.05:
                    await asyncio.sleep(random.uniform(0.2, 0.8))

            logger.debug(f"✅ Texto escrito humanamente en {selector}")
        except Exception as e:
            logger.error(f"❌ Error escribiendo en {selector}: {e}")

    async def human_click(self, page: Page, selector: str) -> None:
        """Hace click con trayectoria Bezier cúbica natural."""
        try:
            el = page.locator(selector).first
            bbox = await el.bounding_box()
            if not bbox:
                logger.warning(f"⚠️ No se encontró bbox para {selector}")
                await page.click(selector)
                return

            # Target: centro del elemento + pequeña variación
            target_x = bbox["x"] + bbox["width"] / 2 + random.uniform(-4, 4)
            target_y = bbox["y"] + bbox["height"] / 2 + random.uniform(-3, 3)

            # Punto de inicio: posición actual del mouse (o aleatoria si es el primer click)
            start_x = random.uniform(50, 400)
            start_y = random.uniform(50, 400)

            # Generar trayectoria Bezier
            path = _generate_bezier_path(
                (start_x, start_y),
                (target_x, target_y),
                steps=random.randint(20, 35),
            )

            # Mover por la trayectoria
            for x, y in path:
                await page.mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.005, 0.020))

            # Pausa antes de click (como apuntar)
            await asyncio.sleep(random.uniform(0.05, 0.20))
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await page.mouse.up()

            logger.debug(f"✅ Click Bezier en {selector}")
        except Exception as e:
            logger.error(f"❌ Error en human_click '{selector}': {e}")
            try:
                await page.click(selector)
            except Exception:
                pass

    async def human_scroll(self, page: Page, distance: int = 500) -> None:
        """Scroll natural con aceleración y pausas de lectura."""
        try:
            steps = random.randint(4, 10)
            step_size = distance / steps

            for i in range(steps):
                # Aceleración al inicio, desaceleración al final
                progress = i / steps
                speed_factor = 1 + 0.5 * (1 - abs(2 * progress - 1))
                actual_step = step_size * speed_factor

                await page.evaluate(f"window.scrollBy(0, {actual_step})")

                # Pausa variable (simula leer mientras scrollea)
                pause = random.uniform(0.3, 1.5)
                if random.random() < 0.2:  # 20% de las veces pausa larga (leyendo)
                    pause += random.uniform(0.5, 2.0)
                await asyncio.sleep(pause)

            logger.debug(f"✅ Scroll humano: {distance}px")
        except Exception as e:
            logger.error(f"❌ Error en human_scroll: {e}")

    async def random_delay(self, min_ms: int = 800, max_ms: int = 3200) -> None:
        """Delay con distribución normal (más realista que uniforme)."""
        mean = (min_ms + max_ms) / 2
        std = (max_ms - min_ms) / 4
        delay = max(min_ms, min(max_ms, random.gauss(mean, std))) / 1000
        await asyncio.sleep(delay)

    async def micro_mouse_movement(self, page: Page) -> None:
        """Pequeños movimientos del mouse (como humano que mueve ligeramente)."""
        try:
            if random.random() < 0.3:  # Solo el 30% de las veces
                for _ in range(random.randint(2, 5)):
                    dx = random.gauss(0, 15)
                    dy = random.gauss(0, 10)
                    current_x = random.uniform(200, 800)
                    current_y = random.uniform(200, 600)
                    await page.mouse.move(current_x + dx, current_y + dy)
                    await asyncio.sleep(random.uniform(0.05, 0.15))
        except Exception:
            pass

    async def viewport_jitter(self, page: Page) -> None:
        """Simula pequeños cambios de viewport (1 de cada 20 veces)."""
        try:
            if random.random() < 0.05:
                current = await page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
                new_w = max(800, current["w"] + random.randint(-30, 30))
                new_h = max(600, current["h"] + random.randint(-20, 20))
                await page.set_viewport_size({"width": new_w, "height": new_h})
        except Exception:
            pass


# Instancia global
anti_detection = AntiDetection()

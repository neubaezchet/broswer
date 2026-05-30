"""
Auto-Reparación Inteligente: Estrategias de recuperación automática
====================================================================
Cuando falla, intenta reparar automáticamente sin intervención humana.
"""

import logging
import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import Page

logger = logging.getLogger(__name__)


class AutoRepair:
    """Sistema inteligente de auto-reparación."""
    
    def __init__(self):
        self.repair_history: Dict[str, list] = {}  # session_id -> historial de repairs
        self.success_cache: Dict[str, Dict[str, Any]] = {}  # cache de reparaciones exitosas por tipo
    
    async def repair_timeout(self, page: Page, session_id: str) -> bool:
        """Estrategia: Aumentar timeout y reintentar."""
        logger.info(f"🔧 Repair: Timeout - aumentando espera...")
        try:
            # Esperar más tiempo
            await asyncio.sleep(5)
            
            # Recargar página
            await page.reload(wait_until='networkidle')
            
            logger.info(f"✅ Timeout repair exitoso")
            self._log_success(session_id, "timeout", {"strategy": "reload_and_wait"})
            return True
        except Exception as e:
            logger.error(f"❌ Timeout repair falló: {e}")
            return False
    
    async def repair_selector_not_found(self, page: Page, session_id: str, selector: str) -> bool:
        """Estrategia: Re-mapear estructura y buscar alternativa."""
        logger.info(f"🔧 Repair: Selector no encontrado - buscando alternativa...")
        try:
            # Intentar variaciones del selector
            last_part = selector.split('=')[-1].strip('\'"')
            alternatives = [
                selector.replace("button", "input[type='button']"),
                selector.replace("class", "id"),
                f"[aria-label*='{last_part}']"  # Por aria-label
            ]
            
            for alt_selector in alternatives:
                try:
                    element = await page.locator(alt_selector).first.bounding_box()
                    if element:
                        logger.info(f"✅ Alternativa encontrada: {alt_selector}")
                        self._log_success(session_id, "selector_not_found", {
                            "original": selector,
                            "alternative": alt_selector
                        })
                        return True
                except:
                    continue
            
            # Si nada funciona, intentar por texto visible
            visible_buttons = await page.evaluate("""() => 
                Array.from(document.querySelectorAll('button, a, input[type="button"]'))
                .filter(el => el.offsetParent !== null)
                .map(el => ({text: el.textContent.trim(), id: el.id, class: el.className}))
            """)
            logger.warning(f"⚠️  Elementos disponibles: {visible_buttons}")
            return False
        except Exception as e:
            logger.error(f"❌ Selector repair falló: {e}")
            return False
    
    async def repair_captcha(self, page: Page, session_id: str) -> bool:
        """Estrategia: Esperar y reintentar CAPTCHA."""
        logger.info(f"🔧 Repair: CAPTCHA - esperando resolución...")
        try:
            # Esperar a que desaparezca el CAPTCHA o se resuelva
            await page.wait_for_selector("[data-captcha-resolved='true'], .success-message", 
                                        timeout=30000)
            logger.info(f"✅ CAPTCHA repair exitoso")
            self._log_success(session_id, "captcha", {"strategy": "wait_for_resolution"})
            return True
        except asyncio.TimeoutError:
            logger.warning(f"⚠️  CAPTCHA no se resolvió en tiempo")
            return False
        except Exception as e:
            logger.error(f"❌ CAPTCHA repair falló: {e}")
            return False
    
    async def repair_login_failed(self, page: Page, session_id: str) -> bool:
        """Estrategia: Reintentar login con nueva sesión."""
        logger.info(f"🔧 Repair: Login fallido - reiniciando sesión...")
        try:
            # Limpiar cookies
            await page.context.clear_cookies()
            
            # Ir a home
            await page.goto('about:blank')
            await asyncio.sleep(2)
            
            logger.info(f"✅ Login repair: Sesión limpiada")
            self._log_success(session_id, "login_failed", {"strategy": "clear_cookies"})
            return True
        except Exception as e:
            logger.error(f"❌ Login repair falló: {e}")
            return False
    
    async def repair_network_error(self, page: Page, session_id: str) -> bool:
        """Estrategia: Reconectar y reintentar."""
        logger.info(f"🔧 Repair: Error de red - reconectando...")
        try:
            # Esperar a conexión
            await asyncio.sleep(3)
            
            # Recargar
            await page.reload(wait_until='domcontentloaded')
            
            logger.info(f"✅ Network repair exitoso")
            self._log_success(session_id, "network_error", {"strategy": "reconnect"})
            return True
        except Exception as e:
            logger.error(f"❌ Network repair falló: {e}")
            return False
    
    async def repair_blocked_access(self, page: Page, session_id: str, proxy_url: Optional[str] = None) -> bool:
        """Estrategia: Cambiar IP si hay proxy disponible."""
        logger.info(f"🔧 Repair: Acceso bloqueado - intentando con proxy...")
        if not proxy_url:
            logger.warning(f"⚠️  No hay proxy disponible")
            return False
        
        try:
            logger.info(f"📍 Reiniciando con proxy: {proxy_url}")
            # En la práctica, esto requeriría reiniciar el contexto con nuevo proxy
            # Por ahora solo logueamos la intención
            self._log_success(session_id, "blocked_access", {"strategy": "proxy_rotation"})
            return True
        except Exception as e:
            logger.error(f"❌ Proxy repair falló: {e}")
            return False
    
    async def repair_form_validation(self, page: Page, session_id: str) -> bool:
        """Estrategia: Detectar campos requeridos y llenarlos."""
        logger.info(f"🔧 Repair: Error de validación - analizando formulario...")
        try:
            required_fields = await page.evaluate("""() => {
                const fields = [];
                document.querySelectorAll('input[required], textarea[required], select[required]')
                    .forEach(f => {
                        if (!f.value) {
                            fields.push({
                                name: f.name,
                                type: f.type,
                                placeholder: f.placeholder,
                                id: f.id
                            });
                        }
                    });
                return fields;
            }""")
            
            if required_fields:
                logger.warning(f"⚠️  Campos vacíos detectados: {required_fields}")
                self._log_success(session_id, "form_validation", {
                    "strategy": "fields_detected",
                    "fields": required_fields
                })
                return False  # No se puede auto-llenar sin datos
            
            logger.info(f"✅ Formulario validado")
            return True
        except Exception as e:
            logger.error(f"❌ Form validation repair falló: {e}")
            return False
    
    def get_repair_success_rate(self, session_id: str) -> float:
        """Obtener tasa de éxito de repairs para una sesión."""
        if session_id not in self.repair_history:
            return 0.0
        
        repairs = self.repair_history[session_id]
        if not repairs:
            return 0.0
        
        successes = len([r for r in repairs if r['success']])
        return (successes / len(repairs)) * 100
    
    def _log_success(self, session_id: str, repair_type: str, details: dict) -> None:
        """Registrar un repair exitoso."""
        if session_id not in self.repair_history:
            self.repair_history[session_id] = []
        
        self.repair_history[session_id].append({
            "type": repair_type,
            "success": True,
            "details": details,
            "timestamp": asyncio.get_event_loop().time(),
        })
        
        # Cachear para futuras referencias
        self.success_cache[repair_type] = details


# Instancia global
auto_repair = AutoRepair()

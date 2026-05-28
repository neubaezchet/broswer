"""Gestión de sesiones, cookies y autenticación 2FA."""

import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
import os

logger = logging.getLogger(__name__)


class SessionManager:
    """Gestiona sesiones, cookies cifradas y 2FA."""
    
    def __init__(self):
        # Generar o cargar clave de cifrado
        self.cipher_key = os.getenv("CIPHER_KEY") or Fernet.generate_key()
        self.cipher = Fernet(self.cipher_key)
        self.sessions_cache: Dict[str, Dict[str, Any]] = {}
    
    async def save_session_cookies(
        self,
        portal_name: str,
        username: str,
        cookies: str,
        totp_secret: Optional[str] = None,
    ) -> bool:
        """Guarda cookies cifradas de una sesión."""
        
        try:
            # Cifrar cookies
            encrypted_cookies = self.cipher.encrypt(cookies.encode()).decode()
            
            session_data = {
                "username": username,
                "portal_name": portal_name,
                "cookies": encrypted_cookies,
                "totp_secret": totp_secret,
                "fecha_guardado": datetime.utcnow().isoformat(),
                "expira_en": (datetime.utcnow() + timedelta(hours=2)).isoformat(),
            }
            
            # Guardar en archivo o BD
            # Por ahora lo guardamos en memoria
            cache_key = f"{portal_name}:{username}"
            self.sessions_cache[cache_key] = session_data
            
            logger.info(f"💾 Sesión guardada: {cache_key}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error guardando sesión: {e}")
            return False
    
    async def load_session_cookies(
        self,
        portal_name: str,
        username: str,
    ) -> Optional[Dict[str, Any]]:
        """Carga cookies cifradas si la sesión es válida (<2h)."""
        
        try:
            cache_key = f"{portal_name}:{username}"
            session_data = self.sessions_cache.get(cache_key)
            
            if not session_data:
                logger.warning(f"⚠️ No hay sesión guardada para {cache_key}")
                return None
            
            # Verificar si ha expirado
            expira_en = datetime.fromisoformat(session_data["expira_en"])
            if datetime.utcnow() > expira_en:
                logger.warning(f"⚠️ Sesión expirada para {cache_key}")
                del self.sessions_cache[cache_key]
                return None
            
            # Desencriptar cookies
            encrypted_cookies = session_data["cookies"]
            cookies = self.cipher.decrypt(encrypted_cookies.encode()).decode()
            
            session_data["cookies"] = cookies
            logger.info(f"📦 Sesión reutilizada: {cache_key}")
            return session_data
            
        except Exception as e:
            logger.error(f"❌ Error cargando sesión: {e}")
            return None
    
    async def generate_2fa_code(self, totp_secret: str) -> Optional[str]:
        """Genera código TOTP (Google Authenticator)."""
        
        try:
            import pyotp
            totp = pyotp.TOTP(totp_secret)
            code = totp.now()
            logger.info(f"🔐 Código 2FA generado: {code}")
            return code
            
        except ImportError:
            logger.warning("⚠️ pyotp no instalado, no se puede generar 2FA")
            return None
        except Exception as e:
            logger.error(f"❌ Error generando 2FA: {e}")
            return None
    
    async def inject_cookies(self, page, cookies: str):
        """Inyecta cookies en la página."""
        
        try:
            cookies_list = json.loads(cookies)
            for cookie in cookies_list:
                await page.context.add_cookies([cookie])
            
            logger.info(f"🍪 {len(cookies_list)} cookies inyectadas")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error inyectando cookies: {e}")
            return False
    
    async def extract_cookies(self, page) -> str:
        """Extrae cookies de la página actual."""
        
        try:
            cookies = await page.context.cookies()
            cookies_json = json.dumps(cookies)
            logger.info(f"🍪 {len(cookies)} cookies extraídas")
            return cookies_json
            
        except Exception as e:
            logger.error(f"❌ Error extrayendo cookies: {e}")
            return "{}"
    
    def clear_expired_sessions(self):
        """Limpia sesiones expiradas."""
        
        now = datetime.utcnow()
        expired = [
            key for key, data in self.sessions_cache.items()
            if datetime.fromisoformat(data["expira_en"]) < now
        ]
        
        for key in expired:
            del self.sessions_cache[key]
        
        if expired:
            logger.info(f"🧹 {len(expired)} sesiones expiradas limpiadas")

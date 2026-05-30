"""
Gestión de sesiones, cookies y autenticación 2FA.

Mejora vs versión anterior: las sesiones se persisten en SQLite
(tabla agent_sessions) en vez de solo en memoria. Al reiniciar el
servidor, las sesiones válidas se recuperan automáticamente.
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# BD SQLite separada para sesiones (no mezclar con la de portales)
_DB_PATH = Path(__file__).parent.parent / "agent_sessions.db"
_CIPHER_KEY = os.getenv("CIPHER_KEY", "")

try:
    from cryptography.fernet import Fernet
    if _CIPHER_KEY:
        _fernet = Fernet(_CIPHER_KEY.encode() if isinstance(_CIPHER_KEY, str) else _CIPHER_KEY)
    else:
        _fernet_key = Fernet.generate_key()
        _fernet = Fernet(_fernet_key)
        logger.warning("⚠️ CIPHER_KEY no configurada — usando clave efímera (sesiones no persistirán entre reinicios)")
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    _fernet = None
    logger.warning("⚠️ cryptography no instalada — cookies se guardan sin cifrar")


# ══════════════════════════════════════════════════════════════════════════════
# DB HELPERS
# ══════════════════════════════════════════════════════════════════════════════

@contextmanager
def _db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db():
    with _db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT UNIQUE NOT NULL,
            portal_name TEXT NOT NULL,
            username TEXT NOT NULL,
            cookies_encrypted TEXT NOT NULL,
            totp_secret TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """)


_init_db()


# ══════════════════════════════════════════════════════════════════════════════
# CIFRADO / DESCIFRADO
# ══════════════════════════════════════════════════════════════════════════════

def _encrypt(data: str) -> str:
    if HAS_CRYPTO and _fernet:
        return _fernet.encrypt(data.encode()).decode()
    return data  # sin cifrar si no hay cryptography


def _decrypt(data: str) -> str:
    if HAS_CRYPTO and _fernet:
        try:
            return _fernet.decrypt(data.encode()).decode()
        except Exception:
            # Si la clave cambió (reinicio sin CIPHER_KEY fija), retornar vacío
            return ""
    return data


# ══════════════════════════════════════════════════════════════════════════════
# SESSION MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class SessionManager:
    """Gestiona sesiones, cookies cifradas y 2FA con persistencia SQLite."""

    SESSION_TTL_HOURS = 2  # Las sesiones duran 2 horas

    async def save_session_cookies(
        self,
        portal_name: str,
        username: str,
        cookies: str,
        totp_secret: str | None = None,
    ) -> bool:
        """Guarda cookies cifradas. Persiste en SQLite para sobrevivir reinicios."""
        try:
            cache_key = f"{portal_name}:{username}"
            encrypted = _encrypt(cookies)
            now = datetime.utcnow()
            expires = now + timedelta(hours=self.SESSION_TTL_HOURS)

            with _db() as conn:
                conn.execute("""
                INSERT INTO agent_sessions
                    (cache_key, portal_name, username, cookies_encrypted, totp_secret, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    cookies_encrypted = excluded.cookies_encrypted,
                    totp_secret       = excluded.totp_secret,
                    created_at        = excluded.created_at,
                    expires_at        = excluded.expires_at
                """, (
                    cache_key, portal_name, username,
                    encrypted, totp_secret,
                    now.isoformat(), expires.isoformat()
                ))

            logger.info(f"💾 Sesión guardada: {cache_key} (expira: {expires.strftime('%H:%M:%S')} UTC)")
            return True
        except Exception as e:
            logger.error(f"❌ Error guardando sesión: {e}")
            return False

    async def load_session_cookies(
        self,
        portal_name: str,
        username: str,
    ) -> dict[str, Any] | None:
        """Carga cookies cifradas si la sesión es válida (<2h). Lee de SQLite."""
        try:
            cache_key = f"{portal_name}:{username}"

            with _db() as conn:
                row = conn.execute(
                    "SELECT * FROM agent_sessions WHERE cache_key = ?", (cache_key,)
                ).fetchone()

            if not row:
                logger.info(f"ℹ️ No hay sesión guardada para {cache_key}")
                return None

            expires_at = datetime.fromisoformat(row["expires_at"])
            if datetime.utcnow() > expires_at:
                logger.warning(f"⚠️ Sesión expirada para {cache_key}")
                self._delete_session(cache_key)
                return None

            cookies = _decrypt(row["cookies_encrypted"])
            if not cookies:
                logger.warning(f"⚠️ No se pudo descifrar sesión para {cache_key} (clave expirada?)")
                return None

            logger.info(f"📦 Sesión reutilizada: {cache_key}")
            return {
                "portal_name": row["portal_name"],
                "username": row["username"],
                "cookies": cookies,
                "totp_secret": row["totp_secret"],
                "expires_at": row["expires_at"],
            }
        except Exception as e:
            logger.error(f"❌ Error cargando sesión: {e}")
            return None

    def _delete_session(self, cache_key: str) -> None:
        try:
            with _db() as conn:
                conn.execute("DELETE FROM agent_sessions WHERE cache_key = ?", (cache_key,))
        except Exception:
            pass

    async def generate_2fa_code(self, totp_secret: str) -> str | None:
        """Genera código TOTP (Google Authenticator compatible)."""
        try:
            import pyotp
            code = pyotp.TOTP(totp_secret).now()
            logger.info(f"🔐 Código 2FA generado: {code}")
            return code
        except ImportError:
            logger.warning("⚠️ pyotp no instalado — pip install pyotp")
            return None
        except Exception as e:
            logger.error(f"❌ Error generando 2FA: {e}")
            return None

    async def inject_cookies(self, page, cookies: str) -> bool:
        """Inyecta cookies JSON en la página activa."""
        try:
            cookies_list = json.loads(cookies)
            await page.context.add_cookies(cookies_list)
            logger.info(f"🍪 {len(cookies_list)} cookies inyectadas")
            return True
        except Exception as e:
            logger.error(f"❌ Error inyectando cookies: {e}")
            return False

    async def extract_cookies(self, page) -> str:
        """Extrae cookies de la página actual como JSON."""
        try:
            cookies = await page.context.cookies()
            return json.dumps(cookies)
        except Exception as e:
            logger.error(f"❌ Error extrayendo cookies: {e}")
            return "[]"

    def clear_expired_sessions(self) -> int:
        """Limpia sesiones expiradas de la BD. Retorna número de eliminadas."""
        try:
            with _db() as conn:
                cur = conn.execute(
                    "DELETE FROM agent_sessions WHERE expires_at < ?",
                    (datetime.utcnow().isoformat(),)
                )
                count = cur.rowcount
            if count:
                logger.info(f"🧹 {count} sesiones expiradas eliminadas de SQLite")
            return count
        except Exception as e:
            logger.error(f"❌ Error limpiando sesiones: {e}")
            return 0

    def list_active_sessions(self) -> list[dict]:
        """Lista sesiones activas (para debugging y dashboard)."""
        try:
            with _db() as conn:
                rows = conn.execute(
                    "SELECT portal_name, username, created_at, expires_at "
                    "FROM agent_sessions WHERE expires_at > ?",
                    (datetime.utcnow().isoformat(),)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

"""
Proxy Manager — Rotación automática según diagnóstico de seguridad.

Se activa automáticamente cuando el diagnóstico detecta:
- Akamai Bot Manager → max_human_simulation → proxy residencial colombiano
- DataDome          → residential_proxy_stealth → proxy residencial
- PerimeterX        → human_simulation_with_proxy → proxy residencial

Configuración en .env:
  PROXY_SERVER=http://user:pass@host:port      # proxy único
  PROXY_LIST=http://user:pass@h1:p,http://...  # lista separada por comas

El manager rota entre proxies disponibles. Si solo hay uno, lo reutiliza.
Si no hay proxies configurados, retorna None (sin proxy).
"""

import logging
import os
import random
from typing import Any

logger = logging.getLogger(__name__)

# Estrategias que requieren proxy para funcionar correctamente
STRATEGIES_REQUIRING_PROXY = {
    "max_human_simulation",
    "residential_proxy_stealth",
    "human_simulation_with_proxy",
}


class ProxyManager:
    """Gestiona pool de proxies con rotación automática."""

    def __init__(self):
        self._proxies: list[str] = self._load_proxies()
        self._current_idx = 0
        if self._proxies:
            logger.info(f"🔁 ProxyManager: {len(self._proxies)} proxy(s) cargado(s)")
        else:
            logger.info("ℹ️ ProxyManager: Sin proxies configurados (modo directo)")

    def _load_proxies(self) -> list[str]:
        """Lee proxies desde variables de entorno."""
        proxies: list[str] = []

        # Lista separada por comas: PROXY_LIST=http://u:p@h1:8080,http://u:p@h2:8080
        proxy_list_env = os.getenv("PROXY_LIST", "").strip()
        if proxy_list_env:
            proxies = [p.strip() for p in proxy_list_env.split(",") if p.strip()]

        # Proxy único: PROXY_SERVER=http://user:pass@host:port
        single = os.getenv("PROXY_SERVER", "").strip()
        if single and single not in proxies:
            proxies.append(single)

        return proxies

    def has_proxies(self) -> bool:
        return len(self._proxies) > 0

    def get_next(self) -> str | None:
        """Retorna el siguiente proxy en round-robin. None si no hay proxies."""
        if not self._proxies:
            return None
        proxy = self._proxies[self._current_idx % len(self._proxies)]
        self._current_idx += 1
        return proxy

    def get_random(self) -> str | None:
        """Retorna un proxy aleatorio del pool."""
        if not self._proxies:
            return None
        return random.choice(self._proxies)

    def get_for_strategy(self, strategy: str) -> str | None:
        """
        Retorna proxy si la estrategia lo requiere.
        Para estrategias que no necesitan proxy, retorna None aunque haya proxies configurados.
        """
        if strategy not in STRATEGIES_REQUIRING_PROXY:
            return None
        return self.get_next()

    def get_playwright_proxy_config(self, proxy_url: str) -> dict[str, Any] | None:
        """
        Convierte una URL de proxy al formato que espera Playwright BrowserProfile.

        Input:  'http://user:pass@host:8080'
        Output: {'server': 'http://host:8080', 'username': 'user', 'password': 'pass'}
        """
        if not proxy_url:
            return None
        try:
            from urllib.parse import urlparse
            parsed = urlparse(proxy_url)
            config: dict[str, Any] = {
                "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            }
            if parsed.username:
                config["username"] = parsed.username
            if parsed.password:
                config["password"] = parsed.password
            return config
        except Exception as e:
            logger.warning(f"⚠️ Error parseando proxy URL '{proxy_url}': {e}")
            return None

    def build_profile_kwargs(self, strategy: str) -> dict[str, Any]:
        """
        Retorna kwargs para BrowserProfile basados en la estrategia.
        Si la estrategia requiere proxy y hay proxies disponibles, los incluye.
        """
        kwargs: dict[str, Any] = {}
        proxy_url = self.get_for_strategy(strategy)
        if proxy_url:
            proxy_cfg = self.get_playwright_proxy_config(proxy_url)
            if proxy_cfg:
                kwargs["proxy"] = proxy_cfg
                logger.info(f"🌐 Usando proxy: {proxy_cfg['server']} (estrategia: {strategy})")
        return kwargs


# Instancia global
proxy_manager = ProxyManager()

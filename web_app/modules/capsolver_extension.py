"""
Integración de CapSolver Browser Extension v1.17.0
====================================================

Extrae y carga la extensión de CapSolver directamente en el navegador (Chrome/Firefox)
para resolver CAPTCHA sin usar API. Evita problemas de websiteKey.

Ventajas:
  ✅ No necesita websiteKey correcto (la extensión lo detecta)
  ✅ Resuelve automáticamente sin bloqueo
  ✅ Funciona con todos los CAPTCHA (reCAPTCHA, Turnstile, hCaptcha, etc.)
  ✅ Más rápido que API
  ❌ Requiere extensión cargada en el navegador
"""

import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Rutas de archivos zip de CapSolver (en raíz del proyecto broswer/)
CAPSOLVER_ZIPS = {
    "chrome": Path(__file__).parent.parent.parent / "CapSolver.Browser.Extension-chrome-v1.17.0.zip",
    "firefox": Path(__file__).parent.parent.parent / "CapSolver.Browser.Extension-firefox-v1.17.0.zip",
    "generic_tar": Path(__file__).parent.parent.parent / "capsolver-browser-extension-v.1.17.0.tar.gz",
    "generic_zip": Path(__file__).parent.parent.parent / "capsolver-browser-extension-v.1.17.0.zip",
}

# Directorio donde extraer las extensiones
EXTENSIONS_DIR = Path(__file__).parent.parent / "extensions"
EXTENSIONS_DIR.mkdir(exist_ok=True)


def extract_capsolver_extension(browser_type: str = "chrome") -> Optional[Path]:
    """
    Extrae la extensión de CapSolver según el tipo de navegador.
    
    Args:
        browser_type: "chrome" o "firefox"
    
    Returns:
        Path a la carpeta extraída, o None si falla
    """
    
    # Seleccionar archivo zip correcto
    if browser_type == "chrome":
        zip_file = CAPSOLVER_ZIPS["chrome"]
        extract_folder = EXTENSIONS_DIR / "capsolver-chrome"
    elif browser_type == "firefox":
        zip_file = CAPSOLVER_ZIPS["firefox"]
        extract_folder = EXTENSIONS_DIR / "capsolver-firefox"
    else:
        logger.error(f"❌ browser_type desconocido: {browser_type}")
        return None
    
    # Si ya está extraída, no extraer de nuevo
    if extract_folder.exists() and list(extract_folder.glob("*")):
        logger.info(f"✅ CapSolver {browser_type} ya extraída: {extract_folder}")
        return extract_folder
    
    # Verificar que el zip exista
    if not zip_file.exists():
        logger.error(f"❌ Archivo no encontrado: {zip_file}")
        return None
    
    # Verificar que el zip no esté vacío (< 100KB son archivos corruptos/placeholders)
    if zip_file.stat().st_size < 100000:
        logger.error(f"❌ Archivo de CapSolver muy pequeño (corrupto?): {zip_file} ({zip_file.stat().st_size} bytes)")
        return None
    
    try:
        # Limpiar si existe
        if extract_folder.exists():
            shutil.rmtree(extract_folder)
        
        extract_folder.mkdir(exist_ok=True)
        
        logger.info(f"📦 Extrayendo CapSolver {browser_type}: {zip_file}")
        with zipfile.ZipFile(zip_file, "r") as zf:
            zf.extractall(extract_folder)
        
        logger.info(f"✅ CapSolver {browser_type} extraída: {extract_folder}")
        return extract_folder
        
    except Exception as e:
        logger.error(f"❌ Error extrayendo CapSolver: {e}")
        if extract_folder.exists():
            shutil.rmtree(extract_folder)
        return None


def get_capsolver_extension_path(browser_type: str = "chrome") -> Optional[Path]:
    """
    Obtiene la ruta de la extensión de CapSolver.
    La extrae si no existe.
    """
    if browser_type == "chrome":
        extract_folder = EXTENSIONS_DIR / "capsolver-chrome"
    elif browser_type == "firefox":
        extract_folder = EXTENSIONS_DIR / "capsolver-firefox"
    else:
        return None
    
    # Si ya existe, retornar
    if extract_folder.exists():
        manifest = extract_folder / "manifest.json"
        if manifest.exists():
            logger.info(f"✅ CapSolver {browser_type} lista: {extract_folder}")
            return extract_folder
    
    # Si no existe, extraer
    return extract_capsolver_extension(browser_type)


def get_browser_profile_with_capsolver(
    user_data_dir: Optional[str] = None,
    browser_type: str = "chrome"
) -> Optional[dict]:
    """
    Prepara un BrowserProfile de browser-use con CapSolver cargada.
    
    Retorna un dict con args de Puppeteer/Chromium para cargar extensiones.
    
    Args:
        user_data_dir: Directorio de perfil del usuario (None = temp)
        browser_type: "chrome" o "firefox"
    
    Returns:
        Dict con configuración de browser-use, o None si falla
    """
    
    capsolver_path = get_capsolver_extension_path(browser_type)
    if not capsolver_path:
        logger.error(f"❌ No se pudo extraer CapSolver para {browser_type}")
        return None
    
    # Convertir a ruta absoluta
    abs_path = str(capsolver_path.resolve())
    logger.info(f"🔧 Configurando extensión CapSolver: {abs_path}")
    
    # Argumentos de Chromium/Puppeteer
    browser_profile = {
        "extensions": [abs_path],  # browser-use soporta cargar extensiones
        "additional_args": [
            f"--load-extension={abs_path}",  # Chromium
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    
    if user_data_dir:
        browser_profile["user_data_dir"] = user_data_dir
    
    return browser_profile


# ─────────────────────────────────────────────────────────────────
# Setup inicial (ejecutar una vez al iniciar la app)
# ─────────────────────────────────────────────────────────────────

def setup_capsolver() -> bool:
    """
    Inicializa CapSolver en startup.
    Extrae las extensiones para Chrome y Firefox.
    
    Retorna True si al menos una extensión se configuró correctamente.
    """
    logger.info("🚀 Inicializando CapSolver Extension...")
    
    success_chrome = False
    success_firefox = False
    
    # Extraer Chrome
    chrome_path = extract_capsolver_extension("chrome")
    if chrome_path:
        logger.info(f"✅ CapSolver Chrome lista")
        success_chrome = True
    else:
        logger.warning(f"⚠️ CapSolver Chrome no disponible")
    
    # Extraer Firefox (opcional)
    firefox_path = extract_capsolver_extension("firefox")
    if firefox_path:
        logger.info(f"✅ CapSolver Firefox lista")
        success_firefox = True
    else:
        logger.warning(f"⚠️ CapSolver Firefox no disponible")
    
    if success_chrome or success_firefox:
        logger.info(f"✅ CapSolver inicializado correctamente")
        return True
    else:
        logger.error(f"❌ No se pudo inicializar CapSolver")
        return False

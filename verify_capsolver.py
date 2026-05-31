#!/usr/bin/env python3
"""
Verificador de CapSolver Extension Setup
Checks si todo está configurado correctamente para browser-use + CapSolver.

Ejecución:
    python verify_capsolver.py
"""

import sys
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Agregar web_app al path
sys.path.insert(0, str(Path(__file__).parent / "web_app"))


def check_zip_files():
    """Verificar que los archivos ZIP de CapSolver existan."""
    logger.info("🔍 Verificando archivos ZIP...")
    
    root = Path(__file__).parent
    required_files = [
        "CapSolver.Browser.Extension-chrome-v1.17.0.zip",
        "CapSolver.Browser.Extension-firefox-v1.17.0.zip",
        "capsolver-browser-extension-v.1.17.0.zip",
        "capsolver-browser-extension-v.1.17.0.tar.gz",
    ]
    
    all_exist = True
    for filename in required_files:
        fpath = root / filename
        exists = fpath.exists()
        size_mb = fpath.stat().st_size / (1024*1024) if exists else 0
        status = "✅" if exists else "❌"
        logger.info(f"  {status} {filename} ({size_mb:.1f} MB)")
        if not exists:
            all_exist = False
    
    return all_exist


def check_env_config():
    """Verificar que .env tenga CAPSOLVER_API_KEY."""
    logger.info("\n🔍 Verificando .env...")
    
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        logger.warning("  ❌ .env no existe")
        return False
    
    content = env_file.read_text()
    has_gemini = "GEMINI_API_KEY" in content and "AIzaSy" in content
    has_capsolver = "CAPSOLVER_API_KEY" in content and "CAP-" in content
    
    logger.info(f"  {'✅' if has_gemini else '❌'} GEMINI_API_KEY configurada")
    logger.info(f"  {'✅' if has_capsolver else '❌'} CAPSOLVER_API_KEY configurada")
    
    return has_gemini and has_capsolver


def check_modules():
    """Verificar que los módulos Python existan."""
    logger.info("\n🔍 Verificando módulos...")
    
    web_app = Path(__file__).parent / "web_app"
    required_modules = [
        "modules/capsolver_extension.py",
        "modules/captcha_solver.py",
        "modules/diagnostics.py",
        "app.py",
    ]
    
    all_exist = True
    for module in required_modules:
        fpath = web_app / module
        exists = fpath.exists()
        status = "✅" if exists else "❌"
        logger.info(f"  {status} {module}")
        if not exists:
            all_exist = False
    
    return all_exist


def check_capsolver_import():
    """Verificar que capsolver_extension.py sea importable."""
    logger.info("\n🔍 Verificando import de capsolver_extension...")
    
    try:
        from modules.capsolver_extension import (
            setup_capsolver,
            get_capsolver_extension_path,
        )
        logger.info("  ✅ Módulo importable")
        return True
    except ImportError as e:
        logger.error(f"  ❌ Error importando: {e}")
        return False


def check_setup_capsolver():
    """Ejecutar setup_capsolver() y verificar."""
    logger.info("\n🔍 Ejecutando setup_capsolver()...")
    
    try:
        from modules.capsolver_extension import (
            setup_capsolver,
            get_capsolver_extension_path,
            EXTENSIONS_DIR,
        )
        
        success = setup_capsolver()
        
        if success:
            logger.info("  ✅ Setup completado")
            
            # Verificar extensiones extraídas
            chrome_path = get_capsolver_extension_path("chrome")
            firefox_path = get_capsolver_extension_path("firefox")
            
            if chrome_path and chrome_path.exists():
                manifest = chrome_path / "manifest.json"
                logger.info(f"  ✅ Chrome extraída: {chrome_path.name}/")
                logger.info(f"     Manifest: {'✅' if manifest.exists() else '❌'}")
            
            if firefox_path and firefox_path.exists():
                logger.info(f"  ✅ Firefox extraída: {firefox_path.name}/")
            
            logger.info(f"  📁 Carpeta: {EXTENSIONS_DIR}")
            return True
        else:
            logger.warning("  ⚠️ Setup no completado (extensiones no disponibles)")
            return False
            
    except Exception as e:
        logger.error(f"  ❌ Error ejecutando setup: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Ejecutar todas las verificaciones."""
    logger.info("=" * 60)
    logger.info("Verificador de CapSolver Extension Setup")
    logger.info("=" * 60)
    
    checks = [
        ("Archivos ZIP", check_zip_files),
        ("Configuración .env", check_env_config),
        ("Módulos Python", check_modules),
        ("Import de módulo", check_capsolver_import),
        ("Ejecución setup_capsolver()", check_setup_capsolver),
    ]
    
    results = []
    for name, check_fn in checks:
        try:
            result = check_fn()
            results.append((name, result))
        except Exception as e:
            logger.error(f"❌ Error en {name}: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # Resumen
    logger.info("\n" + "=" * 60)
    logger.info("RESUMEN")
    logger.info("=" * 60)
    
    all_pass = True
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status}: {name}")
        if not result:
            all_pass = False
    
    logger.info("=" * 60)
    
    if all_pass:
        logger.info("✅ ¡Todo configurado correctamente!")
        logger.info("\nPuedes iniciar la app:")
        logger.info("  cd web_app")
        logger.info("  uv run uvicorn app:app --reload")
        return 0
    else:
        logger.warning("❌ Hay problemas de configuración. Ver arriba.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

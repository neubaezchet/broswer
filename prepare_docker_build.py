#!/usr/bin/env python3
"""
Prepare broswer repo for Docker build.
Ensures browser_use/ package is in the correct location.
"""

import shutil
import sys
from pathlib import Path

def setup_for_docker_build():
    """Prepare repo structure for Docker build"""
    
    repo_root = Path(__file__).parent
    
    # Expected locations
    browser_use_target = repo_root / "browser_use"
    browser_use_source = repo_root / "browser-use-main" / "browser_use"
    
    print(f"🔧 Preparando repo para Docker build...")
    print(f"   Raíz: {repo_root}")
    
    # Check if browser_use already exists in root
    if browser_use_target.exists():
        print(f"   ✅ browser_use/ ya está en la raíz")
        return True
    
    # Check if browser_use exists in browser-use-main
    if browser_use_source.exists():
        print(f"   📦 Encontrada en: browser-use-main/browser_use/")
        print(f"   🔗 Creando vínculo simbólico...")
        try:
            # Try symlink first (works on most systems)
            browser_use_target.symlink_to(browser_use_source)
            print(f"   ✅ Vínculo simbólico creado exitosamente")
            return True
        except Exception as e:
            print(f"   ⚠️ Symlink falló ({e}), usando copia...")
            try:
                # Fallback: copy the directory
                shutil.copytree(browser_use_source, browser_use_target)
                print(f"   ✅ browser_use/ copiada exitosamente")
                return True
            except Exception as e2:
                print(f"   ❌ Error copiando: {e2}")
                return False
    else:
        print(f"   ❌ No se encontró browser_use/")
        print(f"      Buscaba en: {browser_use_source}")
        return False

if __name__ == "__main__":
    success = setup_for_docker_build()
    sys.exit(0 if success else 1)

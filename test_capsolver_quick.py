#!/usr/bin/env python3
"""
Test rápido: Verificar CapSolver Chrome solo
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "web_app"))

from modules.capsolver_extension import (
    setup_capsolver,
    get_capsolver_extension_path,
    CAPSOLVER_ZIPS,
    EXTENSIONS_DIR,
)

print("=" * 60)
print("VERIFICADOR CAPSOLVER - MODO RÁPIDO")
print("=" * 60)

# 1. Ver archivos
print("\n1️⃣ Verificando archivos ZIP disponibles:")
for name, path in CAPSOLVER_ZIPS.items():
    if path.exists():
        size_kb = path.stat().st_size / 1024
        valid = "✅" if size_kb > 100 else "⚠️ PEQUEÑO"
        print(f"   {valid} {name}: {size_kb:.1f} KB")
    else:
        print(f"   ❌ {name}: NO EXISTE")

# 2. Setup
print("\n2️⃣ Ejecutando setup_capsolver()...")
try:
    success = setup_capsolver()
    print(f"   Resultado: {'✅ SUCCESS' if success else '❌ FAILED'}")
except Exception as e:
    print(f"   ❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 3. Verificar Chrome
print("\n3️⃣ Verificando Chrome extraída:")
chrome_path = get_capsolver_extension_path("chrome")
if chrome_path:
    print(f"   ✅ Ruta: {chrome_path}")
    manifest = chrome_path / "manifest.json"
    print(f"   Manifest: {'✅' if manifest.exists() else '❌'}")
else:
    print(f"   ❌ No disponible")

# 4. Resumen
print("\n" + "=" * 60)
if chrome_path and manifest.exists():
    print("✅ ¡TODO LISTO! La extensión de CapSolver está lista.")
    print(f"   Inyectaremos: --load-extension={chrome_path}")
else:
    print("❌ Hay problemas. Ver arriba para detalles.")

print("=" * 60)

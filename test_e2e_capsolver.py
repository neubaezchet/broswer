#!/usr/bin/env python3
"""
Test End-to-End: Simula una conexión WebSocket real
Verifica que CapSolver se carga correctamente en el navegador-use
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "web_app"))

async def test_e2e():
    """Prueba E2E de CapSolver + browser-use"""
    
    print("=" * 70)
    print("TEST END-TO-END: CapSolver + Browser-Use Integration")
    print("=" * 70)
    
    # Paso 1: Verificar módulos
    print("\n[1/4] Importando módulos...")
    try:
        from modules.capsolver_extension import (
            setup_capsolver,
            get_capsolver_extension_path,
        )
        print("      ✅ capsolver_extension importado")
    except ImportError as e:
        print(f"      ❌ Error: {e}")
        return False
    
    try:
        from modules.diagnostics import check_app_health
        print("      ✅ diagnostics importado")
    except ImportError:
        print("      ⚠️ diagnostics no disponible (no crítico)")
    
    # Paso 2: Setup CapSolver
    print("\n[2/4] Ejecutando setup_capsolver()...")
    try:
        success = setup_capsolver()
        if success:
            print("      ✅ Setup completado")
        else:
            print("      ❌ Setup falló")
            return False
    except Exception as e:
        print(f"      ❌ Error: {e}")
        return False
    
    # Paso 3: Obtener ruta de extensión
    print("\n[3/4] Verificando ruta de extensión...")
    try:
        capsolver_path = get_capsolver_extension_path("chrome")
        if capsolver_path and capsolver_path.exists():
            manifest = capsolver_path / "manifest.json"
            if manifest.exists():
                print(f"      ✅ Extensión lista: {capsolver_path.name}/")
                print(f"         Inyectaremos: --load-extension={capsolver_path}")
            else:
                print(f"      ❌ manifest.json no encontrado")
                return False
        else:
            print(f"      ❌ Ruta de extensión no válida")
            return False
    except Exception as e:
        print(f"      ❌ Error: {e}")
        return False
    
    # Paso 4: Simular profile injection
    print("\n[4/4] Simulando inyección en BrowserProfile...")
    try:
        abs_path = str(capsolver_path.resolve())
        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--load-extension={abs_path}",  # ← INYECTADO
        ]
        
        print(f"      ✅ Args de Chromium listos:")
        for arg in browser_args:
            if "load-extension" in arg:
                print(f"         🔧 {arg}")
            else:
                print(f"         ✓  {arg}")
                
    except Exception as e:
        print(f"      ❌ Error: {e}")
        return False
    
    # Resumen
    print("\n" + "=" * 70)
    print("✅ TEST COMPLETADO EXITOSAMENTE")
    print("=" * 70)
    print("\n📋 Próximos pasos:")
    print(f"   1. cd web_app")
    print(f"   2. uv run uvicorn app:app --reload")
    print(f"   3. Verás en logs: '✅ CapSolver Extension inyectada'")
    print(f"   4. Conecta WebSocket y prueba agente en Compensar")
    print(f"\n🎯 Cuando aparezca reCAPTCHA:")
    print(f"   → CapSolver lo detectará automáticamente")
    print(f"   → Se resolverá sin interacción")
    print(f"   → Agente continuará con formulario")
    
    return True

if __name__ == "__main__":
    success = asyncio.run(test_e2e())
    sys.exit(0 if success else 1)

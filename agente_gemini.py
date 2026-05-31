"""
Browser-Use con Gemini — Script de demostración local
======================================================
Uso:
    uv run agente_gemini.py

O con una tarea personalizada:
    uv run agente_gemini.py "Busca el precio del dólar hoy en Colombia"
"""

import asyncio
import sys
from dotenv import load_dotenv

load_dotenv()  # Carga .env con GOOGLE_API_KEY

from browser_use import Agent, Browser
from browser_use.llm import ChatGoogle

# ── Tarea ────────────────────────────────────────────────────
tarea = sys.argv[1] if len(sys.argv) > 1 else (
    "Ve a google.com, busca 'precio dolar colombia hoy' "
    "y dime el valor actual del dólar en pesos colombianos."
)

async def main():
    print(f"\n🤖 Tarea: {tarea}\n{'─'*60}")

    browser = Browser()   # Abre Chromium visible (headful)

    agent = Agent(
        task=tarea,
        llm=ChatGoogle(model="gemini-3.5-flash", thinking_level="high"),  # Gemini 3 Flash con razonamiento máximo
        browser=browser,
    )

    resultado = await agent.run()

    print(f"\n{'─'*60}")
    print("✅ Resultado final:")
    print(resultado.final_result() or "(sin resultado textual — revisa el navegador)")

if __name__ == "__main__":
    asyncio.run(main())

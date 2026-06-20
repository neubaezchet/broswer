"""
Browser-Use con Gemini + CapSolver
===================================
Uso:
    uv run agente_gemini.py
    uv run agente_gemini.py "Busca el precio del dólar hoy en Colombia"
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Hace que modules/ (captcha_solver, diagnostics) sean importables
sys.path.insert(0, str(Path(__file__).parent / "web_app"))

from browser_use import Agent, Browser
from browser_use.llm import ChatGoogle
from browser_use.controller import Controller
from browser_use.browser.session import BrowserSession
from browser_use.tools.views import ActionResult

from modules.captcha_solver import solve as capsolver_solve, inject as capsolver_inject
from modules.diagnostics import get_captcha_info

CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")

# ── Controller con acción personalizada para CAPTCHAs ────────────
controller = Controller()

@controller.action(
	"Solve any CAPTCHA blocking the current page using CapSolver API. "
	"Use this when you detect a reCAPTCHA, hCaptcha, or Turnstile challenge."
)
async def solve_captcha(browser_session: BrowserSession) -> ActionResult:
	page = await browser_session.must_get_current_page()

	captcha_info = await get_captcha_info(page)
	if not captcha_info:
		return ActionResult(extracted_content="No CAPTCHA detected on this page.")

	captcha_type = captcha_info.get("type", "recaptcha_v2")
	site_key     = captcha_info.get("site_key", "")
	page_url     = page.url

	if not site_key:
		return ActionResult(error=f"CAPTCHA type '{captcha_type}' detected but no sitekey found.")

	if not CAPSOLVER_API_KEY:
		return ActionResult(error="CAPSOLVER_API_KEY not set in .env — cannot solve CAPTCHA automatically.")

	token = await capsolver_solve(captcha_type, site_key, page_url, CAPSOLVER_API_KEY)
	if not token:
		return ActionResult(error=f"CapSolver failed to resolve {captcha_type} CAPTCHA.")

	success = await capsolver_inject(page, token, captcha_type)
	if success:
		return ActionResult(extracted_content=f"CAPTCHA ({captcha_type}) solved and injected successfully.")
	return ActionResult(error="Token received but injection into DOM failed.")


# ── Tarea ────────────────────────────────────────────────────────
tarea = sys.argv[1] if len(sys.argv) > 1 else (
	"Ve a google.com, busca 'precio dolar colombia hoy' "
	"y dime el valor actual del dólar en pesos colombianos."
)

async def main():
	print(f"\n🤖 Tarea: {tarea}\n{'─'*60}")

	browser = Browser()

	agent = Agent(
		task=tarea,
		llm=ChatGoogle(model="gemini-2.5-flash"),
		browser=browser,
		controller=controller,
	)

	resultado = await agent.run()

	print(f"\n{'─'*60}")
	print("✅ Resultado final:")
	print(resultado.final_result() or "(sin resultado textual — revisa el navegador)")

if __name__ == "__main__":
	asyncio.run(main())

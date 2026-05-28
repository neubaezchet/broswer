"""Ejecución directa de APIs y funciones JavaScript sin pasar por UI."""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def execute_direct_api(
    page,
    api_endpoint: str,
    method: str = "POST",
    payload: Dict[str, Any] = None,
    headers: Dict[str, str] = None,
) -> Optional[Any]:
    """
    Ejecuta una llamada API directa sin clickear nada.
    
    Ej:
        result = await execute_direct_api(
            page,
            "/api/radicar",
            payload={"cedula": "123", "fecha": "2026-05-01"}
        )
    """
    
    if payload is None:
        payload = {}
    if headers is None:
        headers = {"Content-Type": "application/json"}
    
    logger.info(f"⚡ Ejecutando API directo: {method} {api_endpoint}")
    
    try:
        script = f"""
        (async () => {{
            try {{
                const response = await fetch('{api_endpoint}', {{
                    method: '{method}',
                    headers: {json.dumps(headers)},
                    body: JSON.stringify({json.dumps(payload)})
                }});
                
                if (!response.ok) {{
                    throw new Error(`HTTP ${{response.status}}`);
                }}
                
                const data = await response.json();
                return {{
                    success: true,
                    status: response.status,
                    data: data
                }};
            }} catch (error) {{
                return {{
                    success: false,
                    error: error.message
                }};
            }}
        }})()
        """
        
        result = await page.evaluate(script)
        
        if result.get('success'):
            logger.info(f"✅ API ejecutada exitosamente: {result.get('data')}")
        else:
            logger.error(f"❌ Error en API: {result.get('error')}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Error ejecutando API: {e}")
        return {"success": False, "error": str(e)}


async def execute_form_function(
    page,
    function_name: str,
    **kwargs
) -> Optional[Any]:
    """
    Ejecuta una función interna del portal.
    
    Ej:
        result = await execute_form_function(
            page,
            "submitIncapacidad",
            cedula="123456",
            fecha="2026-05-01"
        )
    """
    
    logger.info(f"⚡ Ejecutando función: {function_name}({kwargs})")
    
    try:
        payload_str = json.dumps(kwargs)
        script = f"""
        (async () => {{
            try {{
                const result = await window.{function_name}({payload_str});
                return {{
                    success: true,
                    result: result
                }};
            }} catch (error) {{
                return {{
                    success: false,
                    error: error.message
                }};
            }}
        }})()
        """
        
        result = await page.evaluate(script)
        
        if result.get('success'):
            logger.info(f"✅ Función ejecutada: {result.get('result')}")
        else:
            logger.error(f"❌ Error en función: {result.get('error')}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Error ejecutando función: {e}")
        return {"success": False, "error": str(e)}


async def fill_form_direct(
    page,
    form_data: Dict[str, str],
    submit: bool = False,
) -> bool:
    """Llena un formulario directamente en el DOM."""
    
    logger.info(f"⚡ Llenando formulario con {len(form_data)} campos")
    
    try:
        for field_name, value in form_data.items():
            await page.evaluate(f"""
            () => {{
                const field = document.querySelector('[name="{field_name}"]');
                if (field) {{
                    field.value = '{value}';
                    field.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    field.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }}
            """)
        
        if submit:
            await page.evaluate("""
            () => {
                const submitBtn = document.querySelector('button[type="submit"], input[type="submit"]');
                if (submitBtn) {
                    submitBtn.click();
                    return true;
                }
                return false;
            }
            """)
        
        logger.info(f"✅ Formulario llenado exitosamente")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error llenando formulario: {e}")
        return False


async def intercept_network_call(page, button_id: str) -> Optional[Dict[str, Any]]:
    """
    Intercepta qué API se llama cuando se hace click en un botón.
    Sin hacer click real.
    """
    
    network_log = []
    
    def on_request(request):
        if request.method in ["POST", "PUT", "GET"]:
            network_log.append({
                "method": request.method,
                "url": request.url,
                "headers": dict(request.headers) if hasattr(request, 'headers') else {},
                "postData": request.post_data if hasattr(request, 'post_data') else None,
            })
    
    page.on("request", on_request)
    
    # Hacer click y capturar red
    await page.click(f"#{button_id}")
    await page.wait_for_timeout(1000)  # Esperar a que se procese
    
    page.remove_listener("request", on_request)
    
    logger.info(f"🔍 Llamadas de red interceptadas: {len(network_log)}")
    return network_log[-1] if network_log else None

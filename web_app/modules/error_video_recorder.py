"""
Grabación automática de errores con video + anotaciones visuales
=================================================================
Cuando el agente falla, graba un video del momento exacto.
"""

import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from playwright.async_api import Page, Browser
import json

logger = logging.getLogger(__name__)


class ErrorVideoRecorder:
    """Grabador automático de errores con anotaciones."""
    
    def __init__(self, videos_dir: Path = None):
        self.videos_dir = videos_dir or Path(__file__).parent.parent / "error_videos"
        self.videos_dir.mkdir(exist_ok=True)
        self.is_recording = False
        self.current_video_path = None
        self.error_log = []
    
    async def start_recording(self, browser: Browser, session_id: str) -> str:
        """Iniciar grabación de video."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            video_path = self.videos_dir / f"error_{session_id}_{timestamp}.webm"
            
            # Crear contexto con grabación
            context = await browser.new_context(
                record_video_dir=str(self.videos_dir),
            )
            
            self.is_recording = True
            self.current_video_path = str(video_path)
            self.error_log = []
            
            logger.info(f"🎥 Grabación iniciada: {video_path}")
            return str(video_path)
        except Exception as e:
            logger.error(f"❌ Error iniciando grabación: {e}")
            return None
    
    async def log_error(
        self,
        page: Page,
        error_type: str,
        error_msg: str,
        step_n: int,
        screenshot: Optional[bytes] = None
    ) -> None:
        """Registrar un error con screenshot anotado."""
        try:
            # Capturar screenshot anotado
            if screenshot is None:
                screenshot = await page.screenshot()
            
            # Obtener posición donde falló
            url = page.url
            
            # Crear anotación visual
            annotated = await page.evaluate("""() => {
                // Dibujar rectángulo rojo alrededor del último elemento interactuado
                const last = document.activeElement;
                if (last) {
                    const rect = last.getBoundingClientRect();
                    return {
                        failed_element: last.tagName + (last.id ? '#' + last.id : ''),
                        position: {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height
                        }
                    };
                }
                return null;
            }""")
            
            error_entry = {
                "timestamp": datetime.now().isoformat(),
                "step": step_n,
                "type": error_type,
                "message": error_msg,
                "url": url,
                "screenshot_b64": screenshot.hex()[:100] + "...",  # Preview
                "failed_element": annotated,
            }
            
            self.error_log.append(error_entry)
            logger.warning(f"🔴 Error registrado: {error_type} - {error_msg}")
        except Exception as e:
            logger.error(f"⚠️  Error registrando error: {e}")
    
    async def stop_recording(self, session_id: str, success: bool = False) -> dict:
        """Detener grabación y generar reporte."""
        try:
            self.is_recording = False
            
            status = "✅ EXITOSO" if success else "❌ FALLIDO"
            
            report = {
                "session_id": session_id,
                "status": status,
                "video_path": self.current_video_path,
                "error_count": len(self.error_log),
                "errors": self.error_log,
                "timestamp": datetime.now().isoformat(),
            }
            
            # Guardar reporte JSON
            report_path = self.videos_dir / f"report_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)
            
            logger.info(f"📊 Reporte guardado: {report_path}")
            return report
        except Exception as e:
            logger.error(f"❌ Error deteniendo grabación: {e}")
            return None
    
    def get_error_summary(self) -> str:
        """Generar resumen en lenguaje natural de los errores."""
        if not self.error_log:
            return "Sin errores detectados"
        
        summary_parts = []
        for err in self.error_log:
            elem = err.get('failed_element', {}).get('failed_element', 'elemento desconocido')
            summary_parts.append(f"Paso {err['step']}: {err['message']} en {elem}")
        
        return " → ".join(summary_parts)


# Instancia global
error_recorder = ErrorVideoRecorder()

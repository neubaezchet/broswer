"""Pool de sesiones para ejecutar tareas en paralelo."""

import asyncio
import logging
from typing import List, Dict, Any, Callable, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class SessionStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskResult:
    task_id: str
    status: SessionStatus
    resultado: str = ""
    tiempo_segundos: float = 0.0
    error: str = ""
    pasos: int = 0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class SessionPool:
    """Pool de hasta 20 sesiones paralelas con Browser-Use."""
    
    def __init__(self, max_sessions: int = 20, llm_provider: str = "gemini"):
        self.max_sessions = max_sessions
        self.active_sessions: Dict[int, Dict[str, Any]] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.results: List[TaskResult] = []
        self.llm_provider = llm_provider
        self.lock = asyncio.Lock()
    
    async def run_batch(
        self,
        tasks: List[str],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> List[TaskResult]:
        """
        Distribuye N tareas entre M sesiones (máx 20).
        
        tasks = ["radicar incapacidad 1", "radicar incapacidad 2", ...]
        """
        
        logger.info(f"🚀 Iniciando batch de {len(tasks)} tareas con {min(self.max_sessions, len(tasks))} sesiones")
        
        # Agregar todas las tareas a la cola
        for i, task in enumerate(tasks):
            await self.queue.put({
                "id": i,
                "task": task,
                "timestamp": datetime.utcnow().isoformat()
            })
        
        # Crear workers
        num_workers = min(self.max_sessions, len(tasks))
        workers = [
            asyncio.create_task(self._worker(worker_id, on_progress))
            for worker_id in range(num_workers)
        ]
        
        # Esperar a que terminen todos
        await asyncio.gather(*workers)
        
        logger.info(f"✅ Batch completado: {len(self.results)} resultados")
        return self.results
    
    async def _worker(self, worker_id: int, on_progress: Optional[Callable] = None):
        """Cada worker procesa tareas de la cola."""
        
        logger.info(f"👷 Worker {worker_id} iniciado")
        
        while not self.queue.empty():
            try:
                task_data = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                break
            
            task_id = task_data['id']
            task = task_data['task']
            
            logger.info(f"📋 Worker {worker_id} procesando tarea {task_id}: {task[:50]}")
            
            try:
                # Aquí va la lógica de ejecutar con Browser-Use
                # Por ahora simulamos
                inicio = datetime.utcnow()
                
                # TODO: Reemplazar con lógica real de Browser-Use
                result = await self._execute_task(task)
                
                tiempo = (datetime.utcnow() - inicio).total_seconds()
                
                task_result = TaskResult(
                    task_id=str(task_id),
                    status=SessionStatus.COMPLETED if result.get('success') else SessionStatus.FAILED,
                    resultado=result.get('resultado', ''),
                    tiempo_segundos=tiempo,
                    error=result.get('error', ''),
                    pasos=result.get('pasos', 0),
                )
                
                async with self.lock:
                    self.results.append(task_result)
                
                logger.info(f"✅ Tarea {task_id} completada en {tiempo:.2f}s")
                
                if on_progress:
                    on_progress(len(self.results), self.queue.qsize())
                
            except Exception as e:
                logger.error(f"❌ Error en tarea {task_id}: {e}")
                
                task_result = TaskResult(
                    task_id=str(task_id),
                    status=SessionStatus.FAILED,
                    error=str(e),
                )
                
                async with self.lock:
                    self.results.append(task_result)
            
            finally:
                self.queue.task_done()
        
        logger.info(f"👷 Worker {worker_id} finalizado")
    
    async def _execute_task(self, task: str) -> Dict[str, Any]:
        """Ejecuta una tarea con Browser-Use (conectar aquí)."""
        
        # Placeholder - aquí va la integración real con Browser-Use
        await asyncio.sleep(2)  # Simular trabajo
        
        return {
            "success": True,
            "resultado": f"Tarea completada: {task[:50]}",
            "pasos": 5,
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Obtiene status actual del pool."""
        
        return {
            "active_sessions": len(self.active_sessions),
            "queue_size": self.queue.qsize(),
            "completed": len(self.results),
            "success_rate": len([r for r in self.results if r.status == SessionStatus.COMPLETED]) / max(len(self.results), 1),
            "avg_time": sum(r.tiempo_segundos for r in self.results) / max(len(self.results), 1),
        }
    
    def get_results_summary(self) -> Dict[str, Any]:
        """Resumen de resultados."""
        
        completed = [r for r in self.results if r.status == SessionStatus.COMPLETED]
        failed = [r for r in self.results if r.status == SessionStatus.FAILED]
        
        return {
            "total": len(self.results),
            "completadas": len(completed),
            "fallidas": len(failed),
            "tasa_exito": len(completed) / max(len(self.results), 1),
            "tiempo_promedio": sum(r.tiempo_segundos for r in self.results) / max(len(self.results), 1),
            "resultados": self.results,
        }

"""
Agente Supervisor: Monitorea todos los agentes en tiempo real
==============================================================
Detecta fallos, auto-repara, y mantiene estadísticas.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import json

logger = logging.getLogger(__name__)


@dataclass
class AgentMetrics:
    """Métricas de un agente individual."""
    session_id: str
    task: str
    status: str  # running, success, failed, paused
    step_current: int
    step_total: int
    start_time: datetime
    end_time: Optional[datetime] = None
    errors: List[str] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []
    
    @property
    def elapsed_seconds(self) -> float:
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()
    
    @property
    def success_rate(self) -> float:
        if self.step_total == 0:
            return 0.0
        return (self.step_current / self.step_total) * 100
    
    def to_dict(self) -> dict:
        data = asdict(self)
        data['start_time'] = self.start_time.isoformat()
        data['end_time'] = self.end_time.isoformat() if self.end_time else None
        data['elapsed_seconds'] = self.elapsed_seconds
        data['success_rate'] = self.success_rate
        return data


class SupervisorAgent:
    """Supervisor central que monitorea todos los agentes."""
    
    def __init__(self):
        self.agents: Dict[str, AgentMetrics] = {}
        self.performance_history: List[dict] = []
        self.auto_repair_attempts = 0
        self.max_auto_repairs = 3
    
    def register_agent(self, session_id: str, task: str) -> None:
        """Registrar nuevo agente."""
        self.agents[session_id] = AgentMetrics(
            session_id=session_id,
            task=task,
            status="running",
            step_current=0,
            step_total=0,
            start_time=datetime.now(),
        )
        logger.info(f"📊 Agente registrado: {session_id}")
    
    def update_agent(
        self,
        session_id: str,
        step_current: int,
        step_total: int,
        status: str = "running",
        error: Optional[str] = None
    ) -> None:
        """Actualizar estado del agente."""
        if session_id not in self.agents:
            logger.warning(f"⚠️  Agente no encontrado: {session_id}")
            return
        
        agent = self.agents[session_id]
        agent.step_current = step_current
        agent.step_total = step_total
        agent.status = status
        
        if error:
            agent.errors.append(error)
            agent.last_error = error
            agent.retry_count += 1
            
            # Detectar patrón de errores
            if len(agent.errors) >= 3:
                recent_errors = agent.errors[-3:]
                if all(e == error for e in recent_errors):
                    logger.warning(f"🔴 Patrón detectado: {session_id} falla repetidamente con: {error}")
                    self._trigger_auto_repair(session_id, error)
    
    def finish_agent(self, session_id: str, success: bool) -> dict:
        """Marcar agente como finalizado."""
        if session_id not in self.agents:
            return {}
        
        agent = self.agents[session_id]
        agent.status = "success" if success else "failed"
        agent.end_time = datetime.now()
        
        # Guardar en historial
        self.performance_history.append(agent.to_dict())
        
        logger.info(
            f"✅ Agente finalizado: {session_id} "
            f"({agent.success_rate:.1f}% completado en {agent.elapsed_seconds:.1f}s)"
        )
        
        return agent.to_dict()
    
    def _trigger_auto_repair(self, session_id: str, error: str) -> None:
        """Intentar auto-reparación cuando hay patrón de errores."""
        agent = self.agents.get(session_id)
        if not agent or self.auto_repair_attempts >= self.max_auto_repairs:
            logger.error(f"❌ Auto-repair fallido para {session_id}: máximo de intentos")
            return
        
        self.auto_repair_attempts += 1
        logger.info(f"🔧 Iniciando auto-repair {self.auto_repair_attempts}/{self.max_auto_repairs} para {session_id}")
        
        # Estrategias de auto-reparación según el error
        repair_strategy = self._get_repair_strategy(error)
        logger.info(f"📋 Estrategia: {repair_strategy}")
        
        # Aquí se ejecutaría la estrategia (en app.py se integraría)
    
    def _get_repair_strategy(self, error: str) -> str:
        """Determinar estrategia de auto-reparación según el tipo de error."""
        if "timeout" in error.lower():
            return "Aumentar timeout y reintentar"
        elif "not found" in error.lower() or "selector" in error.lower():
            return "Re-mapear estructura del sitio y buscar alternativa"
        elif "captcha" in error.lower():
            return "Usar CapSolver o esperar más tiempo"
        elif "login" in error.lower():
            return "Reintentar login con nueva sesión"
        elif "blocked" in error.lower():
            return "Cambiar IP/proxy y reintentar"
        else:
            return "Reintentar con delay aumentado"
    
    def get_dashboard_data(self) -> dict:
        """Datos para dashboard en tiempo real."""
        active_agents = [
            ag for ag in self.agents.values()
            if ag.status == "running"
        ]
        
        completed_agents = [
            ag for ag in self.agents.values()
            if ag.status in ["success", "failed"]
        ]
        
        success_count = len([ag for ag in completed_agents if ag.status == "success"])
        fail_count = len([ag for ag in completed_agents if ag.status == "failed"])
        
        success_rate = (
            (success_count / (success_count + fail_count) * 100)
            if (success_count + fail_count) > 0
            else 0
        )
        
        return {
            "timestamp": datetime.now().isoformat(),
            "active_agents": len(active_agents),
            "completed_agents": len(completed_agents),
            "success_count": success_count,
            "fail_count": fail_count,
            "success_rate": f"{success_rate:.1f}%",
            "auto_repair_attempts": self.auto_repair_attempts,
            "active": [
                {
                    "session_id": ag.session_id,
                    "task": ag.task[:50] + "..." if len(ag.task) > 50 else ag.task,
                    "progress": f"{ag.step_current}/{ag.step_total}",
                    "elapsed": f"{ag.elapsed_seconds:.1f}s",
                    "errors": len(ag.errors),
                }
                for ag in active_agents
            ],
            "completed": [
                {
                    "session_id": ag.session_id,
                    "status": ag.status,
                    "success_rate": f"{ag.success_rate:.1f}%",
                    "duration": f"{ag.elapsed_seconds:.1f}s",
                }
                for ag in completed_agents[-10:]  # Últimos 10
            ]
        }
    
    def get_performance_stats(self) -> dict:
        """Estadísticas agregadas de rendimiento."""
        if not self.performance_history:
            return {"message": "No hay datos de rendimiento"}
        
        total = len(self.performance_history)
        successful = len([ag for ag in self.performance_history if ag['status'] == 'success'])
        avg_time = sum(ag['elapsed_seconds'] for ag in self.performance_history) / total
        
        return {
            "total_completed": total,
            "successful": successful,
            "failed": total - successful,
            "success_rate": f"{(successful/total*100):.1f}%",
            "avg_time": f"{avg_time:.1f}s",
            "total_errors": sum(len(ag['errors']) for ag in self.performance_history),
        }


# Instancia global
supervisor = SupervisorAgent()

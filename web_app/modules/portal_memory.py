"""
Portal Memory — Persistencia de flujos de automatización exitosos por portal.

Aprende de cada tarea completada:
- Qué pasos tomó exactamente (JSON de acciones)
- Qué campos del formulario se usaron
- Cuánto tardó y cuántas veces se ejecutó

En la próxima ejecución del mismo portal+tarea, el agente puede saltar
la fase de exploración y ejecutar el flujo conocido directamente (~10x más rápido).

Usa Jaccard similarity sobre las palabras de la descripción de tarea para
determinar si un flujo guardado coincide con la solicitud actual.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "portal_memory.db"
SIMILARITY_THRESHOLD = 0.45


class PortalMemory:
    """Memoria persistente de flujos de automatización por portal."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_flows (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    portal          TEXT    NOT NULL,
                    task_description TEXT   NOT NULL,
                    steps_json      TEXT    NOT NULL,
                    fields_used     TEXT,
                    success         INTEGER DEFAULT 1,
                    duration_seconds REAL,
                    step_count      INTEGER,
                    run_count       INTEGER DEFAULT 1,
                    last_used       TEXT,
                    created_at      TEXT    DEFAULT (datetime('now'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_portal ON task_flows(portal, success)"
            )
            conn.commit()

    # ── Public API ─────────────────────────────────────────────────────────────

    def save_flow(
        self,
        portal: str,
        task_description: str,
        steps: list[dict[str, Any]],
        fields_used: list[str] | None = None,
        duration_seconds: float | None = None,
    ) -> int:
        """
        Guarda un flujo exitoso.
        Si ya existe un flujo similar para ese portal, lo actualiza (merge inteligente).
        Retorna el ID de la fila.
        """
        steps_json  = json.dumps(steps,            ensure_ascii=False)
        fields_json = json.dumps(fields_used or [], ensure_ascii=False)
        now = datetime.utcnow().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            existing = self._find_similar(conn, portal, task_description)
            if existing:
                conn.execute(
                    """UPDATE task_flows SET
                        steps_json       = ?,
                        fields_used      = ?,
                        duration_seconds = ?,
                        step_count       = ?,
                        run_count        = run_count + 1,
                        last_used        = ?
                    WHERE id = ?""",
                    (steps_json, fields_json, duration_seconds, len(steps), now, existing["id"]),
                )
                conn.commit()
                logger.info(
                    f"🧠 Flujo actualizado: portal={portal} pasos={len(steps)} "
                    f"ejecuciones={existing['run_count'] + 1}"
                )
                return existing["id"]

            cur = conn.execute(
                """INSERT INTO task_flows
                    (portal, task_description, steps_json, fields_used, duration_seconds, step_count, last_used)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (portal, task_description, steps_json, fields_json, duration_seconds, len(steps), now),
            )
            conn.commit()
            row_id = cur.lastrowid
            logger.info(f"🧠 Nuevo flujo guardado: portal={portal} pasos={len(steps)} id={row_id}")
            return row_id

    def get_flow(self, portal: str, task_description: str) -> dict[str, Any] | None:
        """
        Busca un flujo conocido para portal+tarea.
        Retorna None si no hay coincidencia suficiente (score < SIMILARITY_THRESHOLD).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = self._find_similar(conn, portal, task_description)
            if not row:
                return None
            flow = dict(row)
            flow["steps"]       = json.loads(flow.pop("steps_json",  "[]"))
            flow["fields_used"] = json.loads(flow.pop("fields_used", "[]"))
            logger.info(
                f"🧠 Flujo encontrado: portal={portal} pasos={flow['step_count']} "
                f"ejecuciones={flow['run_count']}"
            )
            return flow

    def list_flows(self, portal: str | None = None) -> list[dict[str, Any]]:
        """Lista flujos guardados (sin steps para no saturar el contexto)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            q = (
                "SELECT id, portal, task_description, step_count, run_count, "
                "duration_seconds, last_used, created_at FROM task_flows WHERE success = 1"
            )
            params: tuple = ()
            if portal:
                q += " AND portal = ?"
                params = (portal,)
            q += " ORDER BY portal, run_count DESC"
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    def mark_failed(self, flow_id: int) -> None:
        """Marca un flujo como fallido para que no se reutilice."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE task_flows SET success = 0 WHERE id = ?", (flow_id,))
            conn.commit()

    def delete_portal(self, portal: str) -> int:
        """Elimina todos los flujos de un portal. Retorna la cantidad borrada."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM task_flows WHERE portal = ?", (portal,))
            conn.commit()
            return cur.rowcount

    # ── Internal ────────────────────────────────────────────────────────────────

    @staticmethod
    def _jaccard(a: str, b: str) -> float:
        wa = set(a.lower().split())
        wb = set(b.lower().split())
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / len(wa | wb)

    def _find_similar(
        self, conn: sqlite3.Connection, portal: str, task_description: str
    ) -> sqlite3.Row | None:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM task_flows WHERE portal = ? AND success = 1 "
            "ORDER BY run_count DESC, last_used DESC",
            (portal,),
        ).fetchall()

        best: sqlite3.Row | None = None
        best_score = 0.0
        for row in rows:
            score = self._jaccard(task_description, row["task_description"])
            if score > best_score:
                best_score = score
                best = row

        return best if best_score >= SIMILARITY_THRESHOLD else None


# Instancia global
portal_memory = PortalMemory()

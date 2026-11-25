"""De una instancia validada a un horario auditado.

Orden: carga en SQLite en memoria, prefiltro SQL de alternativas admisibles, solver,
auditoría independiente, conteo de ventanas y exportaciones. Toda la lógica viene del MVP
original (`academic_timetabling_demo`) y de la versión 2 (`timetabling_milp`): este módulo
solo orquesta y traduce el resultado a un registro que la vista y el almacenamiento usan.
"""

import math
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from academic_timetabling_demo.candidates import Precheck, build_candidates
from academic_timetabling_demo.db import connect, load_instance
from academic_timetabling_demo.domain import Instance, RunConfig
from academic_timetabling_demo.model import SolveResult, build_and_solve
from academic_timetabling_demo.report import (
    SessionRow,
    presentation,
    render_html,
    schedule_csv,
    session_rows,
)
from academic_timetabling_demo.validate import AuditReport, audit
from timetabling_milp import cpsat_model, pyomo_model
from timetabling_milp.compactness import count_windows, load_compactness
from timetabling_milp.instance import MilpRun, SolverOptions, load_data

SOLVER_LABELS = {
    "highs": "HiGHS: programación lineal entera, vía Pyomo",
    "cp-sat": "CP-SAT: programación por restricciones, OR-Tools",
}
MAX_TIME_LIMIT = 60
MAX_GAMMA = 100
MAX_SECTIONS = 300
MAX_WORKERS = 4


class SettingsError(ValueError):
    """Parámetros de la corrida fuera de rango. El mensaje se muestra al usuario."""


@dataclass(frozen=True)
class Settings:
    solver: str = "highs"
    time_limit: float = 20
    gamma: int = 0

    def __post_init__(self) -> None:
        if self.solver not in SOLVER_LABELS:
            raise SettingsError(f"Solver desconocido: {self.solver}.")
        if not (math.isfinite(self.time_limit) and 1 <= self.time_limit <= MAX_TIME_LIMIT):
            raise SettingsError(f"El límite de tiempo debe estar entre 1 y {MAX_TIME_LIMIT} s.")
        if not 0 <= self.gamma <= MAX_GAMMA:
            raise SettingsError(f"La penalización por ventana debe estar entre 0 y {MAX_GAMMA}.")


def explain(status: str, time_limit: float) -> str:
    """Qué significa cada estado, sin prometer más de lo que el solver demostró."""
    return {
        "OPTIMAL": (
            "Óptimo demostrado: no existe un horario de menor costo con estos datos y pesos."
        ),
        "FEASIBLE": (
            f"Mejor horario encontrado en {time_limit:g} s. El solver no alcanzó a demostrar "
            "que sea óptimo; la brecha acota cuánto podría mejorar."
        ),
        "INFEASIBLE": "El solver demostró que ningún horario cumple todas las restricciones duras.",
        "UNKNOWN": (
            f"Sin horario en {time_limit:g} s. No se sabe si la instancia es factible: "
            "pruebe con más tiempo o con el otro solver."
        ),
        "MODEL_INVALID": "El modelo construido no es válido: es un error del programa.",
        "NO_CANDIDATES": (
            "El prefiltro dejó secciones sin ninguna combinación de patrón y sala admisible, "
            "así que no se llamó al solver. Abajo se indica qué sección y por qué."
        ),
    }.get(status, status)


@dataclass(frozen=True)
class Outcome:
    run_id: str
    created_utc: str
    settings: Settings
    sections: int
    precheck: Precheck
    seconds: float
    result: SolveResult | None = None
    windows: int | None = None
    audit: AuditReport | None = None
    schedule_html: str | None = None
    schedule_csv: str | None = None

    @property
    def status(self) -> str:
        return self.result.status if self.result is not None else self.precheck.status

    @property
    def total(self) -> int | None:
        """Costo de las alternativas más gamma por ventana, recalculado desde el horario."""
        if self.result is None or self.result.objective is None:
            return None
        return self.result.objective + self.settings.gamma * (self.windows or 0)

    @property
    def bound(self) -> float | None:
        bound = self.result.best_bound if self.result is not None else None
        return bound if bound is not None and math.isfinite(bound) else None

    @property
    def gap(self) -> float | None:
        if self.total is None or self.bound is None or self.total <= 0:
            return None
        return max(0.0, (self.total - self.bound) / self.total)

    def record(self, source: str) -> dict[str, object]:
        """Registro plano, serializable a JSON: lo usan la vista y la tabla `runs`."""
        precheck = self.precheck
        return {
            "id": self.run_id,
            "created_at": self.created_utc,
            "source": source,
            "solver": self.settings.solver,
            "time_limit": self.settings.time_limit,
            "gamma": self.settings.gamma,
            "status": self.status,
            "sections": self.sections,
            "alternatives": precheck.alternatives_after,
            "objective": self.result.objective if self.result else None,
            "total": self.total,
            "best_bound": self.bound,
            "gap": self.gap,
            "windows": self.windows,
            "audit": self.audit.status if self.audit else None,
            "audit_findings": [f.as_dict() for f in self.audit.findings] if self.audit else [],
            "seconds": round(self.seconds, 3),
            "solve_seconds": round(self.result.solve_seconds, 3) if self.result else None,
            "precheck": {
                "status": precheck.status,
                "pairs_before": precheck.pairs_before,
                "alternatives_after": precheck.alternatives_after,
                "discards_by_rule": dict(precheck.discards_by_rule),
                "empty_sections": {k: list(v) for k, v in precheck.empty_sections.items()},
            },
            "schedule_html": self.schedule_html,
            "schedule_csv": self.schedule_csv,
        }


def solve(instance: Instance, settings: Settings) -> Outcome:
    if len(instance.sections) > MAX_SECTIONS:
        raise SettingsError(
            f"La instancia tiene {len(instance.sections)} secciones; esta demo admite hasta "
            f"{MAX_SECTIONS}."
        )
    started = time.perf_counter()
    run_id = uuid.uuid4().hex[:12]
    created = datetime.now(UTC).isoformat(timespec="seconds")
    workers = max(1, min(MAX_WORKERS, os.cpu_count() or 1))
    config = instance.config.model_copy(
        update={"time_limit_seconds": settings.time_limit, "num_search_workers": workers}
    )

    connection = connect(":memory:")
    try:
        load_instance(connection, instance)
        precheck = build_candidates(connection)
        run = _solve(connection, config, settings) if precheck.status == "READY" else None
    finally:
        connection.close()

    common = {
        "run_id": run_id,
        "created_utc": created,
        "settings": settings,
        "sections": len(instance.sections),
        "precheck": precheck,
    }
    if run is None:
        return Outcome(**common, seconds=time.perf_counter() - started)
    result = run.result
    rows = [(a.section_id, a.pattern_id, a.room_id) for a in result.assignments]
    if not rows:
        return Outcome(**common, seconds=time.perf_counter() - started, result=result)

    report = audit(instance, rows, result.objective, config)
    sessions = session_rows(instance, rows)
    return Outcome(
        **common,
        seconds=time.perf_counter() - started,
        result=result,
        windows=count_windows(instance, rows),
        audit=report,
        schedule_html=_grid(instance, sessions, config, run_id, created, settings, result, report),
        schedule_csv=schedule_csv(run_id, sessions).decode("utf-8"),
    )


def _solve(connection, config: RunConfig, settings: Settings) -> MilpRun:
    if settings.solver == "cp-sat" and settings.gamma == 0:
        # El modelo base con CP-SAT es exactamente el del MVP original.
        return MilpRun("cp-sat", build_and_solve(connection, config))
    data = load_data(connection, config)
    options = SolverOptions.from_config(config)
    compact = load_compactness(connection, settings.gamma) if settings.gamma else None
    if settings.solver == "cp-sat":
        return cpsat_model.solve(data, options, compact)
    return pyomo_model.solve(data, options, compact)


def _grid(
    instance: Instance,
    sessions: list[SessionRow],
    config: RunConfig,
    run_id: str,
    created: str,
    settings: Settings,
    result: SolveResult,
    report: AuditReport,
) -> str:
    """Grilla interactiva del MVP original, con el método y el estado de esta corrida."""
    metrics = {
        "run_id": run_id,
        "created_utc": created,
        "parameters": {"solver": settings.solver},
        "status": {
            "solver": result.status,
            "audit": report.status,
            # La grilla del original informa la verificación de su snapshot en disco; aquí la
            # entrada se valida al subirla y no hay snapshot.
            "integrity": "no aplica",
        },
        "costs": {
            "objective": result.objective,
            "cost_d": result.cost_d,
            "cost_e": result.cost_e,
        },
        "timings_seconds": {"solve": round(result.solve_seconds, 3)},
        "provenance": {},
        "counts": {
            "assigned_sections": len({row.section_id for row in sessions}),
            "sections": len(instance.sections),
        },
    }
    data = presentation(instance, sessions, config, metrics)
    data["run"]["method"] = SOLVER_LABELS[settings.solver]
    data["run"]["status"] = f"{result.status}: {explain(result.status, settings.time_limit)}"
    data["run"]["timing"] = {"label": "Tiempo de resolución", "seconds": result.solve_seconds}
    return render_html(data).decode("utf-8")

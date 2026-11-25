"""Rutas HTTP. La lógica está en `inputs` (datos), `pipeline` (resolución) y `store`."""

import base64
import hmac
import logging
import os
from pathlib import Path
from typing import Annotated

from academic_timetabling_demo.domain import CONFIG_FILE, TABLES, DataValidationError
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from timetabling_web.inputs import MAX_ZIP_BYTES, SAMPLES, UploadError, read_zip, sample_instance
from timetabling_web.inputs import sample_zip as build_sample_zip
from timetabling_web.pipeline import (
    MAX_GAMMA,
    MAX_SECTIONS,
    MAX_TIME_LIMIT,
    SOLVER_LABELS,
    Settings,
    SettingsError,
    explain,
    solve,
)
from timetabling_web.store import store_from_env

log = logging.getLogger("timetabling_web")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

app = FastAPI(title="Horarios universitarios", docs_url=None, redoc_url=None, openapi_url=None)
app.state.store = store_from_env()

RULE_LABELS = {
    "CAPACITY": "Capacidad de la sala menor que la matrícula",
    "FEATURES": "La sala no tiene el equipamiento exigido",
    "TEACHER_UNAVAILABLE": "Docente no disponible en alguna sesión",
    "ROOM_UNAVAILABLE": "Sala no disponible en alguna sesión",
    "ROOM_RESERVED": "Sala reservada en alguna sesión",
    "PROTECTED_SLOT": "Franja protegida para la sección",
}
TABLE_NOTES = {
    "weeks": "Semanas del horizonte, consecutivas desde 1.",
    "days": "Días con clases (1 = lunes).",
    "blocks": "Bloques del día, todos de igual duración y sin solaparse.",
    "teachers": "Docentes.",
    "rooms": "Salas y su capacidad.",
    "features": "Equipamientos, por ejemplo laboratorio.",
    "room_features": "Equipamiento de cada sala.",
    "groups": "Grupos de estudiantes que no pueden tener dos clases a la vez.",
    "sections": "Secciones: docente, matrícula, sesiones por semana y bloques por sesión.",
    "section_groups": "Grupos a los que pertenece cada sección.",
    "section_features": "Equipamiento que exige cada sección.",
    "section_weeks": "Semanas en que se dicta cada sección.",
    "patterns": "Patrones semanales posibles.",
    "pattern_sessions": "Sesiones de cada patrón: día, bloque inicial y duración.",
    "section_patterns": "Patrones autorizados para cada sección.",
    "teacher_available": "Disponibilidad de cada docente (lista positiva).",
    "room_available": "Disponibilidad de cada sala (lista positiva).",
    "room_reservations": "Reservas externas que bloquean una sala.",
    "section_blocked": "Franjas en que una sección no puede tener clase.",
    "teacher_penalties": "Costo de usar un bloque que el docente prefiere evitar.",
}


def _access_code() -> str:
    return os.environ.get("DEMO_ACCESS_CODE", "")


def _page(request: Request, name: str, status_code: int = 200, **context: object) -> HTMLResponse:
    context |= {"store_enabled": request.app.state.store.enabled}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _index(request: Request, status_code: int = 200, **context: object) -> HTMLResponse:
    defaults = {"source": "demo", "solver": "highs", "time_limit": 20, "gamma": 0}
    return _page(
        request,
        "index.html",
        status_code,
        samples=list(SAMPLES.values()),
        solvers=SOLVER_LABELS,
        max_time=MAX_TIME_LIMIT,
        max_gamma=MAX_GAMMA,
        max_sections=MAX_SECTIONS,
        max_zip_mb=MAX_ZIP_BYTES // 1024**2,
        access_required=bool(_access_code()),
        form=defaults | dict(context.pop("form", {})),
        **context,
    )


def _result(request: Request, record: dict[str, object], notes: tuple[str, ...] = ()):
    files = {}
    for key, media in (("schedule_csv", "text/csv"), ("schedule_html", "text/html")):
        text = record.get(key)
        if text:
            encoded = base64.b64encode(str(text).encode("utf-8")).decode("ascii")
            files[key] = f"data:{media};charset=utf-8;base64,{encoded}"
    return _page(
        request,
        "result.html",
        run=record,
        explanation=explain(str(record["status"]), float(record["time_limit"])),
        solver_label=SOLVER_LABELS.get(str(record["solver"]), record["solver"]),
        rule_labels=RULE_LABELS,
        files=files,
        notes=notes,
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return _index(request)


@app.get("/formato", response_class=HTMLResponse)
def data_format(request: Request) -> HTMLResponse:
    tables = [
        {"file": spec.file, "columns": spec.columns, "note": TABLE_NOTES.get(spec.name, "")}
        for spec in TABLES
    ]
    return _page(request, "format.html", tables=tables, config_file=CONFIG_FILE)


@app.get("/ejemplos/{name}.zip")
def sample_zip(name: str) -> Response:
    if name not in SAMPLES:
        raise HTTPException(status_code=404, detail="Ejemplo desconocido")
    return Response(
        build_sample_zip(name),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="timetabling-{name}.zip"'},
    )


@app.post("/resolver", response_class=HTMLResponse)
def resolve(
    request: Request,
    source: Annotated[str, Form()] = "demo",
    solver: Annotated[str, Form()] = "highs",
    time_limit: Annotated[float, Form()] = 20,
    gamma: Annotated[int, Form()] = 0,
    access_code: Annotated[str, Form()] = "",
    upload: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    form = {"source": source, "solver": solver, "time_limit": time_limit, "gamma": gamma}
    expected = _access_code()
    if expected and not hmac.compare_digest(access_code.encode(), expected.encode()):
        return _index(request, 403, form=form, error="Código de acceso incorrecto.")
    try:
        settings = Settings(solver=solver, time_limit=time_limit, gamma=gamma)
    except SettingsError as error:
        return _index(request, 400, form=form, error=str(error))

    notes: tuple[str, ...] = ()
    content: bytes | None = None
    if source == "upload":
        if upload is None or not upload.filename:
            return _index(request, 400, form=form, error="Elija un archivo ZIP para subir.")
        content = upload.file.read(MAX_ZIP_BYTES + 1)
        try:
            instance, notes = read_zip(content)
        except UploadError as error:
            return _index(request, 400, form=form, error=str(error))
        except DataValidationError as error:
            return _index(request, 422, form=form, issues=error.issues)
        label = f"archivo {upload.filename}"
    elif source in SAMPLES:
        instance = sample_instance(source)
        label = f"ejemplo {source}"
    else:
        return _index(request, 400, form=form, error="Origen de datos desconocido.")

    try:
        outcome = solve(instance, settings)
    except SettingsError as error:
        return _index(request, 400, form=form, error=str(error))
    record = outcome.record(label)

    store = request.app.state.store
    if store.enabled:
        try:
            store.save(record, content)
        except Exception:  # el horario ya está resuelto: no se pierde por no poder guardarlo
            log.exception("no se pudo guardar la corrida %s", outcome.run_id)
            notes += ("No se pudo guardar esta corrida en el historial.",)
    return _result(request, record, notes)


@app.get("/corridas", response_class=HTMLResponse)
def runs(request: Request) -> HTMLResponse:
    store = request.app.state.store
    if not store.enabled:
        raise HTTPException(status_code=404, detail="El historial no está activado")
    return _page(request, "runs.html", runs=store.list_runs())


@app.get("/corridas/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: str) -> HTMLResponse:
    store = request.app.state.store
    record = store.get_run(run_id) if store.enabled else None
    if record is None:
        raise HTTPException(status_code=404, detail="Corrida no encontrada")
    return _result(request, record)


@app.get("/api/health")
def health(request: Request) -> dict[str, object]:
    return {"status": "ok", "store": request.app.state.store.enabled}

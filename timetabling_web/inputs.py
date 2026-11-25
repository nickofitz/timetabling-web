"""Entrada de datos: ZIP subido por el usuario y ejemplos descargables.

El contrato es el del MVP original: un CSV por tabla fuente (20) más `config.json`. El ZIP
se lee en memoria con límites de tamaño; solo se aceptan nombres de archivo conocidos y se
descarta cualquier ruta interna, así que nada se escribe fuera de una carpeta temporal.
"""

import io
import json
import tempfile
import zipfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath

from academic_timetabling_demo.domain import (
    CONFIG_FILE,
    DEFAULT_CONFIG,
    TABLES,
    Instance,
    read_instance,
)
from academic_timetabling_demo.synthetic import GeneratedCase, generate_case, source_files
from timetabling_milp.generator import PRESETS, generate

MAX_ZIP_BYTES = 2 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
EXPECTED_FILES = frozenset({spec.file for spec in TABLES} | {CONFIG_FILE})


class UploadError(ValueError):
    """El archivo subido no es un ZIP aceptable. El mensaje se muestra al usuario."""


@dataclass(frozen=True)
class Sample:
    name: str
    sections: int
    seed: int
    description: str


SAMPLES = {
    sample.name: sample
    for sample in (
        Sample("micro", 3, 0, "Caso mínimo: se puede resolver a mano."),
        Sample("demo", 12, 42, "Salas holgadas: ambos solvers llegan al óptimo en milisegundos."),
        Sample("small", 30, 1, "Salas al 70 % de ocupación."),
        Sample("medium", 80, 1, "Salas al 80 %: aquí los solvers empiezan a separarse."),
        Sample(
            "large", 200, 1, "Salas al 82 %: en las pruebas, ningún solver cerró la brecha en 20 s."
        ),
    )
}


@cache
def _generated(name: str) -> GeneratedCase:
    sample = SAMPLES[name]
    if name in PRESETS:
        return generate(name, PRESETS[name], sample.seed)
    return generate_case(name, sample.seed)


def sample_instance(name: str) -> Instance:
    """Instancia de un ejemplo. KeyError si el nombre no existe."""
    return _generated(name).instance


def sample_zip(name: str) -> bytes:
    """ZIP del ejemplo con el mismo formato que se sube: sirve de plantilla para editar."""
    files = source_files(_generated(name))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in sorted(files):
            info = zipfile.ZipInfo(
                f"timetabling-{name}/{filename}", date_time=(1980, 1, 1, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[filename])
    return buffer.getvalue()


def read_zip(content: bytes) -> tuple[Instance, tuple[str, ...]]:
    """Valida y lee un ZIP subido. Devuelve la instancia y avisos para el usuario.

    Lanza UploadError si el archivo no es un ZIP aceptable y DataValidationError (del MVP
    original, con archivo, fila y campo de cada problema) si los datos no cumplen el formato.
    """
    if not content:
        raise UploadError("El archivo está vacío.")
    if len(content) > MAX_ZIP_BYTES:
        raise UploadError(f"El ZIP supera {MAX_ZIP_BYTES // 1024**2} MB.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise UploadError("El archivo no es un ZIP válido.") from error

    files: dict[str, bytes] = {}
    total = 0
    with archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if info.is_dir() or "__MACOSX" in path.parts or path.name not in EXPECTED_FILES:
                continue
            if path.name in files:
                raise UploadError(f"{path.name} aparece más de una vez en el ZIP.")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise UploadError(
                    f"El contenido descomprimido supera {MAX_UNCOMPRESSED_BYTES // 1024**2} MB."
                )
            try:
                files[path.name] = archive.read(info)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
                raise UploadError(f"No se pudo leer {path.name} dentro del ZIP.") from error
    if not files:
        raise UploadError(
            "El ZIP no contiene ningún archivo con los nombres esperados (ver Formato de datos)."
        )

    notes: list[str] = []
    if CONFIG_FILE not in files:
        files[CONFIG_FILE] = json.dumps(DEFAULT_CONFIG.model_dump()).encode("utf-8")
        notes.append(
            f"El ZIP no traía {CONFIG_FILE}: se usaron los pesos por defecto "
            f"(alpha = {DEFAULT_CONFIG.alpha}, beta = {DEFAULT_CONFIG.beta})."
        )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, data in files.items():
            (root / name).write_bytes(data)
        instance = read_instance(root)
    return instance, tuple(notes)

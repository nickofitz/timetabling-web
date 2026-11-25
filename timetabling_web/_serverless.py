"""Ajustes al entorno de las funciones de Vercel (AWS Lambda). Se aplican antes de importar
Pyomo y el MVP original; cada uno se activa solo si el entorno lo necesita.

1. SQLite. El prefiltro del original ordena dentro de json_group_array, lo que exige SQLite
   3.44 o posterior (el original lo comprueba en runner.check_sqlite). El Python de Vercel
   trae una versión anterior y la consulta fallaba con «near "ORDER": syntax error». Si el
   SQLite del sistema no lo admite, se usa el de pysqlite3-binary en su lugar.

2. multiprocessing.Lock. Lambda no tiene /dev/shm y crear el lock falla con FileNotFoundError.
   Pyomo crea uno al importarse multiprocessing y solo tolera PermissionError, así que la
   función moría al arrancar. Esta app no lanza subprocesos: si el sistema no admite el lock
   de procesos, basta uno de hilos, que es lo que Pyomo conserva cuando su excepción coincide.
"""

import multiprocessing
import sys
import threading
from types import ModuleType

ORDERED_AGGREGATE = "SELECT json_group_array(x ORDER BY x) FROM (SELECT 2 AS x UNION SELECT 1)"


def supports_ordered_aggregates(module: ModuleType) -> bool:
    try:
        connection = module.connect(":memory:")
    except Exception:
        return False
    try:
        return connection.execute(ORDERED_AGGREGATE).fetchone() == ("[1,2]",)
    except Exception:
        return False
    finally:
        connection.close()


def pick_sqlite(stdlib: ModuleType, alternative: ModuleType | None) -> ModuleType:
    """El SQLite de la biblioteca estándar si basta; si no, la alternativa si existe y basta."""
    if supports_ordered_aggregates(stdlib):
        return stdlib
    if alternative is not None and supports_ordered_aggregates(alternative):
        return alternative
    return stdlib  # el prefiltro fallará con el mensaje de SQLite: no hay nada mejor


def use_capable_sqlite() -> ModuleType:
    import sqlite3

    try:
        import pysqlite3
    except ImportError:
        pysqlite3 = None
    chosen = pick_sqlite(sqlite3, pysqlite3)
    sys.modules["sqlite3"] = chosen
    return chosen


_process_lock = multiprocessing.Lock


def _lock_or_thread_lock():
    try:
        return _process_lock()
    except OSError:
        return threading.Lock()


def tolerate_missing_shared_memory() -> None:
    multiprocessing.Lock = _lock_or_thread_lock


def apply() -> None:
    use_capable_sqlite()
    tolerate_missing_shared_memory()

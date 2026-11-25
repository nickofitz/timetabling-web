"""Ajustes al entorno de Vercel (AWS Lambda): SQLite antiguo y sin /dev/shm.

Las simulaciones que alteran módulos del intérprete corren en un proceso aparte para no
contaminar las demás pruebas.
"""

import sqlite3
import subprocess
import sys
import textwrap

import pytest

from timetabling_web import _serverless

NO_SHARED_MEMORY = """
import _multiprocessing

class NoSharedMemory(_multiprocessing.SemLock):
    def __new__(cls, *args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

_multiprocessing.SemLock = NoSharedMemory  # lo que ocurre en AWS Lambda
"""


def run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=".",
    )


class OldSqlite:
    """SQLite anterior a 3.44: rechaza ORDER BY dentro de un agregado."""

    @staticmethod
    def connect(path):
        connection = sqlite3.connect(path)

        class Connection:
            def execute(self, sql, *args):
                if "ORDER BY x)" in sql:
                    raise sqlite3.OperationalError('near "ORDER": syntax error')
                return connection.execute(sql, *args)

            def close(self):
                connection.close()

        return Connection()


def test_the_stdlib_sqlite_is_kept_when_it_is_recent_enough():
    assert _serverless.pick_sqlite(sqlite3, OldSqlite) is sqlite3


def test_an_old_stdlib_sqlite_is_replaced_by_a_capable_alternative():
    assert _serverless.pick_sqlite(OldSqlite, sqlite3) is sqlite3
    assert _serverless.pick_sqlite(OldSqlite, None) is OldSqlite


def test_the_sqlite_in_use_supports_the_original_prefilter():
    import sqlite3 as in_use

    assert _serverless.supports_ordered_aggregates(in_use)


@pytest.mark.skipif(sys.platform != "linux", reason="pysqlite3-binary solo existe para Linux")
def test_the_whole_pipeline_runs_on_the_bundled_sqlite():
    result = run(
        """
        import sys
        import pysqlite3
        sys.modules["sqlite3"] = pysqlite3  # como si el SQLite del sistema fuera antiguo
        from timetabling_web.inputs import sample_instance
        from timetabling_web.pipeline import Settings, solve
        import academic_timetabling_demo.db as db
        assert db.sqlite3 is pysqlite3
        for solver in ("highs", "cp-sat"):
            outcome = solve(sample_instance("demo"), Settings(solver=solver, gamma=5))
            assert (outcome.status, outcome.total) == ("OPTIMAL", 17), outcome.status
        print("ok")
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_the_app_imports_and_solves_without_dev_shm():
    result = run(
        NO_SHARED_MEMORY
        + """
import app  # mismo punto de entrada que usa Vercel
import multiprocessing  # el runtime de Vercel lo importa después de la app
from timetabling_web.inputs import sample_instance
from timetabling_web.pipeline import Settings, solve

outcome = solve(sample_instance("micro"), Settings(solver="highs"))
assert outcome.status == "OPTIMAL", outcome.status
print("ok")
"""
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("ok")


def test_without_the_patch_pyomo_would_crash_in_that_environment():
    result = run(NO_SHARED_MEMORY + "import pyomo.environ\nimport multiprocessing\n")

    assert result.returncode != 0 and "FileNotFoundError" in result.stderr

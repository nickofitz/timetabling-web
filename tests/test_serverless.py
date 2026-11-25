"""La app debe arrancar donde multiprocessing.Lock falla, como en las funciones de Vercel.

Se simula AWS Lambda haciendo que crear un semáforo POSIX falle con FileNotFoundError, el
error que registró Vercel. Cada caso corre en un proceso aparte para no contaminar los demás.
"""

import subprocess
import sys
import textwrap


def test_the_app_imports_and_solves_without_dev_shm():
    script = textwrap.dedent(
        """
        import _multiprocessing

        class NoSharedMemory(_multiprocessing.SemLock):
            def __new__(cls, *args, **kwargs):
                raise FileNotFoundError(2, "No such file or directory")

        _multiprocessing.SemLock = NoSharedMemory  # lo que ocurre en AWS Lambda

        import app  # mismo punto de entrada que usa Vercel
        import multiprocessing  # el runtime de Vercel lo importa después de la app
        from timetabling_web.inputs import sample_instance
        from timetabling_web.pipeline import Settings, solve

        outcome = solve(sample_instance("micro"), Settings(solver="highs"))
        assert outcome.status == "OPTIMAL", outcome.status
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, cwd="."
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("ok")


def test_without_the_patch_pyomo_would_crash_in_that_environment():
    script = textwrap.dedent(
        """
        import _multiprocessing

        class NoSharedMemory(_multiprocessing.SemLock):
            def __new__(cls, *args, **kwargs):
                raise FileNotFoundError(2, "No such file or directory")

        _multiprocessing.SemLock = NoSharedMemory
        import pyomo.environ  # noqa: F401
        import multiprocessing  # noqa: F401
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    assert result.returncode != 0 and "FileNotFoundError" in result.stderr

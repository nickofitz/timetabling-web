"""Aplicación web de programación de horarios universitarios."""

import multiprocessing
import threading

# Vercel ejecuta las funciones Python sobre AWS Lambda, que no tiene /dev/shm: crear un
# multiprocessing.Lock falla con FileNotFoundError. Pyomo crea uno en cuanto se importa
# multiprocessing y solo tolera PermissionError (pyomo/common/dependencies.py,
# _finalize_multiprocessing), así que la función moría al arrancar. Esta app no lanza
# subprocesos: si el sistema no admite el lock de procesos, basta uno de hilos, que es
# lo mismo que Pyomo conserva cuando su propia excepción sí coincide. Debe ejecutarse antes
# de importar Pyomo, y por eso vive en el __init__ del paquete.
_process_lock = multiprocessing.Lock


def _lock_or_thread_lock():
    try:
        return _process_lock()
    except OSError:
        return threading.Lock()


multiprocessing.Lock = _lock_or_thread_lock

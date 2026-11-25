"""Aplicación web de programación de horarios universitarios."""

from timetabling_web import _serverless

# Debe ocurrir antes de importar Pyomo o el MVP original: ver _serverless.
_serverless.apply()

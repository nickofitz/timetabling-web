"""Punto de entrada que Vercel detecta: expone la aplicación FastAPI como `app`."""

from timetabling_web.main import app

__all__ = ["app"]

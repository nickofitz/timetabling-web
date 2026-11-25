"""Historial de corridas. Opcional: sin variables de entorno la app funciona sin guardar nada.

Con `SUPABASE_URL` y `SUPABASE_SECRET_KEY` (clave `sb_secret_...`, solo en el servidor),
cada corrida se guarda en la tabla `runs` por la Data API y el ZIP subido en el bucket
privado `instances`. La tabla tiene RLS activado y ninguna política: solo la clave secreta,
que actúa con el rol `service_role`, puede leerla o escribirla.
"""

import os
import re
from typing import Protocol

import httpx

RUN_ID = re.compile(r"^[0-9a-f]{12}$")
LIST_COLUMNS = (
    "id,created_at,source,solver,time_limit,gamma,status,sections,total,gap,windows,audit,seconds"
)


class StoreError(RuntimeError):
    """El almacenamiento no respondió como se esperaba."""


class RunStore(Protocol):
    enabled: bool

    def save(self, record: dict[str, object], upload: bytes | None) -> None: ...

    def list_runs(self, limit: int = 50) -> list[dict[str, object]]: ...

    def get_run(self, run_id: str) -> dict[str, object] | None: ...


class NullStore:
    """Sin persistencia: el despliegue mínimo, y el que usan las pruebas por defecto."""

    enabled = False

    def save(self, record: dict[str, object], upload: bytes | None) -> None:
        return None

    def list_runs(self, limit: int = 50) -> list[dict[str, object]]:
        return []

    def get_run(self, run_id: str) -> dict[str, object] | None:
        return None


class SupabaseStore:
    enabled = True

    def __init__(self, url: str, secret_key: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=10)
        self._base = url.rstrip("/")
        # Las claves sb_secret_ no son JWT: van en `apikey`. Repetirlas en Authorization se
        # admite solo si coinciden con `apikey`, y así también funciona una clave antigua.
        self._headers = {"apikey": secret_key, "Authorization": f"Bearer {secret_key}"}

    def save(self, record: dict[str, object], upload: bytes | None) -> None:
        if upload is not None:
            self._check(
                self._client.post(
                    f"{self._base}/storage/v1/object/instances/{record['id']}.zip",
                    content=upload,
                    headers={**self._headers, "Content-Type": "application/zip"},
                )
            )
        self._check(
            self._client.post(
                f"{self._base}/rest/v1/runs",
                json=record,
                headers={**self._headers, "Prefer": "return=minimal"},
            )
        )

    def list_runs(self, limit: int = 50) -> list[dict[str, object]]:
        response = self._client.get(
            f"{self._base}/rest/v1/runs",
            params={"select": LIST_COLUMNS, "order": "created_at.desc", "limit": str(limit)},
            headers=self._headers,
        )
        return self._check(response).json()

    def get_run(self, run_id: str) -> dict[str, object] | None:
        if not RUN_ID.fullmatch(run_id):
            return None
        response = self._client.get(
            f"{self._base}/rest/v1/runs",
            params={"select": "*", "id": f"eq.{run_id}"},
            headers=self._headers,
        )
        rows = self._check(response).json()
        return rows[0] if rows else None

    @staticmethod
    def _check(response: httpx.Response) -> httpx.Response:
        if response.is_error:
            raise StoreError(f"Supabase respondió {response.status_code}: {response.text[:200]}")
        return response


def store_from_env() -> RunStore:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    return SupabaseStore(url, key) if url and key else NullStore()

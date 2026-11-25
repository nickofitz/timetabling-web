import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from timetabling_web.main import app
from timetabling_web.store import NullStore


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    app.state.store = NullStore()
    return TestClient(app)


def rezip(files: dict[str, bytes], prefix: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(prefix + name, data)
    return buffer.getvalue()


def unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {info.filename.rsplit("/", 1)[-1]: archive.read(info) for info in archive.infolist()}

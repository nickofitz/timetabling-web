from tests.conftest import rezip, unzip
from timetabling_web.inputs import sample_zip
from timetabling_web.main import app


def solve_form(**overrides):
    return {"source": "demo", "solver": "highs", "time_limit": "20", "gamma": "0", **overrides}


def test_home_offers_every_sample_and_both_solvers(client):
    page = client.get("/")

    assert page.status_code == 200
    for text in ("Ejemplo medium", "HiGHS", "CP-SAT", "/ejemplos/large.zip"):
        assert text in page.text
    assert "Corridas" not in page.text  # sin almacenamiento no se ofrece el historial


def test_format_page_lists_every_file(client):
    page = client.get("/formato")

    assert page.status_code == 200 and "teacher_available.csv" in page.text


def test_sample_download_is_a_zip(client):
    response = client.get("/ejemplos/small.zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "sections.csv" in unzip(response.content)
    assert client.get("/ejemplos/nada.zip").status_code == 404


def test_solving_a_sample_shows_status_grid_and_downloads(client):
    page = client.post("/resolver", data=solve_form())

    assert page.status_code == 200
    for text in ("OPTIMAL", "Óptimo demostrado", "PASSED", "srcdoc=", "data:text/csv"):
        assert text in page.text


def test_uploading_a_downloaded_sample_gives_the_same_result(client):
    page = client.post(
        "/resolver",
        data=solve_form(source="upload", solver="cp-sat"),
        files={"upload": ("mis-datos.zip", sample_zip("demo"), "application/zip")},
    )

    assert page.status_code == 200
    assert "archivo mis-datos.zip" in page.text and "OPTIMAL" in page.text


def test_bad_inputs_return_the_form_with_an_explanation(client):
    files = unzip(sample_zip("micro"))
    files["rooms.csv"] = files["rooms.csv"].replace(b",", b";", 1)

    no_file = client.post("/resolver", data=solve_form(source="upload"))
    not_zip = client.post(
        "/resolver", data=solve_form(source="upload"), files={"upload": ("x.zip", b"no", "a/b")}
    )
    broken = client.post(
        "/resolver",
        data=solve_form(source="upload"),
        files={"upload": ("x.zip", rezip(files), "application/zip")},
    )
    out_of_range = client.post("/resolver", data=solve_form(time_limit="600"))

    assert no_file.status_code == 400 and "Elija un archivo" in no_file.text
    assert not_zip.status_code == 400 and "no es un ZIP" in not_zip.text
    assert broken.status_code == 422 and "rooms.csv" in broken.text
    assert out_of_range.status_code == 400 and "entre 1 y 60" in out_of_range.text


def test_access_code_is_enforced_only_when_configured(client, monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "uai-2026")

    assert "Código de acceso" in client.get("/").text
    assert client.post("/resolver", data=solve_form(source="micro")).status_code == 403
    allowed = client.post("/resolver", data=solve_form(source="micro", access_code="uai-2026"))
    assert allowed.status_code == 200


def test_history_routes_do_not_exist_without_a_store(client):
    assert client.get("/corridas").status_code == 404
    assert client.get("/corridas/0123456789ab").status_code == 404
    assert client.get("/api/health").json() == {"status": "ok", "store": False}


class MemoryStore:
    enabled = True

    def __init__(self, fail: bool = False):
        self.records, self.uploads, self.fail = {}, {}, fail

    def save(self, record, upload):
        if self.fail:
            raise RuntimeError("caído")
        self.records[record["id"]] = record
        self.uploads[record["id"]] = upload

    def list_runs(self, limit=50):
        return list(self.records.values())[:limit]

    def get_run(self, run_id):
        return self.records.get(run_id)


def test_runs_are_saved_listed_and_reopened_when_a_store_is_configured(client):
    store = app.state.store = MemoryStore()

    client.post("/resolver", data=solve_form(source="micro"))
    client.post(
        "/resolver",
        data=solve_form(source="upload"),
        files={"upload": ("d.zip", sample_zip("demo"), "application/zip")},
    )

    run_id, upload_id = list(store.records)
    assert store.uploads[run_id] is None  # los ejemplos se regeneran: no se guarda su ZIP
    assert store.uploads[upload_id] == sample_zip("demo")
    listing = client.get("/corridas")
    assert listing.status_code == 200 and run_id in listing.text
    detail = client.get(f"/corridas/{run_id}")
    assert detail.status_code == 200 and "srcdoc=" in detail.text


def test_a_failing_store_does_not_lose_the_timetable(client):
    app.state.store = MemoryStore(fail=True)

    page = client.post("/resolver", data=solve_form(source="micro"))

    assert page.status_code == 200
    assert "OPTIMAL" in page.text and "No se pudo guardar" in page.text

import json

import httpx

from timetabling_web.store import NullStore, SupabaseStore, store_from_env


def store_with(handler):
    return SupabaseStore(
        "https://abc.supabase.co/",
        "sb_secret_x",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_save_uploads_the_zip_then_inserts_the_run_with_the_secret_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201)

    store_with(handler).save({"id": "0123456789ab", "status": "OPTIMAL"}, b"PK")

    upload, insert = seen
    assert upload.url.path == "/storage/v1/object/instances/0123456789ab.zip"
    assert upload.content == b"PK"
    assert insert.url.path == "/rest/v1/runs"
    assert json.loads(insert.content) == {"id": "0123456789ab", "status": "OPTIMAL"}
    for request in seen:
        assert request.headers["apikey"] == "sb_secret_x"
        assert request.headers["authorization"] == "Bearer sb_secret_x"


def test_runs_without_an_upload_skip_storage():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(201)

    store_with(handler).save({"id": "0123456789ab"}, None)

    assert paths == ["/rest/v1/runs"]


def test_list_and_get_query_the_data_api():
    def handler(request):
        params = dict(request.url.params)
        if params.get("id") == "eq.0123456789ab":
            return httpx.Response(200, json=[{"id": "0123456789ab"}])
        assert params["order"] == "created_at.desc"
        return httpx.Response(200, json=[{"id": "a"}, {"id": "b"}])

    store = store_with(handler)

    assert [run["id"] for run in store.list_runs()] == ["a", "b"]
    assert store.get_run("0123456789ab") == {"id": "0123456789ab"}


def test_malformed_ids_never_reach_the_database():
    def handler(request):
        raise AssertionError("no debió consultar")

    assert store_with(handler).get_run("1 or 1=1") is None


def test_errors_from_supabase_are_raised():
    store = store_with(lambda request: httpx.Response(401, text="no"))

    try:
        store.list_runs()
    except RuntimeError as error:
        assert "401" in str(error)
    else:
        raise AssertionError("debió fallar")


def test_the_store_is_chosen_from_the_environment(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    assert isinstance(store_from_env(), NullStore)

    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_x")
    assert store_from_env().enabled

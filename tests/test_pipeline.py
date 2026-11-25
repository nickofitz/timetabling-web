import pytest

from timetabling_web.inputs import sample_instance
from timetabling_web.pipeline import Settings, SettingsError, solve


@pytest.mark.parametrize("solver", ["highs", "cp-sat"])
def test_both_solvers_prove_the_known_optimum_of_demo(solver):
    outcome = solve(sample_instance("demo"), Settings(solver=solver, time_limit=20))

    assert outcome.status == "OPTIMAL"
    assert outcome.total == 17
    assert outcome.gap == 0
    assert outcome.audit.passed
    assert "<html" in outcome.schedule_html.lower()
    assert outcome.schedule_csv.splitlines()[0].startswith("run_id")


@pytest.mark.parametrize("solver", ["highs", "cp-sat"])
def test_compactness_removes_every_window_of_demo_at_the_same_cost(solver):
    outcome = solve(sample_instance("demo"), Settings(solver=solver, time_limit=20, gamma=5))

    assert outcome.status == "OPTIMAL"
    assert (outcome.result.objective, outcome.windows, outcome.total) == (17, 0, 17)


def test_a_section_bigger_than_every_room_stops_before_the_solver():
    instance = sample_instance("micro")
    big = max(room.capacity for room in instance.rooms) + 1
    first, *rest = instance.sections
    broken = instance.model_copy(
        update={"sections": (first.model_copy(update={"enrollment": big}), *rest)}
    )

    outcome = solve(broken, Settings())

    assert outcome.status == "NO_CANDIDATES"
    assert outcome.result is None
    assert "CAPACITY" in outcome.precheck.empty_sections[first.id]
    assert outcome.record("x")["precheck"]["empty_sections"][first.id]


def test_the_record_is_plain_json():
    import json

    outcome = solve(sample_instance("micro"), Settings())

    record = json.loads(json.dumps(outcome.record("ejemplo micro")))
    assert record["status"] == "OPTIMAL" and record["audit"] == "PASSED"


@pytest.mark.parametrize(
    "kwargs",
    [{"solver": "gurobi"}, {"time_limit": 0}, {"time_limit": 61}, {"gamma": -1}, {"gamma": 101}],
)
def test_settings_out_of_range_are_rejected(kwargs):
    with pytest.raises(SettingsError):
        Settings(**kwargs)

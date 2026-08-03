import json
from pathlib import Path


def test_v020_published_report_is_complete_and_unembellished():
    root = Path(__file__).parents[1]
    data = json.loads((root / "reports" / "v0.2.0" / "results.json").read_text())
    runs = data["runs"]
    task_ids = {path.stem for path in (root / "tasks").glob("*.yaml")}

    assert data["totals"]["runs"] == data["totals"]["passed"] == 20
    assert data["totals"]["failed"] == 0
    assert data["comparison_included"] is False
    assert data["trend"] is None
    assert {run["task_id"] for run in runs} == task_ids
    assert len(runs) == len(task_ids) == 20
    assert all(run["usage"] is None for run in runs)
    assert all(run["gate"]["passed"] and not run["gate"]["findings"] for run in runs)
    assert all("[trusted-unrestricted]" in run["agent"]["label"] for run in runs)

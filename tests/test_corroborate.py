"""Checking the declaration against systems trellis does not own.

A corroborator is handed a snapshot and returns findings. It cannot set
anything — across a process boundary that is structural rather than a promise.
"""

import json
import sys
from pathlib import Path

import pytest

from trellis import cli, corroborate

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "agent-loop" / "graph"


@pytest.fixture
def project(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: a\n    title: A thing\n    status: in_progress\n    ref: TRE-3\n"
        "  - id: b\n    title: Another\n    status: not_started\n"
        "    gates: {start: a.done}\n"
    )
    return graph_dir


def declare(graph_dir: Path, name: str, script: str) -> Path:
    path = graph_dir.parent / f"{name}.py"
    path.write_text(script)
    (graph_dir.parent / "trellis.toml").write_text(
        f'[corroborator.{name}]\ncommand = ["{sys.executable}", "{path}"]\n'
    )
    return path


# -- the contract ------------------------------------------------------------


def test_a_corroborator_is_handed_the_snapshot(project):
    declare(
        project,
        "peek",
        "import json,sys\n"
        "s=json.load(sys.stdin)\n"
        "print(json.dumps([{'node': 'a', 'message': str(sorted(s.keys()))}]))\n",
    )
    found = corroborate.gather(project, {"nodes": {}, "refs": {}})
    assert "nodes" in found[0].message


def test_it_joins_on_ref(project):
    """What `ref:` was for: the corroborator addresses the same item you do."""
    declare(
        project,
        "linear",
        "import json,sys\n"
        "s=json.load(sys.stdin)\n"
        "print(json.dumps([\n"
        "  {'node': n, 'code': 'state_disagrees', 'severity': 'warn',\n"
        "   'message': f'{r} is Done in the tracker'}\n"
        "  for n, r in s['refs'].items() if r == 'TRE-3'\n"
        "]))\n",
    )
    from trellis.engine import Engine
    from trellis.loader import load_graph
    from trellis.snapshot import capture

    payload = capture(project, Engine(load_graph(project)))
    found = corroborate.gather(project, payload)
    assert len(found) == 1
    assert found[0].node == "a"
    assert found[0].code == "linear:state_disagrees"


def test_codes_are_namespaced(project):
    """So a reader sees the source, and nothing can impersonate the kernel."""
    declare(
        project,
        "linear",
        "import json\nprint(json.dumps([{'node': 'a', 'code': 'cycle'}]))\n",
    )
    found = corroborate.gather(project, {})
    assert found[0].code == "linear:cycle"


def test_a_finding_about_no_one_node_is_labelled_graph_level(project):
    """A count across the tree is a real finding, not a finding missing a node."""
    declare(
        project,
        "linear",
        "import json,sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps([{'code': 'unmodelled', 'severity': 'warn',\n"
        "  'message': '33 ticket(s) in the tables have no node'}]))\n",
    )
    found = corroborate.gather(project, {})
    assert found[0].node == corroborate.GRAPH_LEVEL == "(graph)"


def test_doctor_renders_a_graph_level_finding_without_an_empty_slot(project, capsys):
    declare(
        project,
        "linear",
        "import json,sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps([{'code': 'unmodelled', 'severity': 'warn',\n"
        "  'message': '33 ticket(s) in the tables have no node'}]))\n",
    )
    cli.main(["--graph", str(project), "doctor"])
    out = capsys.readouterr().out
    assert "  ? (graph): 33 ticket(s) in the tables have no node" in out
    assert "  ? : " not in out


def test_a_corroborator_may_not_claim_an_error(project):
    """`error` means the graph cannot evaluate. Only the kernel establishes that."""
    declare(
        project,
        "linear",
        "import json\n"
        "print(json.dumps([{'node': 'a', 'severity': 'error', 'message': 'x'}]))\n",
    )
    found = corroborate.gather(project, {})
    assert len(found) == 1
    assert found[0].severity == "warn"
    assert found[0].code == "linear:did_not_run"
    assert "never error" in found[0].message


def test_the_clamp_does_not_double_its_full_stop(project):
    """The detail is another diagnostic here, and it ends in a full stop of its
    own: "only the kernel can establish.. This is silence, not agreement"."""
    declare(
        project,
        "linear",
        "import json\n"
        "print(json.dumps([{'node': 'a', 'severity': 'error', 'message': 'x'}]))\n",
    )
    found = corroborate.gather(project, {})
    assert "only the kernel can establish. This is silence" in found[0].message


# -- failing is a finding, never a silence -----------------------------------


def test_a_missing_program_is_reported_not_swallowed(project):
    (project.parent / "trellis.toml").write_text(
        '[corroborator.linear]\ncommand = ["definitely-not-a-real-program"]\n'
    )
    found = corroborate.gather(project, {})
    assert found[0].severity == "warn"
    assert "silence, not agreement" in found[0].message


def test_a_crashing_corroborator_reports_its_stderr(project):
    declare(project, "linear", "import sys\nsys.stderr.write('boom')\nsys.exit(2)\n")
    found = corroborate.gather(project, {})
    assert "exited 2: boom" in found[0].message


def test_a_timeout_is_reported(project):
    declare(project, "slow", "import time\ntime.sleep(5)\n")
    found = corroborate.gather(project, {}, timeout=1)
    assert "timed out" in found[0].message


def test_garbage_output_is_reported(project):
    declare(project, "linear", "print('not json at all')\n")
    found = corroborate.gather(project, {})
    assert found[0].code == "linear:did_not_run"
    assert "not JSON" in found[0].message


def test_no_output_is_no_findings_not_a_failure(project):
    declare(project, "linear", "pass\n")
    assert corroborate.gather(project, {}) == []


# -- configuration -----------------------------------------------------------


def test_no_config_means_no_corroborators(project):
    assert corroborate.load(project) == {}
    assert corroborate.gather(project, {}) == []


def test_a_corroborator_without_a_command_is_rejected(project):
    (project.parent / "trellis.toml").write_text(
        '[corroborator.broken]\ndescription = "nope"\n'
    )
    with pytest.raises(corroborate.CorroboratorError, match="needs command"):
        corroborate.load(project)


def test_corroborators_run_in_a_stable_order(project):
    (project.parent / "trellis.toml").write_text(
        f'[corroborator.zebra]\ncommand = ["{sys.executable}", "-c", '
        "\"import json;print(json.dumps([{'node':'a'}]))\"]\n"
        f'[corroborator.alpha]\ncommand = ["{sys.executable}", "-c", '
        "\"import json;print(json.dumps([{'node':'b'}]))\"]\n"
    )
    found = corroborate.gather(project, {})
    assert [f.source for f in found] == ["alpha", "zebra"]


# -- through doctor ----------------------------------------------------------


def test_doctor_includes_corroborator_findings(project, capsys):
    declare(
        project,
        "linear",
        "import json,sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps([{'node': 'a', 'code': 'state_disagrees',\n"
        "  'severity': 'warn', 'message': 'TRE-3 is Done in the tracker'}]))\n",
    )
    assert cli.main(["--graph", str(project), "doctor"]) == 0
    assert "TRE-3 is Done in the tracker" in capsys.readouterr().out


def test_a_corroborator_warning_outranks_a_kernel_info(project, capsys):
    """External findings arrive after the ranked set, so the list is re-sorted."""
    declare(
        project,
        "linear",
        "import json,sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps([{'node': 'a', 'severity': 'warn',\n"
        "  'message': 'the tracker disagrees'}]))\n",
    )
    cli.main(["--graph", str(project), "doctor"])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("  ")]
    assert "the tracker disagrees" in lines[0], "warn must sort above info"


def test_doctor_json_carries_them(project, capsys):
    declare(
        project,
        "linear",
        "import json,sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps([{'node': 'a', 'severity': 'warn', 'message': 'x'}]))\n",
    )
    cli.main(["--graph", str(project), "--json", "doctor"])
    payload = json.loads(capsys.readouterr().out)
    assert any(f["message"] == "x" for f in payload)

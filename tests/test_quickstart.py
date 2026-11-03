import copy
import json
from pathlib import Path
import types

import pytest

from virtuoso_lan_bridge import quickstart as q
from virtuoso_lan_bridge.common import read_json, write_json
from virtuoso_lan_bridge.controller import Controller


@pytest.fixture
def world(tmp_path, monkeypatch):
    control = Controller(tmp_path / "controller")
    nodes = [dict(name=name, host=host, user="cadence", port=22, enabled=True, slots=1,
                  threads=1, python="python3", spectre="spectre", root="/tmp/vlb-test", bridge_port=65387)
             for name, host in (("a", "192.0.2.1"), ("b", "192.0.2.2"))]
    write_json(control.pool_path, dict(schema_version=1, max_parallel=2, nodes=nodes))
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck/input.scs").write_text("simulator lang=spectre\n")
    write_json(tmp_path / "jobs.json", {"jobs": [dict(name="j1", bundle="deck"), dict(name="j2", bundle="deck")]})
    profile = tmp_path / "profile.json"
    write_json(profile, dict(schema_version=1, controller_state="controller", jobs="jobs.json",
                            results_root="runs", required_nodes=["a", "b"]))
    calls = []
    def check(self, name):
        calls.append(("check", name))
        return dict(node=name, endpoint=name, ssh="host\ncadence", spectre={"spectre": "spectre", "version_returncode": 0},
                    bridge={"output": "3"})
    monkeypatch.setattr(Controller, "check", check)
    monkeypatch.setattr(q.ssh, "command", lambda *a, **kw: types.SimpleNamespace(stdout=b"bash\nsshd\n"))
    def run(self, jobs, batch, *, expected_config):
        calls.append(("run", str(batch)))
        assert expected_config == q.runner.load_config(self.pool_path)
        Path(batch).mkdir()
        return [dict(name="j" + str(i), node=name, state="SUCCEEDED", job_id="id" + str(i),
                     remote=dict(returncode=0, spectre_errors=0)) for i, name in enumerate(("a", "b"), 1)]
    monkeypatch.setattr(Controller, "run", run)
    def collect(self, batch, name, output):
        calls.append(("collect", name))
        return dict(job=name, state="SUCCEEDED", files=8, output=str(output))
    monkeypatch.setattr(Controller, "collect", collect)
    return types.SimpleNamespace(control=control, profile=profile, calls=calls, tmp=tmp_path)


def silent(*args, **kwargs):
    pass


def test_success_collects_every_node_and_new_directory_each_time(world):
    first = q.run_profile(world.profile, progress=silent)
    second = q.run_profile(world.profile, progress=silent)
    assert first["status"] == second["status"] == "SUCCEEDED"
    assert first["directory"] != second["directory"]
    assert read_json(world.tmp / "runs/latest.json")["directory"] == second["directory"]
    assert first["verification"] == dict(native_success=True, distributed_to_all_required_nodes=True,
                                         artifacts_verified=True, speedup_measured=False)
    assert len([x for x in world.calls if x[0] == "collect"]) == 4
    assert Path(first["report"]).is_file()


@pytest.mark.parametrize("failure", ["ssh", "bridge", "spectre", "models", "busy", "busy_check"])
def test_unready_node_prevents_all_dispatch(world, monkeypatch, failure):
    original = Controller.check
    def check(self, name):
        item = original(self, name)
        if name == "b":
            if failure in ("ssh", "bridge"):
                item[failure + "_error"] = "connection lost"
            elif failure == "spectre": item["spectre"]["version_returncode"] = 1
            elif failure == "models": item["spectre"]["required_files"] = [{"ok": False}]
            elif failure == "busy_check": item["busy_check_error"] = "cannot inspect"
        return item
    monkeypatch.setattr(Controller, "check", check)
    if failure == "busy":
        monkeypatch.setattr(q.ssh, "command", lambda *a, **kw: types.SimpleNamespace(stdout=b"hspice\nspectre\n"))
    result = q.run_profile(world.profile, progress=silent)
    assert result["status"] == "PREFLIGHT_FAILED"
    assert not any(x[0] in {"run", "collect"} for x in world.calls)


def test_check_only_has_no_vm_start_or_simulation(world, monkeypatch):
    vmx = world.tmp / "local.vmx"; vmx.touch()
    data = read_json(world.profile); data["local_vmx"] = str(vmx); write_json(world.profile, data)
    monkeypatch.setattr(q.vmware, "start", lambda *a: pytest.fail("check-only started VM"))
    result = q.run_profile(world.profile, check_only=True, progress=silent)
    assert result["status"] == "READY"
    assert not any(x[0] in {"run", "collect"} for x in world.calls)


def test_previous_unknown_lock_is_preserved(world):
    world.control.lock.write_text("previous uncertain batch")
    result = q.run_profile(world.profile, progress=silent)
    assert result["status"] == "ERROR"
    assert world.control.lock.read_text() == "previous uncertain batch"
    assert not world.calls


def test_double_click_lock_does_not_dispatch_and_releases_after_exit(world):
    with q.launcher_lock(world.control.root / "quickstart.lock"):
        with pytest.raises(RuntimeError, match="already running"):
            q.run_profile(world.profile, progress=silent)
    assert not world.calls
    assert q.run_profile(world.profile, check_only=True, progress=silent)["status"] == "READY"


@pytest.mark.parametrize("mutation", ["disabled", "missing", "parallel", "same_endpoint"])
def test_invalid_multi_node_pool_rejected_before_network(world, mutation):
    cfg = read_json(world.control.pool_path)
    if mutation == "disabled": cfg["nodes"][1]["enabled"] = False
    elif mutation == "missing": cfg["nodes"].pop()
    elif mutation == "parallel": cfg["max_parallel"] = 1
    else: cfg["nodes"][1]["host"] = cfg["nodes"][0]["host"]
    write_json(world.control.pool_path, cfg)
    assert q.run_profile(world.profile, progress=silent)["status"] == "ERROR"
    assert not world.calls


def test_unknown_retains_scheduler_lock_and_never_retries(world, monkeypatch):
    def run(self, *args, **kwargs):
        world.calls.append(("run", "unknown"))
        self.lock.write_text("unknown")
        return [dict(name="j1", node="a", state="UNKNOWN"), dict(name="j2", state="QUEUED")]
    monkeypatch.setattr(Controller, "run", run)
    assert q.run_profile(world.profile, progress=silent)["status"] == "UNKNOWN"
    assert world.control.lock.read_text() == "unknown"
    assert len([x for x in world.calls if x[0] == "run"]) == 1
    assert q.run_profile(world.profile, progress=silent)["status"] == "ERROR"
    assert len([x for x in world.calls if x[0] == "run"]) == 1


@pytest.mark.parametrize("failure", ["native", "distribution", "collection"])
def test_success_requires_native_success_distribution_and_collection(world, monkeypatch, failure):
    original = Controller.run
    def run(self, *args, **kwargs):
        rows = original(self, *args, **kwargs)
        if failure == "native": rows[1]["remote"]["spectre_errors"] = 1
        if failure == "distribution": rows[1]["node"] = "a"
        return rows
    monkeypatch.setattr(Controller, "run", run)
    if failure == "collection":
        monkeypatch.setattr(Controller, "collect", lambda *a, **kw: (_ for _ in ()).throw(ValueError("hash mismatch")))
    result = q.run_profile(world.profile, progress=silent)
    assert result["status"] in {"FAILED", "ERROR"}
    assert Path(result["report"]).is_file()


def test_report_escapes_remote_output():
    text = q.render_report(dict(status="ERROR", message="<script>alert(1)</script>", started_utc="now", directory="x"))
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_controller_rejects_changed_configuration_before_submission(tmp_path):
    c = Controller(tmp_path)
    cfg = dict(schema_version=1, max_parallel=1, nodes=[dict(name="a", host="x", user="u", port=22,
               root="/tmp/vlb", spectre="spectre")])
    write_json(c.pool_path, cfg)
    expected = q.runner.load_config(c.pool_path)
    changed = copy.deepcopy(cfg); changed["nodes"][0]["host"] = "other"
    write_json(c.pool_path, changed)
    with pytest.raises(RuntimeError, match="配置"):
        c.run(tmp_path / "not-used.json", tmp_path / "batch", expected_config=expected)
    assert not c.lock.exists()
    assert not (tmp_path / "batch").exists()

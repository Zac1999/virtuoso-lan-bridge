import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import types

import pytest
from virtuoso_lan_bridge.runner import controller as c


def config():
    return {"schema_version": 1, "max_parallel": 2, "nodes": [
        {"name": n, "host": "192.0.2.1", "user": "master", "port": 22, "enabled": True,
         "slots": 1, "threads": 1, "python": "python3", "spectre": "spectre", "root": "/tmp/lan-test"}
        for n in ("a", "b")]}


def requests(count=4):
    return [{"name": "job-" + str(i), "job_id": "job-" + str(i)} for i in range(count)]


def test_two_nodes_parallel_with_bounded_slots(tmp_path):
    running, assignments, high_water = {}, [], []
    def call(node, action, **kw):
        if action == "submit":
            jid = kw["request"]["job_id"]
            assert node["name"] not in running.values()
            running[jid] = node["name"]
            assignments.append(node["name"])
            high_water.append(len(running))
            return {"job_id": jid, "state": "RUNNING"}
        jid = kw["job_id"]
        del running[jid]
        return {"job_id": jid, "state": "SUCCEEDED"}
    rows = c.schedule(config(), requests(), tmp_path / "batch", call, lambda _: None)
    assert assignments == ["a", "b", "a", "b"]
    assert max(high_water) == 2
    assert all(r["state"] == "SUCCEEDED" for r in rows)


def test_unknown_submit_stops_queue_without_retry(tmp_path):
    calls = []
    def call(node, action, **kw):
        calls.append(action)
        raise TimeoutError("lost reply after start")
    rows = c.schedule(config(), requests(), tmp_path / "batch", call, lambda _: None)
    assert calls == ["submit"]
    assert [r["state"] for r in rows] == ["UNKNOWN", "QUEUED", "QUEUED", "QUEUED"]
    assert c.read(tmp_path / "batch/state.json") == rows


def test_unknown_poll_stops_new_work_but_tracks_known_jobs(tmp_path):
    def call(node, action, **kw):
        jid = kw.get("job_id", kw.get("request", {}).get("job_id"))
        if action == "status" and jid == "job-0":
            raise ConnectionError("disconnect")
        return {"job_id": jid, "state": "RUNNING" if action == "submit" else "SUCCEEDED"}
    rows = c.schedule(config(), requests(), tmp_path / "batch", call, lambda _: None)
    assert [r["state"] for r in rows] == ["UNKNOWN", "SUCCEEDED", "QUEUED", "QUEUED"]


def test_persist_dispatch_intent_before_transport(tmp_path):
    batch = tmp_path / "batch"
    def call(node, action, **kw):
        assert c.read(batch / "state.json")[0]["state"] == "DISPATCHING"
        return {"job_id": "job-0", "state": "SUCCEEDED"}
    c.schedule(config(), requests(1), batch, call, lambda _: None)
    with pytest.raises(FileExistsError):
        c.schedule(config(), requests(1), batch, call)


def test_refresh_only_reads_no_resubmit(tmp_path):
    c.write(tmp_path / "config.snapshot.json", config())
    original = [{"name": "j", "job_id": "j", "node": "a", "state": "UNKNOWN"}]
    c.write(tmp_path / "state.json", original)
    def call(node, action, **kw):
        assert action == "status"
        return {"job_id": "j", "state": "SUCCEEDED"}
    assert c.refresh(tmp_path, call)[0]["state"] == "SUCCEEDED"
    assert c.read(tmp_path / "state.json") == original


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a\\b", "a/../b", "a//b", "C:/escape", "NUL.txt", "com1", "a."])
def test_reject_path_escape_and_windows_special_names(name):
    with pytest.raises(ValueError):
        c.safe_name(name)


def test_manifest_validates_and_hashes_exact_bytes(tmp_path):
    folder = tmp_path / "deck"
    folder.mkdir()
    (folder / "input.scs").write_bytes(b"simulator lang=spectre\n")
    c.write(tmp_path / "jobs.json", {"jobs": [{"name": "one", "bundle": "deck"}]})
    j = c.prepare_jobs(tmp_path / "jobs.json")[0]
    assert base64.b64decode(j["files"]["input.scs"]) == (folder / "input.scs").read_bytes()
    assert j["hashes"]["input.scs"] == c.digest((folder / "input.scs").read_bytes())


def receipt():
    data = json.dumps({"job_id": "j", "state": "SUCCEEDED"}).encode()
    return {"job_id": "j", "state": "SUCCEEDED", "files": {"status.json": base64.b64encode(data).decode()},
            "hashes": {"status.json": c.digest(data)}}


def test_verified_collection_no_overwrite(tmp_path):
    r = receipt()
    c.save_artifacts(r, tmp_path / "out", "j")
    assert c.read(tmp_path / "out/status.json")["state"] == "SUCCEEDED"
    with pytest.raises(FileExistsError):
        c.save_artifacts(r, tmp_path / "out", "j")


@pytest.mark.parametrize("kind", ["hash", "identity", "native", "path"])
def test_invalid_receipt_writes_nothing(tmp_path, kind):
    r = receipt()
    if kind == "hash":
        r["hashes"]["status.json"] = "bad"
    if kind == "identity":
        r["job_id"] = "other"
    if kind == "native":
        r["state"] = "FAILED"
    if kind == "path":
        r["files"]["../escape"] = ""
    with pytest.raises(ValueError):
        c.save_artifacts(r, tmp_path / "out", "j")
    assert not (tmp_path / "out").exists()


def test_ssh_enforces_key_auth_host_verification():
    args = c.ssh_args(config()["nodes"][0])
    assert "StrictHostKeyChecking=yes" in args
    assert "BatchMode=yes" in args
    assert "StrictHostKeyChecking=no" not in args


@pytest.fixture
def worker(monkeypatch):
    # Exercise platform-independent worker behavior on Windows; no claim of Linux flock validation.
    if os.name == "nt":
        monkeypatch.setitem(sys.modules, "fcntl", types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *a: None))
    spec = importlib.util.spec_from_file_location("lan_worker_test", Path(c.__file__).with_name("worker.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("summary,code,expected", [
    ("spectre completes with 0 errors, 3 warnings, and 5 notices", 0, "SUCCEEDED"),
    ("spectre completes with 2 errors", 0, "FAILED"),
    ("spectre completes with 0 errors", 1, "FAILED"),
    ("process started", 0, "FAILED")])
def test_success_requires_exit_and_native_summary(tmp_path, monkeypatch, worker, summary, code, expected):
    root = tmp_path / "job"
    root.mkdir()
    (root / "bundle").mkdir()
    node = {"root": str(tmp_path), "slots": 1, "threads": 1, "spectre": sys.executable}
    worker.write_json(root / "node.json", node)
    worker.write_json(root / "request.json", {"entry": "input.scs", "timeout_seconds": 5})
    worker.write_json(root / "status.json", {"job_id": "job", "state": "STARTING"})
    class Process:
        returncode = code
        def __init__(self, *a, **kw):
            (root / "spectre.out").write_text(summary)
            assert kw["start_new_session"]
        def poll(self):
            return code
    monkeypatch.setattr(worker.subprocess, "Popen", Process)
    worker.run_job(root)
    assert worker.read_json(root / "status.json")["state"] == expected


def test_stale_worker_status_is_unknown(tmp_path, monkeypatch, worker):
    monkeypatch.setattr(worker, "job_path", lambda *a: tmp_path)
    worker.write_json(tmp_path / "status.json", {"job_id": "j", "state": "RUNNING", "heartbeat": time.time() - 90})
    assert worker.status({}, "j")["state"] == "UNKNOWN"


def test_duplicate_remote_job_never_launches(tmp_path, monkeypatch, worker):
    root = tmp_path / "jobs" / "j"
    root.mkdir(parents=True)
    worker.write_json(root / "status.json", {"job_id": "j", "state": "SUCCEEDED"})
    monkeypatch.setattr(worker, "job_path", lambda *a: root)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(FileExistsError):
        worker.submit({}, {"job_id": "j"}, "")

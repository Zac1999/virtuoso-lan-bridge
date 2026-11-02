"""Explicit Linux integration QA with a fake simulator; consumes no Cadence license."""
import base64
import importlib.util
import json
from pathlib import Path
import sys
import time


def main(root):
    root = Path(root)
    spec = importlib.util.spec_from_file_location("worker", root / "worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    source = (root / "worker.py").read_text()
    fake = root / "fake_spectre.py"
    fake.write_text('''#!/usr/bin/python3
import pathlib,sys,time
a=sys.argv[1:]
mode=pathlib.Path(a[0]).read_text().strip()
if mode in ("slow", "cancel", "busy"):
    time.sleep(30)
log=pathlib.Path(a[a.index("+log")+1])
log.write_text("spectre completes with %s errors, 0 warnings, and 0 notices\\n" % ("2" if mode=="failure" else "0"))
raw=pathlib.Path(a[a.index("-raw")+1]);raw.mkdir()
(raw/"fake.raw").write_text("QA only, not simulation data")
sys.exit(1 if mode=="failure" else 0)
''')
    fake.chmod(0o700)
    node = {"root": str(root / "runs"), "slots": 1, "threads": 1, "spectre": str(fake)}
    checks = []
    def start(jid, mode, timeout=10):
        data = mode.encode()
        request = {"job_id": jid, "entry": "input.scs", "files": {"input.scs": base64.b64encode(data).decode()},
                   "hashes": {"input.scs": worker.sha(data)}, "timeout_seconds": timeout}
        return worker.submit(node, request, source)
    def wait(jid, wanted=None):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            s = worker.status(node, jid)
            if s["state"] in worker.TERMINAL or s["state"] == wanted:
                return s
            time.sleep(.15)
        raise AssertionError("QA timeout: " + jid)
    start("success", "success")
    assert wait("success")["state"] == "SUCCEEDED"
    checks.append("detached_start_and_poll")
    pulled = worker.collect(node, "success", True)
    assert "psf/fake.raw" in pulled["files"]
    assert all(worker.sha(base64.b64decode(v)) == pulled["hashes"][k] for k, v in pulled["files"].items())
    checks.append("waveform_transfer_hashes")
    try:
        start("success", "success")
        raise AssertionError("duplicate accepted")
    except FileExistsError:
        checks.append("duplicate_never_relaunched")
    start("failure", "failure")
    assert wait("failure")["state"] == "FAILED"
    checks.append("native_failure")
    start("timeout", "slow", 1)
    assert wait("timeout")["state"] == "TIMED_OUT"
    checks.append("process_group_timeout")
    start("cancel", "cancel")
    assert wait("cancel", "RUNNING")["state"] == "RUNNING"
    start("busy", "busy")
    assert wait("busy")["state"] == "REJECTED"
    checks.append("linux_slot_lock")
    worker.rpc({"node": node, "action": "cancel", "job_id": "cancel"})
    assert wait("cancel")["state"] == "CANCELLED"
    checks.append("process_group_cancel")
    # Confirm slot released after timeout/cancel and a detached worker can complete again.
    start("after-cancel", "success")
    assert wait("after-cancel")["state"] == "SUCCEEDED"
    checks.append("slot_release_after_cancel")
    print(json.dumps({"status": "PASS", "checks": checks, "root": str(root), "real_simulations": 0}))


if __name__ == "__main__":
    main(sys.argv[1])

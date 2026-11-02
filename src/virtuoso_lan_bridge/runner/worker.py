"""Linux worker, Python >=3.8, stdlib only. Invoked over authenticated SSH."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "REJECTED"}


def safe_name(name):
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or str(p) != name or ".." in p.parts
            or any(not re.fullmatch(r"[A-Za-z0-9_+.-]+", x) or x.endswith(".")
                   or x.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL"}
                   or re.fullmatch(r"(?i)(COM|LPT)[0-9]", x.split(".")[0]) for x in p.parts)):
        raise ValueError("Unsafe relative path: " + name)
    return p


def write_json(path, value):
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(str(tmp), str(path))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def job_path(node, job_id):
    if not re.fullmatch(r"[a-z0-9_-]{1,100}", job_id):
        raise ValueError("Invalid job ID")
    root = Path(node["root"])
    if not root.is_absolute() or ".." in root.parts or len(root.parts) < 3:
        raise ValueError("Use an absolute, dedicated remote root")
    return root / "jobs" / job_id


def environment(node):
    return dict(os.environ, **node.get("environment", {}))


def check_files(node):
    rows = []
    for item in node.get("required_files", []):
        path = Path(item["path"])
        digest = sha(path.read_bytes()) if path.is_file() else None
        rows.append({"path": str(path), "sha256": digest,
                     "ok": digest is not None and (not item.get("sha256") or digest == item["sha256"])})
    return rows


def doctor(node):
    env = environment(node)
    exe = shutil.which(node["spectre"], path=env.get("PATH"))
    version = ""
    version_rc = None
    if exe:
        p = subprocess.run([exe, "-W"], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=20)
        version = p.stdout.decode("utf-8", "replace")[-4000:]
        version_rc = p.returncode
    memory = Path("/proc/meminfo").read_text() if Path("/proc/meminfo").exists() else ""
    return {"host": socket.gethostname(), "python": sys.version.split()[0],
            "cpus": os.cpu_count(), "memory": memory.splitlines()[:3],
            "spectre": exe, "version": version, "version_returncode": version_rc,
            "required_files": check_files(node), "license_checkout_verified": False}


def status(node, job_id):
    root = job_path(node, job_id)
    if not root.exists():
        return {"job_id": job_id, "state": "ABSENT"}
    if not (root / "status.json").is_file():
        return {"job_id": job_id, "state": "UNKNOWN", "reason": "Incomplete staging; do not resubmit"}
    result = read_json(root / "status.json")
    if result["state"] not in TERMINAL and time.time() - result.get("heartbeat", 0) > 30:
        result = dict(result, state="UNKNOWN", reason="Worker heartbeat stale; inspect node before new work")
    return result


def submit(node, request, source):
    root = job_path(node, request["job_id"])
    # Preserve uncertainty across controller restarts / independent batch directories.
    if root.parent.exists():
        for old in root.parent.iterdir():
            if old.is_dir() and status(node, old.name)["state"] == "UNKNOWN":
                raise ValueError("Unresolved remote job blocks new submissions: " + old.name)
    # Atomic, immutable identity reservation: an existing job is NEVER relaunched.
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(exist_ok=False)
    write_json(root / "request.json", request)
    write_json(root / "node.json", node)
    (root / "worker.py").write_text(source, encoding="utf-8")
    bundle = root / "bundle"
    bundle.mkdir()
    total = 0
    for name, encoded in request["files"].items():
        p = safe_name(name)
        data = base64.b64decode(encoded, validate=True)
        total += len(data)
        if total > 32 * 1024 * 1024:
            raise ValueError("Input bundle exceeds 32 MiB")
        if sha(data) != request["hashes"][name]:
            raise ValueError("Input hash mismatch")
        dest = bundle.joinpath(*p.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    if not bundle.joinpath(*safe_name(request["entry"]).parts).is_file():
        raise ValueError("Missing entry netlist")
    write_json(root / "status.json", {"job_id": request["job_id"], "state": "STARTING",
                                      "heartbeat": time.time(), "input_hashes": request["hashes"]})
    with (root / "worker.log").open("ab") as log:
        subprocess.Popen([sys.executable, str(root / "worker.py"), "--run", str(root)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)
    return status(node, request["job_id"])


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def run_job(root):
    request, node = read_json(root / "request.json"), read_json(root / "node.json")
    state = read_json(root / "status.json")
    slot = None
    process = None
    try:
        lockroot = Path(node["root"]) / "slots"
        lockroot.mkdir(exist_ok=True)
        for number in range(node["slots"]):
            candidate = (lockroot / (str(number) + ".lock")).open("a+")
            try:
                fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                slot = candidate
                break
            except BlockingIOError:
                candidate.close()
        if slot is None:
            state.update(state="REJECTED", reason="All configured node slots busy")
            return
        checks = check_files(node)
        if any(not row["ok"] for row in checks):
            state.update(state="REJECTED", reason="Required model/file check failed", required_files=checks)
            return
        env = environment(node)
        exe = shutil.which(node["spectre"], path=env.get("PATH"))
        if not exe:
            raise ValueError("Spectre executable not found in configured environment")
        if (root / "cancel.request").exists():
            state.update(state="CANCELLED", reason="Cancelled before simulator launch")
            return
        bundle = root / "bundle"
        command = [exe, request["entry"], "+log", str(root / "spectre.out"),
                   "-raw", str(root / "psf"), "+mt=" + str(node["threads"])] + node.get("spectre_args", [])
        started = time.time()
        state.update(state="RUNNING", started=started, command=command, required_files=checks)
        with (root / "console.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=str(bundle), env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True,
                                       pass_fds=(slot.fileno(),))
            while process.poll() is None:
                state.update(heartbeat=time.time(), pid=process.pid)
                write_json(root / "status.json", state)
                if (root / "cancel.request").exists():
                    stop_group(process)
                    state["state"] = "CANCELLED"
                    break
                if time.time() - started > request["timeout_seconds"]:
                    stop_group(process)
                    state["state"] = "TIMED_OUT"
                    break
                time.sleep(1)
        state["returncode"] = process.returncode
        log_text = ""
        if (root / "spectre.out").exists():
            with (root / "spectre.out").open("rb") as native_log:
                native_log.seek(max(0, (root / "spectre.out").stat().st_size - 65536))
                log_text = native_log.read().decode("utf-8", "replace")
        summaries = re.findall(r"spectre completes with\s+(\d+)\s+errors?", log_text, re.I)
        state["spectre_errors"] = int(summaries[-1]) if summaries else None
        if state["state"] == "RUNNING":
            state["state"] = "SUCCEEDED" if process.returncode == 0 and summaries and int(summaries[-1]) == 0 else "FAILED"
            if not summaries:
                state["reason"] = "Missing native Spectre completion summary"
    except Exception as exc:
        if process is not None and process.poll() is None:
            stop_group(process)
        state.update(state="FAILED", reason=str(exc))
    finally:
        state.update(heartbeat=time.time(), finished=time.time())
        write_json(root / "status.json", state)
        if slot:
            slot.close()


def collect(node, job_id, waveforms=False, limit=64 * 1024 * 1024):
    root = job_path(node, job_id)
    current = status(node, job_id)
    if current["state"] not in TERMINAL:
        raise ValueError("Collect only a confirmed terminal job")
    paths = [root / n for n in ("status.json", "spectre.out", "console.log", "worker.log")]
    if waveforms and (root / "psf").exists():
        paths.extend(sorted((root / "psf").rglob("*")))
    files, hashes, total = {}, {}, 0
    for p in paths:
        if not p.exists() or p.is_dir():
            continue
        if p.is_symlink() or root.resolve() not in p.resolve().parents:
            raise ValueError("Artifact escapes run directory")
        name = p.relative_to(root).as_posix()
        safe_name(name)
        total += p.stat().st_size
        if total > limit or len(files) >= 5000:
            raise ValueError("Artifact limit exceeded; fetch logs only or use managed file transfer")
        data = p.read_bytes()
        files[name], hashes[name] = base64.b64encode(data).decode("ascii"), sha(data)
    return {"job_id": job_id, "state": current["state"], "files": files, "hashes": hashes}


def rpc(payload):
    node, action = payload["node"], payload["action"]
    if action == "doctor":
        return doctor(node)
    if action == "submit":
        return submit(node, payload["request"], payload["source"])
    job_id = payload["job_id"]
    if action == "status":
        return status(node, job_id)
    if action == "cancel":
        current = status(node, job_id)
        if current["state"] == "ABSENT":
            raise ValueError("No such job")
        if current["state"] not in TERMINAL:
            (job_path(node, job_id) / "cancel.request").touch(exist_ok=True)
        return dict(current, cancel_requested=current["state"] not in TERMINAL)
    if action == "collect":
        return collect(node, job_id, payload.get("waveforms", False))
    raise ValueError("Unknown action")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--run":
        run_job(Path(sys.argv[2]))
    else:
        try:
            print(json.dumps(rpc(json.load(sys.stdin)), ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"error": str(exc)}))
            sys.exit(1)

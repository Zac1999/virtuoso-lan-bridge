"""Local Python >=3.10 controller. No third-party packages required."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "REJECTED"}
SOURCE = Path(__file__).with_name("worker.py").read_text(encoding="utf-8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or str(p) != name or ".." in p.parts
            or any(not re.fullmatch(r"[A-Za-z0-9_+.-]+", x) or x.endswith(".")
                   or x.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL"}
                   or re.fullmatch(r"(?i)(COM|LPT)[0-9]", x.split(".")[0]) for x in p.parts)):
        raise ValueError("Unsafe relative path: " + name)
    return p


def load_config(path):
    cfg = read(path)
    if cfg.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if type(cfg.get("max_parallel")) is not int or not 1 <= cfg["max_parallel"] <= 64:
        raise ValueError("max_parallel must be 1..64 (within verified license capacity)")
    names = set()
    for node in cfg["nodes"]:
        if not re.fullmatch(r"[a-z0-9_-]+", node["name"]) or node["name"] in names:
            raise ValueError("Invalid/duplicate node name")
        names.add(node["name"])
        for key in ("host", "user"):
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", node[key]):
                raise ValueError("Invalid " + key)
        for key, default, maximum in (("port", 22, 65535), ("slots", 1, 64), ("threads", 1, 256)):
            node.setdefault(key, default)
            if type(node[key]) is not int or not 1 <= node[key] <= maximum:
                raise ValueError("Invalid " + key)
        root = PurePosixPath(node["root"])
        if not root.is_absolute() or len(root.parts) < 3 or ".." in root.parts:
            raise ValueError("Use an absolute, dedicated remote root")
        node.setdefault("enabled", True)
        node.setdefault("python", "python3")
        node.setdefault("spectre_args", [])
        for key in ("python", "spectre"):
            if not isinstance(node[key], str) or not node[key] or "\x00" in node[key]:
                raise ValueError("Invalid executable")
        if not isinstance(node["spectre_args"], list) or any(not isinstance(x, str) or "\x00" in x for x in node["spectre_args"]):
            raise ValueError("spectre_args must be a string list")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) or not isinstance(v, str) or "\x00" in v
               for k, v in node.get("environment", {}).items()):
            raise ValueError("Invalid environment")
    if not names:
        raise ValueError("No nodes configured")
    return cfg


def ssh_args(node):
    args = [node.get("ssh", "ssh")]
    if node.get("ssh_config"):
        args += ["-F", node["ssh_config"]]
    if node.get("identity_file"):
        args += ["-i", node["identity_file"]]
    args += ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
             "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
             "-p", str(node["port"]), node["user"] + "@" + node["host"]]
    return args


def remote(node, action, **kwargs):
    code = "import base64;exec(compile(base64.b64decode(%r),'lan_worker','exec'))" % base64.b64encode(SOURCE.encode()).decode()
    command = shlex.quote(node["python"]) + " -c " + shlex.quote(code)
    payload = dict(node=node, action=action, **kwargs)
    if action == "submit":
        payload["source"] = SOURCE
    # File-backed bounded transport; an SSH timeout says nothing about remote termination.
    with tempfile.TemporaryFile() as inp, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        inp.write(json.dumps(payload).encode("utf-8"))
        inp.seek(0)
        hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen(ssh_args(node) + [command], stdin=inp, stdout=out, stderr=err, **hidden)
        deadline = time.monotonic() + (180 if action == "collect" else 60)
        try:
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("SSH timeout; remote outcome unknown")
                if os.fstat(out.fileno()).st_size > 100 * 1024 * 1024 or os.fstat(err.fileno()).st_size > 1024 * 1024:
                    raise ValueError("SSH response exceeded transport budget")
                time.sleep(.05)
            if os.fstat(out.fileno()).st_size > 100 * 1024 * 1024 or os.fstat(err.fileno()).st_size > 1024 * 1024:
                raise ValueError("SSH response exceeded transport budget")
        except BaseException:
            process.kill()
            process.wait(timeout=10)
            raise
        out.seek(0)
        err.seek(0)
        raw = out.read()
        if process.returncode:
            raise RuntimeError("SSH/worker failed: " + err.read().decode("utf-8", "replace")[-1500:] + raw.decode("utf-8", "replace")[-1500:])
        result = json.loads(raw)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result


def prepare_jobs(manifest):
    manifest = Path(manifest).resolve()
    jobs, labels = [], set()
    for item in read(manifest)["jobs"]:
        label = item["name"]
        if not re.fullmatch(r"[a-z0-9_-]{1,60}", label) or label in labels:
            raise ValueError("Job names must be unique lowercase identifiers")
        labels.add(label)
        folder = (manifest.parent / item["bundle"]).resolve(strict=True)
        if not folder.is_dir():
            raise ValueError("Bundle must be an explicit directory")
        files, hashes, size = {}, {}, 0
        for p in sorted(folder.rglob("*")):
            if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) or folder not in p.resolve().parents:
                raise ValueError("Bundle links are unsupported; copy explicit input files")
            if p.is_dir():
                continue
            name = p.relative_to(folder).as_posix()
            safe_name(name)
            size += p.stat().st_size
            if size > 32 * 1024 * 1024 or len(files) >= 5000:
                raise ValueError("Bundle exceeds 32 MiB/5000 files; do not bundle PDK trees")
            data = p.read_bytes()
            files[name], hashes[name] = base64.b64encode(data).decode(), digest(data)
        entry = item.get("entry", "input.scs")
        safe_name(entry)
        if entry not in files:
            raise ValueError("Missing entry netlist: " + entry)
        timeout = item.get("timeout_seconds", 3600)
        if type(timeout) is not int or not 1 <= timeout <= 604800:
            raise ValueError("Invalid timeout_seconds")
        jobs.append({"name": label, "job_id": label + "-" + uuid.uuid4().hex[:16], "entry": entry,
                     "timeout_seconds": timeout, "files": files, "hashes": hashes})
    if not jobs:
        raise ValueError("No jobs")
    return jobs


def event(batch, row):
    with (Path(batch) / "ledger.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(dict(time=time.time(), **row), ensure_ascii=False) + "\n")
        out.flush()
        os.fsync(out.fileno())


def schedule(cfg, requests, batch, call=remote, pause=time.sleep):
    """One controller loop; record DISPATCHING before crossing the SSH boundary."""
    batch = Path(batch)
    nodes = {n["name"]: n for n in cfg["nodes"] if n["enabled"]}
    if not nodes:
        raise ValueError("No enabled node")
    batch.mkdir(parents=True, exist_ok=False)
    write(batch / "config.snapshot.json", cfg)
    write(batch / "requests.json", requests)
    rows = [{"name": x["name"], "job_id": x["job_id"], "state": "QUEUED"} for x in requests]
    write(batch / "state.json", rows)
    active = {}
    halted = False
    while True:
        for index in list(active):
            row = rows[index]
            try:
                result = call(nodes[row["node"]], "status", job_id=row["job_id"])
                if result.get("job_id") != row["job_id"] or result.get("state") not in TERMINAL | {"STARTING", "RUNNING"}:
                    raise ValueError("Remote identity/state not confirmed")
                row.update(remote=result, state=result["state"])
                if row["state"] in TERMINAL:
                    del active[index]
                    event(batch, row)
            except Exception as exc:
                row.update(state="UNKNOWN", error=str(exc))
                event(batch, row)
                del active[index]
                halted = True
            write(batch / "state.json", rows)
        if not halted:
            for index, request in enumerate(requests):
                if rows[index]["state"] != "QUEUED" or len(active) >= cfg["max_parallel"]:
                    continue
                available = [n for n in nodes.values() if sum(rows[i]["node"] == n["name"] for i in active) < n["slots"]]
                if not available:
                    break
                # Spread across idle nodes before filling additional slots on one node.
                node = min(available, key=lambda n: sum(rows[i]["node"] == n["name"] for i in active) / n["slots"])
                row = rows[index]
                row.update(node=node["name"], state="DISPATCHING")
                write(batch / "state.json", rows)
                event(batch, row)
                try:
                    result = call(node, "submit", request=request)
                    if result.get("job_id") != row["job_id"] or result.get("state") not in TERMINAL | {"STARTING", "RUNNING"}:
                        raise ValueError("Submission not confirmed")
                    row.update(remote=result, state=result["state"])
                    if row["state"] not in TERMINAL:
                        active[index] = True
                except Exception as exc:
                    row.update(state="UNKNOWN", error=str(exc))
                    halted = True
                event(batch, row)
                write(batch / "state.json", rows)
                if halted:
                    break
        if not active and (halted or all(r["state"] in TERMINAL for r in rows)):
            return rows
        pause(2)


def refresh(batch, call=remote):
    batch = Path(batch)
    cfg, rows = read(batch / "config.snapshot.json"), read(batch / "state.json")
    nodes = {n["name"]: n for n in cfg["nodes"]}
    # Read-only reconciliation: never launches a job, including after controller interruption.
    for row in rows:
        if "node" not in row or row["state"] in TERMINAL:
            continue
        try:
            result = call(nodes[row["node"]], "status", job_id=row["job_id"])
            if result.get("job_id") != row["job_id"] or result.get("state") not in TERMINAL | {"STARTING", "RUNNING"}:
                raise ValueError("Remote job identity/state unknown")
            row.update(state=result["state"], remote=result)
            row.pop("error", None)
        except Exception as exc:
            row.update(state="UNKNOWN", error=str(exc))
    return rows


def save_artifacts(result, dest, job_id):
    if result.get("job_id") != job_id or result.get("state") not in TERMINAL:
        raise ValueError("Artifact receipt identity/state mismatch")
    decoded, size = {}, 0
    for name, content in result["files"].items():
        safe_name(name)
        if name.casefold() in {x.casefold() for x in decoded}:
            raise ValueError("Case-colliding artifacts")
        data = base64.b64decode(content, validate=True)
        size += len(data)
        if size > 64 * 1024 * 1024 or len(decoded) >= 5000 or digest(data) != result["hashes"].get(name):
            raise ValueError("Artifact size/hash mismatch")
        decoded[name] = data
    if set(result["hashes"]) != set(decoded) or "status.json" not in decoded:
        raise ValueError("Incomplete artifact receipt")
    native = json.loads(decoded["status.json"])
    if native.get("job_id") != job_id or native.get("state") != result["state"]:
        raise ValueError("Native status does not match receipt")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    for name, data in decoded.items():
        p = dest.joinpath(*PurePosixPath(name).parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    write(dest / "transfer-receipt.json", {k: v for k, v in result.items() if k != "files"})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Cadence LAN netlist runner; JSON output for agents")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "doctor", "run"):
        p = sub.add_parser(action)
        p.add_argument("--config", required=True)
        if action != "doctor":
            p.add_argument("--jobs", required=True)
        if action == "run":
            p.add_argument("--batch", required=True)
    for action in ("status", "fetch", "cancel"):
        p = sub.add_parser(action)
        p.add_argument("--batch", required=True)
        if action != "status":
            p.add_argument("--job", required=True, help="Job name in the batch")
        if action == "fetch":
            p.add_argument("--output", required=True)
            p.add_argument("--waveforms", action="store_true")
    args = parser.parse_args(argv)
    if args.action in {"plan", "doctor", "run"}:
        cfg = load_config(args.config)
        if args.action == "doctor":
            def probe(n):
                try:
                    info = remote(n, "doctor")
                    ready = bool(info["spectre"]) and info["version_returncode"] == 0 and all(x["ok"] for x in info["required_files"])
                    return {"node": n["name"], "probe": info, "software_ready": ready}
                except Exception as exc:
                    return {"node": n["name"], "error": str(exc)}
            with ThreadPoolExecutor(max_workers=4) as pool:
                result = list(pool.map(probe, [n for n in cfg["nodes"] if n["enabled"]]))
        else:
            jobs = prepare_jobs(args.jobs)
            if args.action == "plan":
                result = {"nodes": [n["name"] for n in cfg["nodes"] if n["enabled"]],
                          "max_parallel": cfg["max_parallel"], "jobs": [{k: v for k, v in j.items() if k != "files"} for j in jobs],
                          "remote_execution": False}
            else:
                # One scheduler per config. A crash leaves this marker for explicit inspection.
                lock = Path(args.config).resolve().with_suffix(".controller.lock")
                with lock.open("x", encoding="utf-8") as out:
                    json.dump({"pid": os.getpid(), "batch": str(Path(args.batch).resolve())}, out)
                try:
                    result = schedule(cfg, jobs, args.batch)
                except BaseException:
                    raise  # Keep lock: jobs may still be running remotely.
                else:
                    if all(row["state"] in TERMINAL for row in result):
                        lock.unlink()
    elif args.action == "status":
        result = refresh(args.batch)
    else:
        cfg = read(Path(args.batch) / "config.snapshot.json")
        rows = read(Path(args.batch) / "state.json")
        matches = [r for r in rows if r["name"] == args.job and "node" in r]
        if len(matches) != 1:
            raise ValueError("Job not dispatched or name not found")
        row = matches[0]
        node = next(n for n in cfg["nodes"] if n["name"] == row["node"])
        result = remote(node, "collect" if args.action == "fetch" else "cancel",
                        job_id=row["job_id"], **({"waveforms": args.waveforms} if args.action == "fetch" else {}))
        if args.action == "fetch":
            save_artifacts(result, args.output, row["job_id"])
            result = {"job_id": row["job_id"], "output": str(Path(args.output).resolve()), "state": result["state"], "files": len(result["files"])}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, list) and any(r.get("state") in {"UNKNOWN", "FAILED", "TIMED_OUT", "REJECTED", "CANCELLED"} or "error" in r or r.get("software_ready") is False for r in result):
        return 2
    return 0

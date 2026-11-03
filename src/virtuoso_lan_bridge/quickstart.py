"""Saved, all-nodes-required workflow; uses the existing scheduler and SSH trust."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import html
import os
from pathlib import Path
import re
import time
import uuid
import webbrowser

from . import ssh, vmware
from .common import read_json, write_json
from .controller import Controller
from .runner import controller as runner


@contextmanager
def launcher_lock(path):
    """OS lock releases on exit/crash. The scheduler's uncertainty lock is separate."""
    with Path(path).open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("联动入口正在运行，请查看已有窗口。 / Quickstart is already running.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def load_profile(path):
    path = Path(path).resolve(strict=True)
    profile = read_json(path)
    if profile.get("schema_version") != 1:
        raise ValueError("profile schema_version must be 1")
    for key in ("controller_state", "jobs", "results_root"):
        value = profile.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError("Missing profile path: " + key)
        profile[key] = str((path.parent / value).resolve())
    required = profile.get("required_nodes")
    if (not isinstance(required, list) or len(required) < 2 or
            any(not isinstance(x, str) or not re.fullmatch(r"[a-z0-9_-]+", x) for x in required) or
            len(set(required)) != len(required)):
        raise ValueError("required_nodes must name at least two distinct paired nodes")
    for key in ("require_bridge", "stop_if_simulator_busy"):
        profile.setdefault(key, True)
        if type(profile[key]) is not bool:
            raise ValueError(key + " must be boolean")
    if not (Path(profile["controller_state"]) / "pool.json").is_file():
        raise ValueError("先完成节点配对 / Pair nodes before using quickstart")
    if profile.get("local_vmx"):
        profile["local_vmx"] = str((path.parent / profile["local_vmx"]).resolve(strict=True))
    return profile


def probe(controller, name, check_busy):
    result = controller.check(name)
    if "ssh_error" not in result and check_busy:
        try:
            output = ssh.command(controller.node(name), "ps -eo comm=", timeout=20).stdout.decode("utf-8", "replace")
            result["running_simulators"] = sorted({x.strip() for x in output.splitlines()
                if re.fullmatch(r"(?i)(spectre|hspice)(64)?", x.strip())})
        except Exception as exc:
            result["busy_check_error"] = str(exc)
    return result


def ready(check, profile):
    if any(k.endswith("_error") and (k != "bridge_error" or profile["require_bridge"]) for k in check):
        return False
    spec = check.get("spectre", {})
    if not check.get("ssh") or spec.get("version_returncode") != 0 or not spec.get("spectre"):
        return False
    if any(not r.get("ok") for r in spec.get("required_files", [])):
        return False
    if profile["require_bridge"] and check.get("bridge", {}).get("output") != "3":
        return False
    return not (profile["stop_if_simulator_busy"] and check.get("running_simulators"))


def render_report(result):
    esc = lambda value: html.escape(str(value), quote=True)
    checks = []
    for check in result.get("checks", []):
        errors = "; ".join(str(v) for k, v in check.items() if k.endswith("_error"))
        if check.get("running_simulators"):
            errors += " 已有仿真进程 / busy: " + ", ".join(check["running_simulators"])
        checks.append("<tr>" + "".join("<td>" + esc(v) + "</td>" for v in (
            check["node"], check.get("endpoint", ""), "OK" if check.get("ssh") else "FAIL",
            "OK" if check.get("spectre", {}).get("version_returncode") == 0 else "FAIL",
            check.get("bridge", {}).get("output", "unavailable"), errors)) + "</tr>")
    jobs = []
    for row in result.get("jobs", []):
        native = row.get("remote", {})
        jobs.append("<tr>" + "".join("<td>" + esc(v) + "</td>" for v in (
            row["name"], row.get("node", "—"), row["state"], native.get("returncode", "—"),
            native.get("spectre_errors", "—"))) + "</tr>")
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>Virtuoso 双机联动 / Multi-node simulation</title>
<style>body{font:16px/1.6 system-ui,sans-serif;margin:40px auto;max-width:1100px;padding:0 24px;color:#162735;background:#f6f8fa}
main{background:white;padding:28px;border:1px solid #d4dfe6;border-radius:12px}h1{font-size:27px}table{border-collapse:collapse;width:100%%;margin:20px 0}td,th{text-align:left;border-bottom:1px solid #ddd;padding:10px;overflow-wrap:anywhere}small{color:#556}code{overflow-wrap:anywhere}.status{font-size:23px;font-weight:700}</style><main>
<h1>Virtuoso 双机联动 <small>/ Multi-node simulation</small></h1>
<p class="status">%s</p><p>%s</p><p><small>%s</small></p>
<table><tr><th>节点 / Node</th><th>入口 / Endpoint</th><th>SSH</th><th>Spectre</th><th>Bridge 1+2</th><th>说明 / Details</th></tr>%s</table>
<table><tr><th>任务 / Job</th><th>节点 / Node</th><th>状态 / Status</th><th>退出码 / Exit</th><th>原生日志错误 / Errors</th></tr>%s</table>
<p>结果目录 / Results: <code>%s</code></p>
<p><a href="report.json">完整报告 / JSON report</a></p>
<p>每次双击运行已保存的任务清单。默认 RC 示例用于验证分发和回传；实际电路需先从 ADE 导出网表并更换清单。<br>
Each launch runs the saved manifest. RC examples verify distribution and collection; export your ADE netlists to run your own circuits.</p>
<p>未测量加速比；单个 transient 不会自动跨电脑拆分。 / No speedup benchmark; a single transient is not split across machines.</p>
</main></html>""" % (esc(result["status"]), esc(result["message"]), esc(result["started_utc"]),
    "".join(checks), "".join(jobs), esc(result["directory"]))


def run_profile(path, *, check_only=False, open_report=False, progress=print):
    profile = load_profile(path)
    c = Controller(profile["controller_state"])
    with launcher_lock(c.root / "quickstart.lock"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        folder = Path(profile["results_root"]) / stamp
        folder.mkdir(parents=True, exist_ok=False)
        result = dict(status="CHECKING", started_utc=datetime.now(timezone.utc).isoformat(),
                      directory=str(folder), report=str(folder / "report.html"), checks=[], jobs=[],
                      mode="check-only" if check_only else "simulate", message="正在检查 / Checking")
        write_json(folder / "profile.snapshot.json", profile, exclusive=True)

        def finish(status, message):
            result.update(status=status, message=message, finished_utc=datetime.now(timezone.utc).isoformat())
            write_json(folder / "report.json", result)
            (folder / "report.html").write_text(render_report(result), encoding="utf-8")
            write_json(Path(profile["results_root"]) / "latest.json", result)
            progress(message, flush=True)
            progress("报告 / Report: " + result["report"], flush=True)
            if open_report:
                try:
                    webbrowser.open((folder / "report.html").as_uri())
                except OSError:
                    pass  # The saved report remains usable without a browser.
            return result

        try:
            c.idle()  # Never clear a previous scheduler's uncertain work.
            cfg = runner.load_config(c.pool_path)
            enabled = [n for n in cfg["nodes"] if n["enabled"]]
            if {n["name"] for n in enabled} != set(profile["required_nodes"]):
                raise ValueError("启用节点必须与联动配置完全一致 / Enabled nodes must match required_nodes")
            if cfg["max_parallel"] < len(enabled):
                raise ValueError("并发数不足 / max_parallel is smaller than the required node count")
            if len({(n["host"], n["port"], n["user"]) for n in enabled}) != len(enabled):
                raise ValueError("联动节点不能重复同一个 SSH 入口 / Duplicate SSH endpoints")
            # Validate bundles before any VM startup or simulation.
            if len(runner.prepare_jobs(profile["jobs"])) < len(enabled):
                raise ValueError("任务数必须至少等于节点数 / Need at least one job per node")
            write_json(folder / "pool.checked.json", cfg, exclusive=True)
            if profile.get("local_vmx") and not check_only:
                progress("启动或复用本机虚拟机 / Start or reuse local VM", flush=True)
                result["vmware"] = vmware.start(profile["local_vmx"], profile.get("vmrun"))
                if result["vmware"]["state"] == "running":
                    # Bounded read-only readiness polling after this launcher starts a cold VM.
                    name = profile.get("local_node")
                    if name not in profile["required_nodes"]:
                        raise ValueError("local_node must identify the local VM")
                    for attempt in range(12):
                        try:
                            ssh.command(c.node(name), "true", timeout=10)
                            break
                        except Exception:
                            if attempt == 11:
                                raise RuntimeError("本机 VM 的 SSH 尚未就绪 / Local VM SSH not ready")
                            time.sleep(2)
            progress("检查全部节点的 SSH、Spectre、Bridge / Checking all nodes", flush=True)
            with ThreadPoolExecutor(max_workers=min(8, len(enabled))) as pool:
                result["checks"] = list(pool.map(lambda n: probe(c, n["name"], profile["stop_if_simulator_busy"]), enabled))
            if not all(ready(check, profile) for check in result["checks"]):
                return finish("PREFLIGHT_FAILED", "检查未通过，未派发仿真。查看节点错误；台式机需开机、联网并启动 VM/SSH 入口。 / Preflight failed; no simulations dispatched.")
            if check_only:
                return finish("READY", "全部节点连接检查通过；本次没有运行仿真或验证许可证签出。 / All nodes ready; no simulation or license checkout performed.")
            progress("分发独立仿真任务 / Dispatching independent simulation jobs", flush=True)
            result["jobs"] = c.run(profile["jobs"], folder / "batch", expected_config=cfg)
            write_json(folder / "report.json", result)
            result["collections"] = []
            for row in result["jobs"]:
                if row["state"] not in runner.TERMINAL or "node" not in row:
                    continue
                progress("取回并校验结果 / Collecting: " + row["name"], flush=True)
                receipt = c.collect(folder / "batch", row["name"], folder / "results" / row["name"])
                result["collections"].append(receipt)
            verified = dict(
                native_success=all(r["state"] == "SUCCEEDED" and r.get("remote", {}).get("returncode") == 0
                                   and r.get("remote", {}).get("spectre_errors") == 0 for r in result["jobs"]),
                distributed_to_all_required_nodes={r.get("node") for r in result["jobs"]} == set(profile["required_nodes"]),
                artifacts_verified=len(result["collections"]) == len(result["jobs"]), speedup_measured=False)
            result["verification"] = verified
            if all(verified[k] for k in ("native_success", "distributed_to_all_required_nodes", "artifacts_verified")):
                return finish("SUCCEEDED", "多节点仿真完成，原生日志通过，结果全部取回并通过哈希校验。 / Multi-node simulations and verified collection succeeded.")
            if any(r["state"] == "UNKNOWN" for r in result["jobs"]):
                return finish("UNKNOWN", "存在状态未知的任务，保留调度锁；请先查询原批次，不要重复派发。 / Unknown outcome; scheduler lock retained. Inspect the original batch.")
            return finish("FAILED", "未满足全部节点仿真成功及回传条件，请查看任务结果。 / Simulation/distribution/collection requirements were not all met.")
        except Exception as exc:
            return finish("ERROR", str(exc))

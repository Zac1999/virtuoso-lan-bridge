import argparse
import json
from pathlib import Path
import sys

from . import node, ssh, vmware
from .common import read_json, state_root, write_json
from .controller import Controller
from .runner import controller as runner


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def controller_main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv == ["gui"]:
        from .gui import launch
        launch("controller")
        return 0
    p = argparse.ArgumentParser(description="Virtuoso LAN Bridge 母端")
    p.add_argument("--state", type=Path)
    s = p.add_subparsers(dest="action", required=True)
    q = s.add_parser("pair"); q.add_argument("--ip", required=True); q.add_argument("--output", required=True)
    q = s.add_parser("import-node"); q.add_argument("card")
    s.add_parser("nodes")
    q = s.add_parser("check"); q.add_argument("name")
    q = s.add_parser("skill"); q.add_argument("name"); q.add_argument("expression")
    q = s.add_parser("run"); q.add_argument("--jobs", required=True); q.add_argument("--batch", required=True)
    q = s.add_parser("status"); q.add_argument("batch")
    q = s.add_parser("fetch"); q.add_argument("batch"); q.add_argument("job"); q.add_argument("output"); q.add_argument("--logs-only", action="store_true")
    q = s.add_parser("cancel"); q.add_argument("batch"); q.add_argument("job")
    q = s.add_parser("start-vm"); q.add_argument("vmx"); q.add_argument("--vmrun")
    a = p.parse_args(argv)
    c = Controller(a.state)
    if a.action == "pair": result = c.pairing(a.ip, a.output)
    elif a.action == "import-node": result = c.import_card(a.card)
    elif a.action == "nodes": result = c.nodes()
    elif a.action == "check": result = c.check(a.name)
    elif a.action == "skill": result = ssh.skill(c.node(a.name), a.expression)
    elif a.action == "run": result = c.run(a.jobs, a.batch)
    elif a.action == "status": result = c.reconcile(a.batch)
    elif a.action == "fetch": result = c.collect(a.batch, a.job, a.output, not a.logs_only)
    elif a.action == "start-vm": result = vmware.start(a.vmx, a.vmrun)
    elif a.action == "cancel":
        rows = read_json(Path(a.batch) / "state.json")
        row = next(r for r in rows if r["name"] == a.job)
        cfg = read_json(Path(a.batch) / "config.snapshot.json")
        n = next(n for n in cfg["nodes"] if n["name"] == row["node"])
        result = runner.remote(n, "cancel", job_id=row["job_id"])
    _print(result)
    if isinstance(result, list) and any(r.get("state") in {"UNKNOWN", "FAILED", "TIMED_OUT", "REJECTED", "CANCELLED"} for r in result):
        return 2
    if isinstance(result, dict) and any(k.endswith("_error") for k in result): return 2
    return 0


def node_main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv == ["gui"]:
        from .gui import launch
        launch("node")
        return 0
    p = argparse.ArgumentParser(description="Virtuoso LAN Bridge 子端")
    p.add_argument("--state", type=Path, default=state_root() / "node")
    s = p.add_subparsers(dest="action", required=True)
    q = s.add_parser("template"); q.add_argument("output")
    q = s.add_parser("scan"); q.add_argument("host"); q.add_argument("--port", type=int, default=22)
    q = s.add_parser("start-vm"); q.add_argument("vmx"); q.add_argument("--vmrun")
    for name in ("bootstrap", "install", "card", "forward", "check", "detect", "start-virtuoso"):
        q = s.add_parser(name); q.add_argument("--config", required=True)
        if name in {"bootstrap", "install", "card", "forward"}: q.add_argument("--request", required=True)
        if name == "card": q.add_argument("--output", required=True); q.add_argument("--direct", action="store_true")
    a = p.parse_args(argv)
    if a.action == "template": result = node.defaults(); write_json(a.output, result)
    elif a.action == "scan": result = ssh.scan_key(a.host, a.port)
    elif a.action == "start-vm": result = vmware.start(a.vmx, a.vmrun)
    else:
        cfg = read_json(a.config)
        request = read_json(a.request) if hasattr(a, "request") else None
        if a.action == "bootstrap": result = {"script": str(node.bootstrap(cfg, request, a.state))}
        elif a.action == "install": result = node.interactive_install(cfg, request, a.state)
        elif a.action == "card": result = node.export_card(cfg, request, a.output, direct=a.direct)
        elif a.action == "forward": result = node.apply_forward(cfg, request, a.state)
        else:
            n = node.local_node(cfg, a.state)
            if a.action == "check": result = {"spectre": runner.remote(n, "doctor"), "bridge": ssh.skill(n)}
            elif a.action == "detect": result = node.detect_environment(n)
            else: result = node.start_virtuoso(cfg, n)
    _print(result)
    return 0


def entry(role):
    try:
        if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"): sys.stderr.reconfigure(encoding="utf-8")
        if sys.argv[1:] == ["--self-test-gui"]:
            from .gui import App
            app = App(role)
            app.root.withdraw()
            app.root.update_idletasks()
            app.root.destroy()
            _print({"gui": role, "widgets_created": True})
            return 0
        return controller_main() if role == "controller" else node_main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

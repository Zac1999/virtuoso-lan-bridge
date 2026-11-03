from __future__ import annotations

import json
import os
from pathlib import Path
import time

from . import ssh
from .common import executable, fingerprint, ipv4, node_id, port, public_key, read_json, state_root, write_json
from .runner import controller as runner


class Controller:
    def __init__(self, root=None):
        self.root = Path(root) if root else state_root() / "controller"
        self.root.mkdir(parents=True, exist_ok=True)
        self.key = self.root / "controller_ed25519"
        self.pool_path = self.root / "pool.json"
        self.lock = self.pool_path.with_suffix(".controller.lock")
        if not self.pool_path.exists():
            write_json(self.pool_path, {"schema_version": 1, "max_parallel": 2, "nodes": []}, exclusive=True)

    def idle(self):
        if self.lock.exists():
            raise RuntimeError("调度锁存在：可能仍有运行或未知任务。请先查询原批次并核实状态；不要重复派发。")

    def pairing(self, controller_ip, output=None):
        key = ssh.ensure_key(self.key)
        request = {"schema_version": 1, "kind": "controller-request", "controller_ip": ipv4(controller_ip),
                   "public_key": key, "fingerprint": fingerprint(key)}
        if output:
            write_json(output, request)
        return request

    def import_card(self, path):
        self.idle()
        card = read_json(path)
        if card.get("kind") != "node-card" or card.get("schema_version") != 1:
            raise ValueError("请选择子端导出的节点卡 JSON")
        key = ssh.ensure_key(self.key)
        if card["controller_fingerprint"] != fingerprint(key):
            raise ValueError("此节点卡是为另一把母端公钥生成的，请重新导出配对请求")
        node = card["node"]
        node_id(node["name"])
        port(node["bridge_port"])
        # Only schema-approved fields cross into a command configuration; do not accept executable/SSH overrides.
        allowed = {"name", "label", "host", "port", "user", "bridge_port", "spectre", "environment", "slots", "threads",
                   "root", "python", "spectre_args", "required_files"}
        if set(node) - allowed:
            raise ValueError("节点卡含未知配置字段")
        node = dict(node, enabled=True, ssh=executable("ssh"), identity_file=str(self.key))
        # Validate before creating any persistent connection configuration.
        temp = self.root / "card-validation.json"
        write_json(temp, {"schema_version": 1, "max_parallel": 1, "nodes": [node]})
        try:
            runner.load_config(temp)
        finally:
            temp.unlink()
        saved = read_json(self.pool_path)
        existing = next((n for n in saved["nodes"] if n["name"] == node["name"]), None)
        if existing and any(existing[k] != node[k] for k in ("host", "port", "user")):
            raise ValueError("节点ID已存在且入口不同；请使用新节点ID，保留旧运行身份")
        node["ssh_config"] = ssh.pinned_config(self.root / "nodes" / node["name"], self.key,
            node["host"], node["port"], node["user"], card["host_fingerprint"], card["host_public_key"])
        node["host_fingerprint"] = card["host_fingerprint"]
        # SSH must authenticate before a node is admitted to the pool.
        identity = ssh.command(node, "hostname; whoami", timeout=20).stdout.decode("utf-8", "replace").strip()
        saved["nodes"] = [n for n in saved["nodes"] if n["name"] != node["name"]] + [node]
        write_json(self.pool_path, saved)
        return {"state": "paired", "node": node["name"], "identity": identity, "host_fingerprint": card["host_fingerprint"]}

    def nodes(self):
        return read_json(self.pool_path)["nodes"]

    def node(self, name):
        return next(n for n in self.nodes() if n["name"] == name)

    def check(self, name):
        node = self.node(name)
        info = {"node": name, "label": node.get("label", name), "endpoint": f'{node["host"]}:{node["port"]}'}
        try:
            info["ssh"] = ssh.command(node, "hostname; whoami", timeout=20).stdout.decode().strip()
        except Exception as exc:
            return dict(info, ssh_error=str(exc))
        try:
            info["spectre"] = runner.remote(node, "doctor")
        except Exception as exc:
            info["spectre_error"] = str(exc)
        try:
            result = ssh.skill(node)
            if result["output"] != "3":
                raise RuntimeError("1+2 返回值不是3")
            info["bridge"] = result
        except Exception as exc:
            info["bridge_error"] = str(exc)
        return info

    def set_enabled(self, name, enabled):
        self.idle()
        cfg = read_json(self.pool_path)
        node = next(n for n in cfg["nodes"] if n["name"] == name)
        node["enabled"] = bool(enabled)
        write_json(self.pool_path, cfg)

    def run(self, jobs, batch, *, expected_config=None):
        cfg = runner.load_config(self.pool_path)
        if expected_config is not None and cfg != expected_config:
            raise RuntimeError("节点配置在检查后发生变化，请重新检查后启动。")
        requests = runner.prepare_jobs(jobs)
        if Path(batch).exists():
            raise ValueError("批次目录已存在，请选择新目录")
        if not any(n["enabled"] for n in cfg["nodes"]):
            raise ValueError("没有启用的计算节点")
        lock_data = {"pid": os.getpid(), "batch": str(Path(batch).resolve()), "time": time.time(), "scheduler_finished": False}
        with self.lock.open("x", encoding="utf-8") as f:
            json.dump(lock_data, f)
        rows = runner.schedule(cfg, requests, batch)
        write_json(self.lock, dict(lock_data, scheduler_finished=True))
        if all(row["state"] in runner.TERMINAL for row in rows):
            self.lock.unlink()
        return rows

    def reconcile(self, batch):
        rows = runner.refresh(batch)
        write_json(Path(batch) / "observed.json", rows)
        # Never unlock another running scheduler; only a scheduler that returned
        # after losing a response may be cleared after all assigned work terminates.
        if self.lock.exists():
            lock = read_json(self.lock)
            if (lock.get("scheduler_finished") and Path(lock["batch"]).resolve() == Path(batch).resolve()
                    and all(r["state"] in runner.TERMINAL or (r["state"] == "QUEUED" and "node" not in r) for r in rows)):
                self.lock.unlink()
        return rows

    def collect(self, batch, job_name, output, waveforms=True):
        batch = Path(batch)
        cfg, rows = read_json(batch / "config.snapshot.json"), read_json(batch / "state.json")
        row = next(r for r in rows if r["name"] == job_name)
        node = next(n for n in cfg["nodes"] if n["name"] == row["node"])
        result = runner.remote(node, "collect", job_id=row["job_id"], waveforms=waveforms)
        runner.save_artifacts(result, output, row["job_id"])
        return {"job": job_name, "state": result["state"], "files": len(result["files"]), "output": str(output)}

from __future__ import annotations

from contextlib import contextmanager
import csv
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from .common import (check_fingerprint, executable, fingerprint, hidden_options, host,
                     node_id, port, public_key, run, username)


def ensure_key(path):
    path = Path(path)
    pub = Path(str(path) + ".pub")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() != pub.exists():
        raise RuntimeError("密钥对不完整；请检查，程序不会覆盖已有密钥")
    if not path.exists():
        run([executable("ssh-keygen"), "-t", "ed25519", "-N", "", "-C", "virtuoso-lan-bridge", "-f", path])
        if os.name == "nt":
            identity = run(["whoami", "/user", "/fo", "csv", "/nh"]).stdout.decode("utf-8", "replace")
            sid = next(csv.reader(identity.strip().splitlines()))[1]
            run(["icacls", path, "/inheritance:r", "/grant:r", "*" + sid + ":(F)"])
        else:
            path.chmod(0o600)
    # Read test catches the Windows sandbox-owner failure seen during initial development.
    with path.open("rb") as f:
        f.read(1)
    return public_key(pub.read_text(encoding="ascii"))


def scan_key(address, ssh_port):
    p = run([executable("ssh-keyscan"), "-T", "5", "-p", str(port(ssh_port)), "-t", "ed25519", host(address)], timeout=15, check=False)
    keys = {public_key(" ".join(line.split()[1:3])) for line in p.stdout.decode().splitlines()
            if line and not line.startswith("#")}
    if len(keys) != 1:
        raise RuntimeError("未取得唯一的 SSH ED25519 主机公钥；请检查网络/端口。 " + p.stderr.decode(errors="replace")[-1000:])
    key = keys.pop()
    return {"key": key, "fingerprint": fingerprint(key)}


def pinned_config(directory, identity_file, address, ssh_port, user, expected, key=None):
    address, user, ssh_port = host(address), username(user), port(ssh_port)
    check_fingerprint(expected)
    found = scan_key(address, ssh_port)
    if found["fingerprint"] != expected:
        raise RuntimeError("SSH 主机指纹不一致，已停止连接：" + found["fingerprint"])
    if key is not None and public_key(key) != found["key"]:
        raise RuntimeError("节点卡中的主机公钥与实际端点不一致")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    alias = "vlb-" + directory.name
    known = directory / "known_hosts"
    content = alias + " " + found["key"] + "\n"
    if known.exists() and known.read_text() != content:
        raise RuntimeError("已保存的主机身份不同；请显式移除旧节点后重新配对")
    known.write_text(content, encoding="ascii")
    cfg = directory / "ssh_config"
    for path in (known, Path(identity_file)):
        if any(c in str(path) for c in ('"', '\n', '\r')):
            raise ValueError("SSH 路径含不支持的字符")
    cfg.write_text(
        f'Host *\n    HostName {address}\n    Port {ssh_port}\n    User {user}\n'
        f'    IdentityFile "{Path(identity_file).resolve().as_posix()}"\n'
        f'    UserKnownHostsFile "{known.resolve().as_posix()}"\n'
        f'    HostKeyAlias {alias}\n    HostKeyAlgorithms ssh-ed25519\n'
        '    GlobalKnownHostsFile none\n    IdentitiesOnly yes\n    StrictHostKeyChecking yes\n'
        '    BatchMode yes\n    ConnectTimeout 8\n    ServerAliveInterval 15\n    ServerAliveCountMax 2\n',
        encoding="utf-8")
    return str(cfg.resolve())


def args(node):
    return [executable("ssh"), "-F", str(node["ssh_config"]),
            "-p", str(port(node.get("port", 22))), username(node["user"]) + "@" + host(node["host"])]


def command(node, cmd, *, data=None, timeout=30):
    return run(args(node) + [cmd], data=data, timeout=timeout)


@contextmanager
def tunnel(node, remote_port):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        local_port = reserve.getsockname()[1]
    base = args(node)
    cmd = base[:-1] + ["-N", "-o", "ExitOnForwardFailure=yes", "-L",
                       f"127.0.0.1:{local_port}:127.0.0.1:{port(remote_port)}", base[-1]]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, **hidden_options())
        try:
            for _ in range(50):
                if proc.poll() is not None:
                    err.seek(0)
                    raise RuntimeError("SSH 隧道未启动：" + err.read().decode("utf-8", "replace")[-2000:])
                try:
                    with socket.create_connection(("127.0.0.1", local_port), timeout=.2):
                        pass
                    break
                except OSError:
                    time.sleep(.2)
            else:
                raise TimeoutError("SSH 本地隧道没有就绪")
            yield local_port
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def skill(node, expression="1+2", timeout=15):
    """RAMIC-compatible protocol over a pinned SSH tunnel. No automatic retries."""
    with tunnel(node, node["bridge_port"]) as local_port:
        with socket.create_connection(("127.0.0.1", local_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(json.dumps({"skill": expression, "timeout": timeout}).encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            chunks, size = [], 0
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    raise ValueError("SKILL 响应超过 4 MiB")
                chunks.append(chunk)
    raw = b"".join(chunks).decode("utf-8", "replace")
    if not raw.startswith("\x02"):
        raise RuntimeError("SKILL 未返回成功标记：" + raw.lstrip("\x15")[:2000])
    return {"status": "success", "output": raw[1:].strip(), "errors": []}

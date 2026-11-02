from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid


def state_root() -> Path:
    path = Path(os.environ.get("VLB_STATE_DIR", "")) if os.environ.get("VLB_STATE_DIR") else (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VirtuosoLANBridge"
        if os.name == "nt" else Path.home() / ".local/share/virtuoso-lan-bridge")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open("x", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        return
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def hidden_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def run(args, *, timeout=30, data=None, check=True):
    p = subprocess.run([str(x) for x in args], input=data, capture_output=True,
                       timeout=timeout, **hidden_options())
    if check and p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[-2500:] or p.stdout.decode("utf-8", "replace")[-2500:]
                           or "Command failed with exit " + str(p.returncode))
    return p


def executable(name):
    path = shutil.which(name)
    if not path and os.name == "nt" and name.startswith("ssh"):
        path = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH" / (name + ".exe"))
    if not path or not Path(path).is_file():
        raise RuntimeError(name + " 未安装或不在 PATH。Windows 请启用 OpenSSH 客户端。")
    return path


def host(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}", value):
        raise ValueError("无效的主机名或 IPv4 地址")
    return value


def ipv4(value):
    addr = ipaddress.IPv4Address(value)
    if addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_link_local:
        raise ValueError("请填写实际局域网/虚拟机 IPv4 地址")
    return str(addr)


def username(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("无效的 Linux 用户名")
    return value


def port(value):
    if isinstance(value, bool):
        raise ValueError("无效的端口")
    result = int(value)
    if not 1 <= result <= 65535:
        raise ValueError("端口必须为 1..65535")
    return result


def public_key(value):
    fields = value.strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519" or len(value) > 4096:
        raise ValueError("需要不含授权选项的 ED25519 公钥")
    blob = base64.b64decode(fields[1], validate=True)
    # RFC 4253: algorithm name + 32-byte public key, both SSH strings.
    if len(blob) != 51 or blob[:19] != b"\0\0\0\x0bssh-ed25519\0\0\0\x20":
        raise ValueError("无效的 ED25519 公钥编码")
    return fields[0] + " " + fields[1]


def fingerprint(value):
    encoded = public_key(value).split()[1]
    return "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(encoded)).digest()).decode().rstrip("=")


def check_fingerprint(value):
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", value):
        raise ValueError("需要 SHA256: 格式的 SSH 主机指纹")
    return value


def new_id():
    return "node-" + uuid.uuid4().hex[:12]


def node_id(value):
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", value):
        raise ValueError("无效的节点 ID")
    return value


def resource(name):
    return Path(__file__).resolve().parent / name

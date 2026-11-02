"""Local VMware lifecycle only; never powers off or resets a guest."""
import os
from pathlib import Path
import shutil

from .common import run


def find_vmrun():
    found = shutil.which("vmrun")
    if found:
        return found
    for name in ("ProgramFiles", "ProgramFiles(x86)"):
        p = Path(os.environ.get(name, "C:/Program Files")) / "VMware/VMware Workstation/vmrun.exe"
        if p.is_file():
            return str(p)
    return ""


def running(vmrun=None):
    exe = vmrun or find_vmrun()
    if not exe:
        raise RuntimeError("未找到 VMware vmrun；请在设置中指定 vmrun.exe")
    output = run([exe, "list"]).stdout.decode("utf-8", "replace")
    return [x.strip() for x in output.splitlines() if x.strip().lower().endswith(".vmx")]


def start(vmx, vmrun=None):
    path = Path(vmx).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".vmx":
        raise ValueError("请选择已有的 .vmx 文件")
    exe = vmrun or find_vmrun()
    active = running(exe)
    if any(os.path.normcase(os.path.abspath(x)) == os.path.normcase(str(path)) for x in active):
        return {"state": "already_running", "vmx": str(path)}
    run([exe, "start", str(path), "nogui"], timeout=120)
    if not any(os.path.normcase(os.path.abspath(x)) == os.path.normcase(str(path)) for x in running(exe)):
        raise RuntimeError("vmrun 已返回，但运行列表尚未包含目标 VM")
    return {"state": "running", "vmx": str(path)}


def guest_ip(vmx, vmrun=None):
    exe = vmrun or find_vmrun()
    result = run([exe, "getGuestIPAddress", str(Path(vmx).resolve(strict=True)), "-wait"], timeout=30)
    from .common import ipv4
    return ipv4(result.stdout.decode().strip())

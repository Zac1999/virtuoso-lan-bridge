from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

from . import ssh, vmware
from .common import (check_fingerprint, executable, fingerprint, host, ipv4, new_id, node_id,
                     port, public_key, read_json, resource, state_root, username, write_json)


def defaults():
    return {"schema_version": 1, "node_id": new_id(), "label": "计算节点", "vm_ip": "", "user": "master",
            "lan_ip": "", "public_port": 2222, "bridge_port": 65387, "vmx": "", "vmrun": vmware.find_vmrun(),
            "spectre": "spectre", "environment": {}, "slots": 1, "threads": 1,
            "existing_bridge": True, "autoload": False, "virtuoso_executable": "virtuoso",
            "design_directory": "", "display": ":0"}


def validate(cfg):
    node_id(cfg["node_id"])
    username(cfg["user"])
    ipv4(cfg["vm_ip"])
    ipv4(cfg["lan_ip"])
    port(cfg["public_port"])
    port(cfg["bridge_port"])
    if not isinstance(cfg["label"], str) or not 1 <= len(cfg["label"]) <= 100:
        raise ValueError("节点名称长度必须为1..100")
    if cfg.get("autoload") and cfg.get("existing_bridge", True):
        raise ValueError("使用已有桥接时，不应同时选择安装新的自动加载项")
    for name in ("slots", "threads"):
        if type(cfg[name]) is not int or not 1 <= cfg[name] <= 64:
            raise ValueError(name + " 必须为1..64")
    if not isinstance(cfg["spectre"], str) or not cfg["spectre"] or any(x in cfg["spectre"] for x in "\r\n\0"):
        raise ValueError("需要 Spectre 可执行文件路径或命令名")
    return cfg


def validate_request(request):
    if request.get("kind") != "controller-request" or request.get("schema_version") != 1:
        raise ValueError("请选择母电脑导出的配对请求 JSON")
    ipv4(request["controller_ip"])
    key = public_key(request["public_key"])
    if fingerprint(key) != request["fingerprint"]:
        raise ValueError("配对请求公钥指纹不一致")
    return request


def local_node(cfg, directory):
    validate(cfg)
    directory = Path(directory)
    ensure = ssh.ensure_key(directory / "node_ed25519")
    expected = check_fingerprint(cfg.get("host_fingerprint", ""))
    path = ssh.pinned_config(directory / "ssh", directory / "node_ed25519", cfg["vm_ip"], 22,
                             cfg["user"], expected, cfg.get("host_public_key"))
    return {"name": cfg["node_id"], "label": cfg["label"], "host": cfg["vm_ip"], "user": cfg["user"],
            "port": 22, "ssh_config": path, "identity_file": str(directory / "node_ed25519"),
            "ssh": executable("ssh"), "python": "python3", "spectre": cfg["spectre"],
            "environment": cfg.get("environment", {}), "root": cfg.get("remote_root", "/home/" + cfg["user"] + "/virtuoso_lan_runs"),
            "slots": cfg["slots"], "threads": cfg["threads"], "spectre_args": cfg.get("spectre_args", []),
            "required_files": cfg.get("required_files", []), "bridge_port": cfg["bridge_port"], "enabled": True}


def bootstrap(cfg, request, directory):
    """Create a self-contained Python3 guest installer; never contains private keys."""
    validate(cfg)
    validate_request(request)
    directory = Path(directory)
    node_public = ssh.ensure_key(directory / "node_ed25519")
    payload = {"user": cfg["user"], "keys": [public_key(request["public_key"]), node_public],
               "node_id": cfg["node_id"], "bridge_port": port(cfg["bridge_port"]),
               "install_bridge": not cfg.get("existing_bridge", True), "autoload": cfg.get("autoload", False)}
    if payload["install_bridge"]:
        raw = resource("vendor/ramic_bridge.il").read_text(encoding="utf-8")
        if raw.count("RBLocal = nil") != 1:
            raise RuntimeError("Vendored bridge source differs from expected binding declaration")
        # Keep upstream source verbatim in the distribution; derive a loopback-only runtime copy.
        payload["bridge_il"] = raw.replace("RBLocal = nil", "RBLocal = t", 1)
        payload["daemon_py"] = resource("vendor/ramic_bridge_daemon_3.py").read_text(encoding="utf-8")
        payload["entries"] = {role: resource("virtuoso/" + role + ".il").read_text(encoding="utf-8") for role in ("controller", "node")}
    source = '''#!/usr/bin/env python3
import base64,json,os,pathlib,pwd,time
P=json.loads(base64.b64decode(PAYLOAD))
if pwd.getpwuid(os.getuid()).pw_name != P['user']:
 raise SystemExit('Run this installer as '+P['user']+', without sudo.')
os.umask(0o077)
home=pathlib.Path.home();sshdir=home/'.ssh';auth=sshdir/'authorized_keys'
if sshdir.is_symlink() or auth.is_symlink(): raise SystemExit('Refusing symlinked SSH paths')
sshdir.mkdir(mode=0o700,exist_ok=True);sshdir.chmod(0o700)
old=auth.read_text() if auth.exists() else ''
lines=old.splitlines()
existing={' '.join(x.split()[:2]) for x in lines if x.strip() and not x.lstrip().startswith('#')}
new=[k for k in P['keys'] if k not in existing]
if new:
 with auth.open('a') as f:
  if old and not old.endswith('\\n'):f.write('\\n')
  f.write('\\n'.join(new)+'\\n');f.flush();os.fsync(f.fileno())
auth.chmod(0o600)
result={'keys_installed':len(new),'user':P['user'],'bridge_installed':False}
if P['install_bridge']:
 root=home/'.cache/virtuoso_lan_bridge'/P['node_id'];root.mkdir(parents=True,exist_ok=True)
 if root.is_symlink():raise SystemExit('Refusing symlinked runtime directory')
 def put(path,text):
  if path.is_symlink():raise SystemExit('Refusing symlink: '+str(path))
  if path.exists() and path.read_text()!=text:
   backup=path.with_name(path.name+'.backup-'+str(time.time_ns()));backup.write_bytes(path.read_bytes())
  path.write_text(text)
 put(root/'ramic_bridge.il',P['bridge_il'])
 put(root/'daemon.py',P['daemon_py'])
 setup='setShellEnvVar("RB_DAEMON_PATH" '+json.dumps(str(root/'daemon.py'))+')\\n'
 setup+='setShellEnvVar("RB_PYTHON_PATH" "/usr/bin/python3")\\n'
 setup+='setShellEnvVar("RB_PORT" "'+str(P['bridge_port'])+'")\\n'
 setup+='load('+json.dumps(str(root/'ramic_bridge.il'))+')\\n'
 put(root/'setup.il',setup)
 load='load('+json.dumps(str(root/'setup.il'))+')'
 put(root.parent/'active_setup.il',load+'\\n')
 for role,text in P['entries'].items():put(root/(role+'.il'),text)
 result.update(bridge_installed=True,ciw_command=load)
 result['ciw_role_commands']={role:'load('+json.dumps(str(root/(role+'.il')))+')' for role in P['entries']}
 if P['autoload']:
  cds=home/'.cdsinit'
  if cds.is_symlink():raise SystemExit('Refusing symlinked .cdsinit')
  before=cds.read_text() if cds.exists() else ''
  marker='; virtuoso-lan-bridge '+P['node_id']
  block=marker+'\\n'+load+'\\n'
  if marker in before and block not in before:raise SystemExit('Managed .cdsinit block differs; inspect before replacement')
  if block not in before:put(cds,before+'\\n'+block)
print(json.dumps(result))
'''.replace("PAYLOAD", repr(base64.b64encode(json.dumps(payload).encode()).decode()), 1)
    output = directory / "install-in-linux.py"
    output.write_text(source, encoding="utf-8", newline="\n")
    return output


def interactive_install(cfg, request, directory):
    """User-visible OpenSSH prompt. Passwords are handled by ssh.exe, never Python."""
    if os.name != "nt":
        raise RuntimeError("请将生成的 install-in-linux.py 放到虚拟机，并用 python3 执行")
    directory = Path(directory).resolve()
    script = bootstrap(cfg, request, directory)
    node = local_node(cfg, directory)
    # Fixed relative filenames; user inputs stay in the validated OpenSSH config.
    launcher = directory / "install-via-ssh.cmd"
    exe = executable("ssh")
    cfgpath = node["ssh_config"]
    if any(c in str(directory) + exe + cfgpath for c in ('%', '!', '\r', '\n')):
        raise ValueError("首次安装路径含不支持的 CMD 字符，请换用 Linux 初始化脚本")
    command = f'@echo off\r\nchcp 65001 >nul\r\ntype "%~dp0install-in-linux.py" | "{exe}" -F "{cfgpath}" -o BatchMode=no {cfg["user"]}@{cfg["vm_ip"]} python3 -\r\necho SSH exit code: %errorlevel%\r\npause\r\n'
    launcher.write_text(command, encoding="utf-8")
    subprocess.Popen(["cmd.exe", "/d", "/c", str(launcher)], creationflags=subprocess.CREATE_NEW_CONSOLE)
    return {"state": "interactive_window_opened", "note": "输入 Linux 密码只发生在 OpenSSH 窗口；安装成功后点击检测节点。"}


def export_card(cfg, request, path, *, direct=False):
    validate(cfg)
    validate_request(request)
    expected = check_fingerprint(cfg["host_fingerprint"])
    key = public_key(cfg["host_public_key"])
    if fingerprint(key) != expected:
        raise ValueError("保存的主机公钥和指纹不一致")
    node = {"name": cfg["node_id"], "label": cfg["label"], "host": cfg["vm_ip"] if direct else cfg["lan_ip"],
            "port": 22 if direct else port(cfg["public_port"]), "user": cfg["user"],
            "bridge_port": port(cfg["bridge_port"]), "spectre": cfg["spectre"],
            "environment": cfg.get("environment", {}), "slots": cfg["slots"], "threads": cfg["threads"],
            "root": cfg.get("remote_root", "/home/" + cfg["user"] + "/virtuoso_lan_runs"),
            "python": "python3", "spectre_args": cfg.get("spectre_args", []), "required_files": cfg.get("required_files", [])}
    card = {"kind": "node-card", "schema_version": 1, "node": node,
            "host_public_key": key, "host_fingerprint": expected,
            "controller_fingerprint": request["fingerprint"], "readiness": "verify_from_controller"}
    write_json(path, card)
    return card


def forward_payload(cfg, request, directory):
    validate(cfg)
    validate_request(request)
    dest = Path(directory) / "forward.json"
    write_json(dest, {"node_id": cfg["node_id"], "listen_ip": ipv4(cfg["lan_ip"]),
                      "listen_port": port(cfg["public_port"]), "vm_ip": ipv4(cfg["vm_ip"]),
                      "controller_ip": ipv4(request["controller_ip"])})
    return dest


def apply_forward(cfg, request, directory):
    if os.name != "nt":
        raise RuntimeError("Windows NAT 转发只在 Windows 节点端配置；Linux 服务器请直接导出22端口节点卡")
    import ctypes
    path = forward_payload(cfg, request, directory)
    args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(resource("windows_forward.ps1")), "-Config", str(path)]
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", subprocess.list2cmdline(args), None, 0)
    if result <= 32:
        raise RuntimeError("管理员授权未完成，转发未配置")
    return {"state": "admin_helper_started", "receipt": str(path.with_suffix(".result.json")),
            "note": "请查看转发结果；启动管理员进程不等于配置成功。"}


def detect_environment(node):
    script = '''import json,os,shutil,glob
p=shutil.which('spectre')
c=sorted(glob.glob('/export/SoftWare/Cadence/SPECTRE*/bin/spectre'))
v=shutil.which('virtuoso');vs=sorted(glob.glob('/export/SoftWare/Cadence/IC*/tools/dfII/bin/virtuoso')+glob.glob('/export/SoftWare/Cadence/IC*/bin/virtuoso'))
lic='/export/SoftWare/Cadence/license/license.dat'
print(json.dumps({'spectre':p or (c[-1] if c else None),'virtuoso':v or (vs[-1] if vs else None),'license_file':lic if os.path.isfile(lic) else None}))
'''
    return json.loads(ssh.command(node, "python3 -c " + shlex.quote(script)).stdout)


def start_virtuoso(cfg, node):
    exe = cfg.get("virtuoso_executable", "virtuoso")
    cwd = cfg.get("design_directory", "")
    if not cwd.startswith("/") or "\0" in cwd or "\0" in exe:
        raise ValueError("请先设置 Linux 设计工作目录的绝对路径")
    payload = {"exe": exe, "cwd": cwd, "display": cfg.get("display", ":0"), "environment": cfg.get("environment", {})}
    script = '''import json,os,pathlib,subprocess,sys,time
p=json.load(sys.stdin)
check=subprocess.run(['pgrep','-u',str(os.getuid()),'-x','virtuoso'],stdout=subprocess.PIPE)
if check.returncode==0:
 print(json.dumps({'state':'already_running','pids':check.stdout.decode().split()}));sys.exit(0)
folder=pathlib.Path.home()/'.cache/virtuoso_lan_bridge';folder.mkdir(parents=True,exist_ok=True)
env=dict(os.environ,**p['environment']);env['DISPLAY']=p['display']
with (folder/'virtuoso-launch.log').open('ab') as log:
 process=subprocess.Popen([p['exe']],cwd=p['cwd'],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
time.sleep(2)
print(json.dumps({'state':'process_started' if process.poll() is None else 'failed','pid':process.pid,'exitcode':process.poll(),'log':str(folder/'virtuoso-launch.log'),'bridge_verified':False}))
'''
    return json.loads(ssh.command(node, "python3 -c " + shlex.quote(script), data=json.dumps(payload).encode(), timeout=15).stdout)

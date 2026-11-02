import base64
import contextlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import types

import pytest

from virtuoso_lan_bridge import common, node, ssh, vmware
from virtuoso_lan_bridge.controller import Controller


KEY = "ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(range(32))).decode()
OTHER = "ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + b"x" * 32).decode()


def request():
    return dict(schema_version=1, kind="controller-request", controller_ip="192.0.2.10", public_key=KEY, fingerprint=common.fingerprint(KEY))


def config():
    return dict(node.defaults(), node_id="test-node", vm_ip="192.0.2.20", lan_ip="192.0.2.30", user="cadence",
                host_public_key=OTHER, host_fingerprint=common.fingerprint(OTHER))


@pytest.mark.parametrize("bad", ["127.0.0.1", "0.0.0.0", "224.0.0.1", "169.254.1.2", "192.0.2.10;calc", "foo"])
def test_invalid_firewall_addresses_rejected(bad):
    with pytest.raises(ValueError): node.validate_request(dict(request(), controller_ip=bad))


def test_pair_request_cannot_change_key_without_fingerprint():
    with pytest.raises(ValueError): node.validate_request(dict(request(), public_key=OTHER))


def test_authorized_keys_options_not_accepted_as_plain_key():
    with pytest.raises(ValueError): common.public_key('command="sh" ' + KEY)


def test_host_key_mismatch_writes_no_trust(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh, "scan_key", lambda *a: dict(key=OTHER, fingerprint=common.fingerprint(OTHER)))
    with pytest.raises(RuntimeError, match="指纹"):
        ssh.pinned_config(tmp_path / "ssh", tmp_path / "key", "192.0.2.30", 2222, "cadence", common.fingerprint(KEY))
    assert not (tmp_path / "ssh").exists()


def test_pinned_config_isolated_and_refuses_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh, "scan_key", lambda *a: dict(key=KEY, fingerprint=common.fingerprint(KEY)))
    args = (tmp_path / "ssh", tmp_path / "key", "192.0.2.30", 2222, "cadence", common.fingerprint(KEY))
    path = ssh.pinned_config(*args)
    text = Path(path).read_text()
    assert "StrictHostKeyChecking yes" in text and "GlobalKnownHostsFile none" in text
    (tmp_path / "ssh/known_hosts").write_text("old-server\n")
    with pytest.raises(RuntimeError): ssh.pinned_config(*args)
    assert (tmp_path / "ssh/known_hosts").read_text() == "old-server\n"


def test_node_card_has_only_public_material(tmp_path):
    card = node.export_card(config(), request(), tmp_path / "card.json")
    assert card["node"]["host"] == "192.0.2.30" and card["node"]["port"] == 2222
    assert card["controller_fingerprint"] == common.fingerprint(KEY)
    direct = node.export_card(config(), request(), tmp_path / "direct.json", direct=True)
    assert direct["node"]["host"] == "192.0.2.20" and direct["node"]["port"] == 22
    assert "identity_file" not in json.dumps(card)


def test_controller_import_wrong_recipient_never_connects(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh, "ensure_key", lambda p: OTHER)
    monkeypatch.setattr(ssh, "pinned_config", lambda *a: pytest.fail("must not connect"))
    node.export_card(config(), request(), tmp_path / "card.json")
    c = Controller(tmp_path / "controller")
    with pytest.raises(ValueError): c.import_card(tmp_path / "card.json")
    assert c.nodes() == []


def test_controller_requires_authenticated_ssh_before_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh, "ensure_key", lambda p: KEY)
    monkeypatch.setattr(ssh, "pinned_config", lambda *a: "isolated-config")
    def denied(*a, **kw): raise RuntimeError("Permission denied")
    monkeypatch.setattr(ssh, "command", denied)
    node.export_card(config(), request(), tmp_path / "card.json")
    c = Controller(tmp_path / "controller")
    with pytest.raises(RuntimeError): c.import_card(tmp_path / "card.json")
    assert c.nodes() == []


@pytest.mark.parametrize("new_bridge,autoload", [(False, False), (True, False), (True, True)])
def test_guest_installer_preserves_keys_and_is_idempotent(tmp_path, monkeypatch, new_bridge, autoload):
    monkeypatch.setattr(ssh, "ensure_key", lambda p: OTHER)
    cfg = dict(config(), existing_bridge=not new_bridge, autoload=autoload)
    stage = tmp_path / "stage"; stage.mkdir()
    script = node.bootstrap(cfg, request(), stage)
    compile(script.read_text(), str(script), "exec")
    # Execute the real generated code with an isolated home and synthetic pwd.
    fakehome = tmp_path / "guest"; fakehome.mkdir()
    (fakehome / ".ssh").mkdir()
    (fakehome / ".ssh/authorized_keys").write_text("# previous keys\n" + KEY + " original-comment\n")
    (fakehome / ".cdsinit").write_text("; original design startup\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fakehome))
    monkeypatch.setitem(sys.modules, "pwd", types.SimpleNamespace(getpwuid=lambda uid: types.SimpleNamespace(pw_name="cadence")))
    monkeypatch.setattr(node.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(node.os, "umask", lambda x: 0)
    for _ in range(2): exec(compile(script.read_text(), str(script), "exec"), {})
    keys = (fakehome / ".ssh/authorized_keys").read_text()
    assert keys.count(KEY) == 1 and keys.count(OTHER) == 1 and "# previous keys" in keys
    cds = (fakehome / ".cdsinit").read_text()
    assert cds.startswith("; original design startup\n")
    assert ("load(" in cds) == autoload
    if new_bridge:
        root = fakehome / ".cache/virtuoso_lan_bridge/test-node"
        assert "RBLocal = t" in (root / "ramic_bridge.il").read_text()
        assert 'RB_PORT" "65387' in (root / "setup.il").read_text()
    else: assert not (fakehome / ".cache").exists()
    if autoload: assert len(list(fakehome.glob(".cdsinit.backup-*"))) == 1


def test_vm_start_is_idempotent(tmp_path, monkeypatch):
    vmx = tmp_path / "machine.vmx"; vmx.touch()
    calls = []
    monkeypatch.setattr(vmware, "run", lambda args, **kw: calls.append(args) or types.SimpleNamespace(stdout=("Total running VMs: 1\n" + str(vmx)).encode()))
    assert vmware.start(vmx, "vmrun")["state"] == "already_running"
    assert calls == [["vmrun", "list"]]


@pytest.mark.parametrize("raw,success", [(b"\x023", True), (b"3", False), (b"\x15bad skill", False)])
def test_bridge_protocol_requires_success_marker(monkeypatch, raw, success):
    server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen()
    remote_port = server.getsockname()[1]
    @contextlib.contextmanager
    def tunnel(*a): yield remote_port
    monkeypatch.setattr(ssh, "tunnel", tunnel)
    def reply():
        with server:
            conn, _ = server.accept()
            with conn:
                data = b""
                while True:
                    chunk = conn.recv(1024)
                    if not chunk: break
                    data += chunk
                assert json.loads(data)["skill"] == "1+2"
                conn.sendall(raw)
    thread = threading.Thread(target=reply); thread.start()
    try:
        if success: assert ssh.skill({"bridge_port": 1234})["output"] == "3"
        else:
            with pytest.raises(RuntimeError): ssh.skill({"bridge_port": 1234})
    finally: thread.join(timeout=3)


def test_reconcile_releases_only_finished_scheduler(tmp_path, monkeypatch):
    from virtuoso_lan_bridge import controller
    c = Controller(tmp_path / "control"); batch = tmp_path / "batch"; batch.mkdir()
    rows = [dict(name="j", node="n", state="SUCCEEDED"), dict(name="q", state="QUEUED")]
    monkeypatch.setattr(controller.runner, "refresh", lambda p: rows)
    common.write_json(c.lock, dict(batch=str(batch), scheduler_finished=False))
    c.reconcile(batch); assert c.lock.exists()
    common.write_json(c.lock, dict(batch=str(batch), scheduler_finished=True))
    c.reconcile(batch); assert not c.lock.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell parser on Windows")
def test_windows_forward_script_parses():
    path = common.resource("windows_forward.ps1").as_posix()
    command = "$t=$null; $e=$null; [System.Management.Automation.Language.Parser]::ParseFile('" + path + "',[ref]$t,[ref]$e) | Out-Null; if($e.Count){$e | Out-String; exit 1}"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr

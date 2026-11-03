# Virtuoso LAN Bridge

[简体中文](README.md) | **English**

Run one application on each of two Windows computers to use both Linux VMs for Cadence simulation.
You can also add Linux servers that are directly reachable over SSH.

| Application | Responsibilities |
| --- | --- |
| **Controller** | Manage nodes, start the local VMware VM, verify SSH/Bridge connections, dispatch Spectre netlist jobs, and collect logs and waveforms |
| **Node** | Start its local VMware VM, install the controller's public key, configure restricted SSH forwarding, connect to or install a Virtuoso Bridge, and export a node card |

Download the appropriate Windows x64 ZIP from [Releases](https://github.com/jianshengchuanchaun/virtuoso-lan-bridge/releases). **Extract the entire archive before running the EXE.** Keep the accompanying `_internal` directory. The Windows packages include the Python runtime.

This independent connection and scheduling tool is compatible with [virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) and [virtuoso-wiki-bridge](https://github.com/jianshengchuanchaun/virtuoso-wiki-bridge). You can reuse an existing Bridge installation.

The desktop interface uses Chinese labels. This guide includes the relevant labels alongside their English meanings so you can follow the same workflow. Documentation language selection does not change the application's interface language; the quickstart report displays both languages.

## Features

- Each computer starts its own existing VMware VM. A running VM is recognized without starting another instance; the application does not power off or reset VMs.
- Connections use SSH key authentication and verified host-key fingerprints. Bridge requests travel through temporary SSH tunnels, which close after use.
- In NAT mode, the node forwards one SSH port on its specified Windows LAN IP. The added firewall rule restricts the source to the controller's IP. Direct connections to bridged VMs and Linux servers are also supported.
- Reuse an existing RAMIC Bridge, or install the bundled MIT-licensed Bridge. The installer supplies a CIW `load(...)` command and optional `.cdsinit` autoload configuration.
- Independent netlist jobs are distributed according to node slots and the global concurrency limit. Jobs have separate directories, timeouts, and status logs. An uncertain remote response stops new dispatches and is not retried automatically.
- A successful simulation requires both a successful process exit and confirmation in the native Spectre log. Returned artifacts are checked using SHA-256.
- Both EXEs expose a CLI with JSON output for agent integration. The GUI does not depend on a particular agent platform.

**Acceleration scope:** independent corners, parameter points, and circuits can run across multiple nodes. A single ordinary transient analysis is not automatically split across two computers. Per-job multithreading depends on the Spectre version, simulation mode, and license. No fixed speedup is promised. A successful software probe does not establish that a simulation license can be checked out.

## Prerequisites

The Windows computers must be powered on and have VMware Workstation and OpenSSH Client installed. Each Linux VM needs OpenSSH Server, Python 3, Cadence/Spectre, the required PDKs, and a valid license.
For graphical Virtuoso, log in to the Linux desktop and configure the correct DISPLAY and design working directory. VMware Tools can help retrieve the VM's IP; otherwise, run `hostname -I` in Linux and enter the address manually.

The application starts existing `.vmx` files. It does not install operating systems, wake powered-off physical computers, or create VMs.

## First connection: controller → node → controller

1. On the **controller**, open **Compute nodes / servers** (`计算节点 / 服务器`), enter the controller computer's physical LAN IPv4 address, and export `controller-request.json`. Transfer this file to the node computer.
2. On the **node**, select the `.vmx` file and click **Start local VM / retrieve IP** (`启动本机 VM / 获取 IP`). Enter the Linux username, VM IP, and the node computer's Windows LAN IP. The default external SSH port for NAT is 2222. The Bridge port must match the service's actual listening port inside the VM.
3. Click **Verify SSH fingerprint** (`核对 SSH 指纹`). In a Linux terminal, run `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256` and compare its fingerprint with the application before confirming. Obtaining a public key through a network scan alone does not verify the host's identity.
4. Import the controller request and verify its public-key fingerprint. Click **Install public keys / Bridge** (`安装公钥 / Bridge`) and enter the Linux password once in the OpenSSH terminal that opens. The application does not save the password. Alternatively, generate `install-in-linux.py`, copy it to Linux, and run `python3 install-in-linux.py` as the intended ordinary user, without sudo.
5. If you already have a working Bridge, leave **Use the existing working Bridge** (`使用已经正常工作的 Bridge`) selected. Clear it for a new installation; `.cdsinit` autoload is optional. For a Virtuoso session that is already open, execute the installer's `ciw_command` once in its CIW.
6. For NAT, click **Configure NAT entry** (`配置 NAT 入口`), complete Windows administrator authorization, and then click **View forwarding result** (`查看转发结果`). The result must be `CONFIGURED`. If the controller can reach the Linux IP directly, select direct connection and skip forwarding.
7. In **Simulation / new Bridge** (`仿真 / 新建 Bridge`), configure the Spectre path, license environment, and concurrency limits. **Detect Cadence paths** (`探测 Cadence 路径`) can help populate paths. Click **Check node** (`检测节点`) and inspect SSH, Spectre, and Bridge results separately.
8. Export `node-card.json` and transfer it back to the controller. Importing the card verifies the SSH host key again and performs an actual login. Then click **Check selected node** (`检测选中节点`) and confirm that `1+2` returns `3`.

**Adding the controller computer's own VM:** follow the same workflow in its **Local VM** (`本机虚拟机`) tab. Import its own pairing request, select direct connection, export the node card, and import that card into the controller. Once both VMs have been added, choose which nodes participate in computation.

**Linux servers:** Spectre jobs do not require VMware or a graphical Bridge. On a Windows node application that can reach the server, enter the server's IP and username, select direct connection, and follow the same pairing workflow. Alternatively, use the source-based `vlb-node` CLI. Without a graphical session, a failed Bridge check does not prevent independent Spectre jobs, but the Virtuoso GUI is not connected.

For later use, normally start each computer's VM, start Virtuoso if needed, and check the nodes from the controller. Update settings and forwarding when IP addresses change. An existing node ID cannot silently switch to a different SSH endpoint.

## Double-click desktop workflow

After pairing, v0.2.0 can save a workflow profile and install desktop shortcuts. The controller checks every required node, dispatches independent Spectre jobs, collects hash-verified artifacts, and opens a bilingual HTML report. Every required node must pass; an offline node does not cause a silent fallback to one machine.

1. Copy `examples/quickstart.profile.json` into your configuration directory. Set the paired `controller_state`, `required_nodes`, job manifest `jobs`, and output parent `results_root`. Relative paths resolve from the profile directory. Enabled pool nodes must exactly match `required_nodes`; concurrency and job counts must each be at least the number of required nodes.
2. Optionally set `local_vmx` and `local_node` to start or reuse the controller computer's VM. The other physical computer must already be powered on, with its VM and SSH entry available. Graphical Virtuoso / Bridge must already be running; the launcher does not guess design directories or open a second design session.
3. In the extracted controller directory, run once:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_desktop.ps1 -Profile C:\MyConfig\quickstart.json
```

The installer creates **Virtuoso 双机联动仿真** (run saved multi-node jobs), **Virtuoso 连接检查** (connection check only), and **Virtuoso 仿真控制台** (the paired GUI). Connection check mode neither starts a VM nor submits jobs. Shortcuts reuse existing SSH identities and trusted host keys; existing shortcuts with the same names are backed up.

The default RC manifest verifies dispatch and collection; it **does not read the circuit currently open in ADE**. Export netlist bundles and change `jobs` for your designs. Each launch creates a fresh result directory. An OS lock prevents repeated double-click submissions. An unresolved previous job retains the scheduler lock and requires inspection of the original batch. By default, existing Spectre/HSPICE processes also prevent dispatch; that check is a startup snapshot, not a cross-application resource scheduler.

```powershell
.\Virtuoso-LAN-Controller.exe quickstart --profile C:\MyConfig\quickstart.json --check-only --open-report
.\Virtuoso-LAN-Controller.exe quickstart --profile C:\MyConfig\quickstart.json --open-report
```

## Loading the entry scripts in both Virtuoso sessions

The download packages include two SKILL entry scripts for the **Virtuoso CIW**:

- Controller computer's Virtuoso: `virtuoso/controller.il`
- Node computer's Virtuoso: `virtuoso/node.il`

Copy the appropriate file into each Linux VM, then load its actual path in the CIW. For example:

```lisp
; In the controller computer's Virtuoso
load("/home/cadence/virtuoso/controller.il")
; In the other computer's Virtuoso CIW
load("/home/cadence/virtuoso/node.il")
```

If a Bridge is already running, the entry script reuses it. A new Bridge installation writes `~/.cache/virtuoso_lan_bridge/active_setup.il`; its result also provides the exact `ciw_role_commands` paths. To use another Bridge installation that is not running yet, set its real setup path first:

```lisp
setShellEnvVar("VLB_BRIDGE_SETUP" "/absolute/path/to/virtuoso_setup.il")
```

The SKILL entry scripts attach the current Virtuoso sessions. The Windows controller and node applications handle VM startup, SSH, pairing, and scheduling. After loading a script, verify connectivity by executing `1+2` from the controller and checking that it returns `3`.

## Running the example simulations

Each download includes `examples/jobs.json` and two RC netlists that do not require a PDK. After checking the compute nodes, select this manifest in **Simulation jobs** (`仿真任务`), choose a batch directory that **does not exist yet**, and start dispatching. After completion, collect the results into another parent directory.

For real circuits, first export Spectre netlists from ADE and place the netlist and small input files in separate bundles. Install PDKs on the nodes beforehand; model/include paths in the netlists must be valid on the target nodes. The application does not copy entire OA libraries, automatically export ADE sessions, or modify circuits.

Each input bundle is limited to 32 MiB / 5000 files, and result collection is limited to 64 MiB / 5000 files. For larger waveforms, use the CLI's `--logs-only` option and retrieve the waveforms separately through SSH/SCP.

```powershell
# The controller EXE accepts CLI arguments and returns JSON
.\Virtuoso-LAN-Controller.exe pair --ip 192.0.2.10 --output controller-request.json
.\Virtuoso-LAN-Controller.exe import-node node-card.json
.\Virtuoso-LAN-Controller.exe nodes
.\Virtuoso-LAN-Controller.exe check NODE_ID
.\Virtuoso-LAN-Controller.exe skill NODE_ID '1+2'
.\Virtuoso-LAN-Controller.exe run --jobs examples\jobs.json --batch D:\runs\batch-001
.\Virtuoso-LAN-Controller.exe status D:\runs\batch-001
.\Virtuoso-LAN-Controller.exe fetch D:\runs\batch-001 rc-fast D:\results\rc-fast
.\Virtuoso-LAN-Controller.exe cancel D:\runs\batch-001 rc-fast

# Node help and configuration; also supports bootstrap/install/card/forward/check/detect/start-virtuoso
.\Virtuoso-LAN-Node.exe --help
.\Virtuoso-LAN-Node.exe template node.json
.\Virtuoso-LAN-Node.exe scan 192.0.2.20
```

The `192.0.2.*` addresses above are documentation placeholders. Replace them with real addresses. Advanced users can configure `environment`, `spectre_args`, `required_files`, and `remote_root` in the node JSON, then export and import the node card again.

## State and recovery

User state and keys are stored under `%LOCALAPPDATA%\VirtuosoLANBridge`. Set `VLB_STATE_DIR` to use a separate directory. Runtime keys are not stored in the application directory. Pairing requests and node cards contain public keys, not passwords or private keys, but they include network addresses and should only be exchanged with your intended controller and nodes.

`pool.json` stores the node configuration. Each batch directory contains `config.snapshot.json`, `requests.json`, `state.json`, and `ledger.jsonl`. A status query writes a separate `observed.json`; it does not resubmit jobs.

- `SUCCEEDED`: process exit code 0 and a native Spectre summary confirming 0 errors.
- `UNKNOWN`: the connection was lost or the job's state could not be confirmed. **This does not mean the job has stopped.**
- `QUEUED`: not dispatched yet. A recovery query does not dispatch it automatically.
- `FAILED` / `TIMED_OUT` / `CANCELLED` / `REJECTED`: terminal states; logs remain available for inspection.

Normal scheduler completion releases its lock. If dispatch stopped because of an UNKNOWN outcome, a later query releases the lock only after confirming that all assigned jobs have terminated. If the controller was forcibly closed, the lock remains. First confirm that the original controller process has exited and all assigned jobs in the original batch have terminated; then back up and move aside `pool.controller.lock`. Do not simply rerun the same batch.

Windows forwarding does not overwrite an existing mapping to a different target or disable the firewall. To remove the configuration, use the exact endpoint and rule name recorded in the local `forward.result.json`; do not delete unrelated rules. Existing broad firewall rules remain part of the system's prior configuration. Adding a restricted rule does not narrow those other rules automatically.

## Development and verification

Python 3.10+ is required for the applications. Runtime code uses only the standard library, as does the Linux worker.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pytest pyinstaller
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\build_windows.py
```

The build produces two independent onedir ZIPs, each containing an EXE, `_internal`, examples, and license notices. Build Windows binaries on Windows. CI runs tests on Windows and Linux, and builds both applications on Windows.

See the [English verification report](VALIDATION.md) for tested behavior and verification limits, or the [Chinese report](VALIDATION.zh-CN.md).

[`NOTICE.md`](NOTICE.md) records the compatible Bridge's upstream source and license. The project does not distribute commercial software, PDKs, license files, private designs, or SSH private keys.

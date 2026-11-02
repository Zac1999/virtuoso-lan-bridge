# Virtuoso LAN Bridge

两台 Windows 电脑，各运行一个程序，让本机 VM 与另一台电脑的 VM 一起参与 Cadence 仿真。
也可添加可直接 SSH 访问的 Linux 服务器。

| 下载与运行 | 职责 |
| --- | --- |
| **Controller / 母端** | 管理节点，启动本机 VMware VM，验证 SSH/Bridge，分发 Spectre 网表任务，取回日志和波形 |
| **Node / 子端** | 启动本机 VMware VM，配对母端公钥，设置受限 SSH 转发，连接或安装 Virtuoso Bridge，导出节点卡 |

从 [Releases](https://github.com/jianshengchuanchaun/virtuoso-lan-bridge/releases) 下载各自的 Windows x64 ZIP，**先完整解压，再运行其中的 EXE**。保留旁边的 `_internal` 文件夹。Windows 包已带 Python 运行时。

这是独立的连接与调度工具，兼容既有 [virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) / [virtuoso-wiki-bridge](https://github.com/jianshengchuanchaun/virtuoso-wiki-bridge)。已有 Bridge 可直接复用，无需替换原项目。

## 能做什么

- 两台电脑分别启动各自已有的 VMware 虚拟机，已运行时不会再启动，也不会关机或重置 VM。
- 通过 SSH 密钥认证和核对过的主机指纹建立连接。Bridge 用临时 SSH 隧道访问；隧道结束后关闭。
- NAT 模式只在子电脑指定的 LAN IP 上转发一个 SSH 端口，防火墙来源限定为母电脑 IP；支持直接连接桥接 VM / Linux 服务器。
- 兼容已有 RAMIC Bridge；也可安装随包附带的 MIT Bridge，生成 CIW `load(...)` 命令和可选 `.cdsinit` 自动加载。
- 独立网表任务按节点 slot 和总并行数分发。任务有独立目录、超时、状态日志；不明确的远端响应会停止新派发，不自动重试。
- 仿真成功需要进程退出码与 Spectre 原生日志共同确认。结果回传按 SHA-256 校验。
- EXE 附带命令行 JSON 接口，供智能体调用；图形界面不依赖某个特定智能体平台。

**加速的边界：**多 corner、参数扫描、多电路等独立任务可在多节点并行。单个普通 transient 不会自动拆到两台电脑；每个任务的多线程能力取决于 Spectre 版本、模式和许可证。没有承诺固定加速倍数。软件探测通过也不等于许可证已经通过仿真验证。

## 运行前准备

Windows 电脑已开机并安装 VMware Workstation 和 OpenSSH Client。Linux VM 已有 OpenSSH Server、Python 3、Cadence/Spectre、相应 PDK 与有效许可证。
使用图形 Virtuoso 时，Linux 桌面应已登录，配置正确的 DISPLAY 和设计工作目录。VMware Tools 可帮助读取 VM IP；未安装时可用 `hostname -I` 手动填写。

本程序负责启动已有 `.vmx`，不安装操作系统，不唤醒关机的物理电脑，不创建 VM。

## 首次连接：母端 → 子端 → 母端

1. **母端**打开「计算节点 / 服务器」，填写自己的物理局域网 IPv4，导出 `controller-request.json`。把这个文件交给子端。
2. **子端**填写 `.vmx`，点击「启动本机 VM / 获取 IP」。填写 Linux 用户名、VM IP、本机 Windows LAN IP。NAT 默认外部 SSH 端口是 2222；Bridge 端口必须与已有服务实际监听端口一致。
3. 点击「核对 SSH 指纹」。在 Linux 终端执行 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256`，与程序显示值逐字核对后确认。扫描到公钥本身不代表身份已验证。
4. 导入母端请求，核对母端公钥指纹。点击「安装公钥 / Bridge」，在 OpenSSH 弹出的终端输入一次 Linux 密码。密码不会写进本程序配置。也可生成 `install-in-linux.py`，复制到 Linux 后以目标普通用户执行 `python3 install-in-linux.py`；不要用 sudo。
5. 已有可用 Bridge 时保留「使用已经正常工作的 Bridge」。新装时取消该项；可选 `.cdsinit` 自动加载。安装输出的 `ciw_command` 需在已经打开的 Virtuoso CIW 中执行一次。
6. NAT 模式点击「配置 NAT 入口」，完成 Windows 管理员授权，再点击「查看转发结果」。结果必须是 `CONFIGURED`。若母端能直接访问 Linux IP，勾选直连，不需要转发。
7. 在「仿真 / 新建 Bridge」填写 Spectre 路径、许可证环境及并发限制，可用「探测 Cadence 路径」辅助填写。点击「检测节点」，分别查看 SSH、Spectre、Bridge 结果。
8. 导出 `node-card.json`，交回母端。母端导入时会再次检查 SSH 主机公钥和实际登录，再点击「检测选中节点」确认 `1+2` 返回 `3`。

**加入母电脑自己的 VM：**母端「本机虚拟机」页采用同一流程，导入自己的配对请求，勾选直连，导出的节点卡再导入母端。两台 VM 均成功加入后即可选择参与计算。

**Linux 服务器：**不需要 VMware 或图形 Bridge 就能运行 Spectre。可在能连到服务器的 Windows 子端填服务器 IP、用户，选择直连，完成同样配对；也可从源码使用 `vlb-node` CLI。无图形会话时 Bridge 检测报错不影响独立 Spectre 任务，但不能声称已连接 Virtuoso GUI。

以后通常只需两端各点击启动 VM、必要时启动 Virtuoso，再在母端检测节点。地址变更后须更新配置与转发，不能依赖过期的 IP。相同节点 ID 不允许悄悄换成另一个 SSH 入口。

## 在两台 Virtuoso 中加载

下载包还包含两个直接供 **Virtuoso CIW** 加载的 SKILL 入口：

- 母电脑的 Virtuoso：`virtuoso/controller.il`
- 子电脑的 Virtuoso：`virtuoso/node.il`

将对应文件复制到各自 Linux 虚拟机，在 CIW 执行实际路径，例如：

```lisp
; 母电脑的 Virtuoso
load("/home/cadence/virtuoso/controller.il")
; 子电脑的 Virtuoso（在另一台的 CIW 执行）
load("/home/cadence/virtuoso/node.il")
```

若已有 Bridge 正在运行，入口会复用它。新安装的 Bridge 会自动写入 `~/.cache/virtuoso_lan_bridge/active_setup.il`，安装结果也会给出 `ciw_role_commands` 的准确加载路径。若使用另一套尚未运行的 Bridge，可先设置其真实 setup 路径：

```lisp
setShellEnvVar("VLB_BRIDGE_SETUP" "/absolute/path/to/virtuoso_setup.il")
```

SKILL 入口负责接入当前 Virtuoso 会话；Windows 母端 / 子端负责 VM 生命周期、SSH、配对和调度。加载成功后仍以母端实际执行 `1+2` 返回 `3` 作为连通验证。

## 运行示例仿真

下载包附带 `examples/jobs.json` 和两个无 PDK 依赖的 RC 网表。母端先确认计算节点可用，在「仿真任务」选择该清单，指定一个**尚不存在**的批次目录，点击开始分发。完成后取回全部结果到另一个父目录。

实际电路需先由 ADE 导出 Spectre 网表，将网表及小型输入文件放进独立 bundle。PDK 在节点上预先安装，网表中的 model/include 路径应在目标节点有效。程序不复制整个 OA library，不自动导出 ADE 会话，也不修改电路。

每个 bundle 限 32 MiB / 5000 文件，结果取回限 64 MiB / 5000 文件。波形较大时 CLI 可加 `--logs-only`，再自行用 SSH/SCP 取回大波形。

```powershell
# 母端 EXE 也接受 CLI 参数，结果为 JSON
.\Virtuoso-LAN-Controller.exe pair --ip 192.0.2.10 --output controller-request.json
.\Virtuoso-LAN-Controller.exe import-node node-card.json
.\Virtuoso-LAN-Controller.exe nodes
.\Virtuoso-LAN-Controller.exe check NODE_ID
.\Virtuoso-LAN-Controller.exe skill NODE_ID '1+2'
.\Virtuoso-LAN-Controller.exe run --jobs examples\jobs.json --batch D:\runs\batch-001
.\Virtuoso-LAN-Controller.exe status D:\runs\batch-001
.\Virtuoso-LAN-Controller.exe fetch D:\runs\batch-001 rc-fast D:\results\rc-fast
.\Virtuoso-LAN-Controller.exe cancel D:\runs\batch-001 rc-fast

# 子端：查看参数、生成配置；也支持 bootstrap/install/card/forward/check/detect/start-virtuoso
.\Virtuoso-LAN-Node.exe --help
.\Virtuoso-LAN-Node.exe template node.json
.\Virtuoso-LAN-Node.exe scan 192.0.2.20
```

以上 `192.0.2.*` 为文档占位地址，必须换成自己的实际地址。高级用户可在节点 JSON 中配置 `environment`、`spectre_args`、`required_files`、`remote_root`，随后重新导出、导入节点卡。

## 状态与恢复

用户状态与密钥保存在 `%LOCALAPPDATA%\VirtuosoLANBridge`；可用环境变量 `VLB_STATE_DIR` 指定独立目录。程序目录不保存运行密钥。配对请求与节点卡只含公钥，不含密码或私钥，但它们含内网地址，应只交给自己的控制端。

`pool.json` 保存节点，批次目录保存 `config.snapshot.json`、`requests.json`、`state.json`、`ledger.jsonl`。查询另存 `observed.json`，不会重提任务。

- `SUCCEEDED`：进程退出 0，Spectre 日志确认 0 errors。
- `UNKNOWN`：连接丢失或状态无法证实，**不代表任务已经停止**。
- `QUEUED`：尚未派发；恢复查询不会自动派发它。
- `FAILED` / `TIMED_OUT` / `CANCELLED` / `REJECTED`：终态，保留日志供检查。

正常调度结束会释放调度锁。若因 UNKNOWN 停止，后续查询确认所有已分配任务终止后才释放。若控制端被强制结束，锁保留；必须先核实原控制进程已退出、原批次所有已分配任务均结束，再备份并移走 `pool.controller.lock`。不要直接重跑同一批次。

Windows 转发不覆盖已有不同目标的映射，也不关闭防火墙。需要移除时按本机 `forward.result.json` 的精确入口与规则名操作；不要批量删除其它规则。已有宽泛防火墙规则属于系统原配置，本工具新增的限定规则不能使那些旧规则自动收窄。

## 源码开发与验证

Python 3.10+，运行时仅使用标准库；Linux worker 也仅用标准库。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pytest pyinstaller
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\build_windows.py
```

构建输出为两个独立 onedir ZIP，每个包含 EXE、`_internal`、示例和许可证。打包必须在 Windows 上运行。CI 在 Windows/Linux 上测试，并在 Windows 上构建两个程序。

`NOTICE.md` 记录兼容 Bridge 的上游来源与许可证。项目没有附带商业软件、PDK、许可证文件、私人设计或 SSH 私钥。

## English overview

Two Windows applications share one Python package: a controller manages compute nodes and dispatches independent Spectre netlist jobs; a node app starts its local VMware VM, bootstraps SSH public keys, optionally installs a loopback-only RAMIC Bridge, and configures source-restricted Windows SSH forwarding. Direct Linux servers are supported. Runtime state stays outside the application directory. Pin host keys before pairing. An ambiguous submission is never retried automatically. This is not a transparent multi-host solver for one transient analysis, nor an ADE/OA project synchronizer.

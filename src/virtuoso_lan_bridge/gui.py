"""Two native Windows entry points; blocking work never runs on the Tk thread."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__, node, ssh, vmware
from .common import read_json, state_root, write_json
from .controller import Controller
from .runner import controller as runner


class App:
    def __init__(self, role, controller_root=None):
        self.root = tk.Tk()
        self.root.title(f"Virtuoso LAN Bridge {__version__} · " + ("母端 / Controller" if role == "controller" else "子端 / Node"))
        self.root.geometry("1060x850")
        self.root.minsize(880, 730)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.queue = queue.Queue()
        self.busy = False
        self.role = role
        self.controller = Controller(controller_root) if role == "controller" else None
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TButton", padding=(8, 5))
        style.configure("TLabel", padding=(2, 2))
        head = ttk.Frame(self.root, padding=12)
        head.pack(fill="x")
        ttk.Label(head, text="Virtuoso LAN Bridge", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        title = "母端：配对计算节点、启动本机虚拟机、分发 Spectre 任务。" if self.controller else "子端：启动本机虚拟机、安装公钥、提供受限 SSH 入口。"
        ttk.Label(head, text=title).pack(anchor="w")
        book = ttk.Notebook(self.root)
        book.pack(fill="both", expand=True, padx=12)
        if self.controller:
            parent = ttk.Frame(book, padding=10)
            book.add(parent, text="计算节点 / 服务器")
            self.make_controller(parent)
        local = ttk.Frame(book, padding=10)
        book.add(local, text="本机虚拟机" if self.controller else "节点设置")
        self.local = NodePanel(self, local, state_root() / ("local-node" if self.controller else "node"))
        if self.controller:
            tasks = ttk.Frame(book, padding=10)
            book.add(tasks, text="仿真任务")
            self.make_tasks(tasks)
        status = ttk.Frame(self.root, padding=(12, 6))
        status.pack(fill="x")
        self.status = tk.StringVar(value="就绪。首次配对需要在 Linux 中核对 SSH 主机指纹。")
        ttk.Label(status, textvariable=self.status).pack(side="left")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=100)
        self.progress.pack(side="right")
        self.output = tk.Text(self.root, height=11, wrap="word", font=("Consolas", 10), state="disabled")
        self.output.pack(fill="both", padx=12, pady=(0, 12))
        self.root.after(100, self.drain)

    def log(self, value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        self.output.configure(state="normal")
        self.output.insert("end", text + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def work(self, title, action, done=None):
        if self.busy:
            messagebox.showinfo("正在处理", "请等待当前操作结束。仿真运行期间可以查看输出，不要重复提交。")
            return
        self.busy = True
        self.status.set(title + "…")
        self.progress.start()
        def run():
            try:
                self.queue.put((title, action(), None, done))
            except Exception as exc:
                self.queue.put((title, None, str(exc), None))
        threading.Thread(target=run, daemon=True).start()

    def drain(self):
        try:
            while True:
                title, result, error, done = self.queue.get_nowait()
                self.busy = False
                self.progress.stop()
                self.status.set(title + ("失败，请查看输出。" if error else "已返回，请查看具体结果。"))
                self.log({"operation": title, "error": error} if error else result)
                if error:
                    messagebox.showerror(title, error)
                elif done:
                    try:
                        done(result)
                    except Exception as exc:
                        self.log({"error": str(exc)})
                        messagebox.showerror(title, str(exc))
                if self.controller:
                    self.refresh_nodes()
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def close(self):
        if self.busy:
            messagebox.showinfo("任务尚未结束", "请等待操作结束。关闭控制端不代表远端仿真已取消。")
            return
        self.root.destroy()

    def make_controller(self, frame):
        ttk.Label(frame, text="1. 导出配对请求交给子端 → 2. 子端导出节点卡 → 3. 导入并检测。\n本机 VM、另一台电脑的 VM、Linux 服务器都作为计算节点加入。", justify="left").pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(frame); row.pack(fill="x")
        ttk.Label(row, text="母电脑局域网 IPv4").pack(side="left")
        self.ip = tk.StringVar()
        ttk.Entry(row, textvariable=self.ip, width=22).pack(side="left", padx=5)
        ttk.Button(row, text="导出配对请求", command=self.pair).pack(side="left", padx=4)
        ttk.Button(row, text="导入节点卡", command=self.import_card).pack(side="left", padx=4)
        self.table = ttk.Treeview(frame, columns=("label", "host", "port", "enabled", "slots"), show="headings", height=8)
        for key, title, width in [("label", "名称", 180), ("host", "SSH 主机", 190), ("port", "端口", 70), ("enabled", "参与计算", 90), ("slots", "并行任务数", 90)]:
            self.table.heading(key, text=title); self.table.column(key, width=width)
        self.table.pack(fill="both", expand=True, pady=10)
        row = ttk.Frame(frame); row.pack(fill="x")
        ttk.Button(row, text="检测选中节点", command=self.check_node).pack(side="left", padx=3)
        ttk.Button(row, text="检测所有节点", command=lambda: self.work("检测所有节点", lambda: [self.controller.check(n["name"]) for n in self.controller.nodes()])).pack(side="left", padx=3)
        ttk.Button(row, text="启用 / 停用选中节点", command=self.toggle).pack(side="left", padx=3)
        ttk.Button(row, text="全局并行数", command=self.parallel).pack(side="left", padx=3)
        self.refresh_nodes()

    def refresh_nodes(self):
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        for n in self.controller.nodes():
            self.table.insert("", "end", iid=n["name"], values=(n.get("label", n["name"]), n["host"], n["port"], "是" if n.get("enabled", True) else "否", n["slots"]))
        if selected and self.table.exists(selected[0]): self.table.selection_set(selected[0])

    def selected(self):
        values = self.table.selection()
        if not values: raise ValueError("请先选择一个节点")
        return values[0]

    def pair(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="controller-request.json")
        if path:
            ip = self.ip.get().strip()
            self.work("导出配对请求", lambda: self.controller.pairing(ip, path))

    def import_card(self):
        path = filedialog.askopenfilename(filetypes=[("节点卡", "*.json")])
        if path: self.work("配对节点并验证 SSH", lambda: self.controller.import_card(path))

    def check_node(self):
        try: name = self.selected()
        except ValueError as exc: messagebox.showinfo("选择节点", str(exc)); return
        self.work("检查 SSH、Spectre、Virtuoso", lambda: self.controller.check(name))

    def toggle(self):
        try:
            name = self.selected()
            self.controller.set_enabled(name, not self.controller.node(name).get("enabled", True))
            self.refresh_nodes()
        except Exception as exc: messagebox.showerror("切换节点", str(exc))

    def parallel(self):
        value = simpledialog.askinteger("并发设置", "所有节点合计的最大并行任务数（应符合许可证和内存余量）", initialvalue=2, minvalue=1, maxvalue=64)
        if value:
            try:
                self.controller.idle()
                cfg = read_json(self.controller.pool_path); cfg["max_parallel"] = value
                write_json(self.controller.pool_path, cfg)
            except Exception as exc: messagebox.showerror("并发设置", str(exc))

    def make_tasks(self, frame):
        ttk.Label(frame, text="使用已导出的 Spectre 网表和 jobs.json。多参数点、多 corner、多电路可分发。\n单个仿真在选定节点运行；本程序不将一个普通 transient 自动拆到两台机器。", justify="left").pack(anchor="w", pady=8)
        self.jobs = tk.StringVar(); self.batch = tk.StringVar()
        for label, var, callback in [("任务清单", self.jobs, self.choose_jobs), ("批次目录（必须不存在）", self.batch, self.choose_batch)]:
            row = ttk.Frame(frame); row.pack(fill="x", pady=5)
            ttk.Label(row, text=label, width=24).pack(side="left")
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="选择", command=callback).pack(side="left", padx=5)
        row = ttk.Frame(frame); row.pack(fill="x", pady=12)
        ttk.Button(row, text="开始分发", command=self.dispatch).pack(side="left", padx=3)
        ttk.Button(row, text="查询已有批次", command=self.status_batch).pack(side="left", padx=3)
        ttk.Button(row, text="取回全部结果", command=self.fetch).pack(side="left", padx=3)
        ttk.Label(frame, text="任务日志与状态保存在批次目录。中断或响应不明确时停止新派发，防止重复消耗许可证。\n恢复查询不会重新提交任务；UNKNOWN 需要核实远端状态。", justify="left").pack(anchor="w", pady=12)

    def choose_jobs(self):
        path = filedialog.askopenfilename(filetypes=[("任务清单", "*.json")])
        if path: self.jobs.set(path)

    def choose_batch(self):
        path = filedialog.asksaveasfilename(title="填写一个尚不存在的批次目录名", initialfile="batch-001")
        if path: self.batch.set(path)

    def dispatch(self):
        jobs, batch = self.jobs.get(), self.batch.get()
        if not jobs or not batch: messagebox.showinfo("任务清单", "请选择 jobs.json 和新批次目录"); return
        self.work("分发与跟踪仿真", lambda: self.controller.run(jobs, batch))

    def status_batch(self):
        batch = filedialog.askdirectory(title="选择已有批次目录")
        if batch:
            self.batch.set(batch)
            self.work("查询批次", lambda: self.controller.reconcile(batch))

    def fetch(self):
        batch = self.batch.get()
        if not batch: messagebox.showinfo("结果", "请先选择或查询批次"); return
        output = filedialog.askdirectory(title="选择结果保存父目录（每个任务建立新目录）")
        if output:
            def collect():
                rows = read_json(Path(batch) / "state.json")
                return [self.controller.collect(batch, r["name"], Path(output) / r["job_id"]) for r in rows if r.get("node")]
            self.work("取回并校验结果文件", collect)


class NodePanel:
    def __init__(self, app, frame, directory):
        self.app, self.directory = app, Path(directory)
        self.path = self.directory / "node.json"
        self.cfg = read_json(self.path) if self.path.exists() else node.defaults()
        self.request = None
        self.vars = {}
        book = ttk.Notebook(frame); book.pack(fill="both", expand=True)
        basic = ttk.Frame(book, padding=8); book.add(basic, text="连接与启动")
        advanced = ttk.Frame(book, padding=8); book.add(advanced, text="仿真 / 新建 Bridge")
        fields = [("label", "节点名称"), ("vmx", "VMware .vmx 文件"), ("vm_ip", "Linux 虚拟机 IP"), ("user", "Linux 用户名"), ("lan_ip", "本机 Windows 局域网 IP"), ("public_port", "对母端开放 SSH 端口"), ("bridge_port", "虚拟机 Bridge 端口")]
        for i, (key, label) in enumerate(fields): self.field(basic, i, key, label)
        ttk.Button(basic, text="浏览", command=self.browse_vmx).grid(row=1, column=2, padx=4)
        row = ttk.Frame(basic); row.grid(row=7, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Button(row, text="启动本机 VM / 获取 IP", command=self.start_vm).pack(side="left", padx=2)
        ttk.Button(row, text="核对 SSH 指纹", command=self.scan).pack(side="left", padx=2)
        ttk.Button(row, text="保存设置", command=self.save_clicked).pack(side="left", padx=2)
        self.request_label = tk.StringVar(value="尚未导入母端配对请求")
        ttk.Label(basic, textvariable=self.request_label).grid(row=8, column=0, columnspan=3, sticky="w")
        row = ttk.Frame(basic); row.grid(row=9, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Button(row, text="1 导入母端请求", command=self.load_request).pack(side="left", padx=2)
        ttk.Button(row, text="2 安装公钥 / Bridge", command=self.install).pack(side="left", padx=2)
        ttk.Button(row, text="3 配置 NAT 入口", command=self.forward).pack(side="left", padx=2)
        ttk.Button(row, text="查看转发结果", command=self.forward_receipt).pack(side="left", padx=2)
        row = ttk.Frame(basic); row.grid(row=10, column=0, columnspan=3, sticky="w", pady=5)
        self.direct = tk.BooleanVar(value=bool(app.controller))
        ttk.Checkbutton(row, text="母端可直连 Linux IP（本机 VM / 桥接 / 服务器）", variable=self.direct).pack(side="left")
        ttk.Button(row, text="4 导出节点卡", command=self.card).pack(side="left", padx=3)
        ttk.Button(row, text="检测节点", command=self.check).pack(side="left", padx=3)
        ttk.Label(basic, text="NAT 模式：开放的仅是指定来源的 SSH 入口。Bridge 通过 SSH 隧道访问。\n两台电脑各自点击启动 VM；Windows 宿主机必须已开机。", justify="left").grid(row=11, column=0, columnspan=3, sticky="w", pady=6)
        advanced_fields = [("spectre", "Spectre 可执行文件"), ("slots", "同时运行任务数"), ("threads", "每个任务 CPU 线程"), ("license_file", "CDS_LIC_FILE（可空）"), ("virtuoso_executable", "Virtuoso 可执行文件"), ("design_directory", "Linux 设计工作目录"), ("display", "图形 DISPLAY")]
        self.cfg["license_file"] = self.cfg.get("environment", {}).get("CDS_LIC_FILE", "")
        for i, (key, label) in enumerate(advanced_fields): self.field(advanced, i, key, label)
        self.existing = tk.BooleanVar(value=self.cfg.get("existing_bridge", True))
        self.autoload = tk.BooleanVar(value=self.cfg.get("autoload", False))
        ttk.Checkbutton(advanced, text="使用已经正常工作的 Bridge（保留现有安装）", variable=self.existing).grid(row=7, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(advanced, text="新建 Bridge 时将自动加载加入 ~/.cdsinit（会备份）", variable=self.autoload).grid(row=8, column=0, columnspan=3, sticky="w")
        row = ttk.Frame(advanced); row.grid(row=9, column=0, columnspan=3, sticky="w", pady=10)
        ttk.Button(row, text="探测 Cadence 路径", command=self.detect).pack(side="left", padx=3)
        ttk.Button(row, text="启动 Virtuoso", command=self.start_virtuoso).pack(side="left", padx=3)
        ttk.Button(row, text="生成 Linux 初始化脚本", command=self.bootstrap).pack(side="left", padx=3)
        ttk.Label(advanced, text="首次使用：Linux 已装 OpenSSH Server、Python 3、Cadence 和可用许可证。\n新建 Bridge 后，已打开的 Virtuoso 需在 CIW 执行输出的 load 命令；\n之后的新会话可由 .cdsinit 自动加载。服务器无 GUI 时仍可通过 SSH 运行 Spectre。", justify="left").grid(row=10, column=0, columnspan=3, sticky="w")

    def field(self, frame, row, key, label):
        ttk.Label(frame, text=label, width=27).grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar(value=str(self.cfg.get(key, ""))); self.vars[key] = var
        ttk.Entry(frame, textvariable=var).grid(row=row, column=1, sticky="ew", pady=3)
        frame.columnconfigure(1, weight=1)

    def snapshot(self, validate=True):
        cfg = dict(self.cfg)
        cfg.update({k: v.get().strip() for k, v in self.vars.items()})
        for name in ("public_port", "bridge_port", "slots", "threads"): cfg[name] = int(cfg[name])
        cfg["existing_bridge"], cfg["autoload"] = self.existing.get(), self.autoload.get()
        cfg["environment"] = dict(cfg.get("environment", {}))
        license_file = cfg.pop("license_file", "")
        if license_file: cfg["environment"]["CDS_LIC_FILE"] = license_file
        else: cfg["environment"].pop("CDS_LIC_FILE", None)
        if validate: node.validate(cfg)
        self.cfg = cfg; write_json(self.path, cfg)
        return cfg

    def guarded(self, fn):
        try: fn()
        except Exception as exc: messagebox.showerror("节点设置", str(exc))

    def save_clicked(self):
        self.guarded(lambda: self.app.log({"saved": str(self.path), "node": self.snapshot()["node_id"]}))

    def browse_vmx(self):
        path = filedialog.askopenfilename(filetypes=[("VMware 虚拟机", "*.vmx")])
        if path: self.vars["vmx"].set(path)

    def start_vm(self):
        vmx = self.vars["vmx"].get().strip()
        def start():
            result = vmware.start(vmx, self.cfg.get("vmrun"))
            try: result["vm_ip"] = vmware.guest_ip(vmx, self.cfg.get("vmrun"))
            except Exception as exc: result["ip_note"] = str(exc) + "；可从 Linux hostname -I 手动填写。"
            return result
        def done(result):
            if result.get("vm_ip"): self.vars["vm_ip"].set(result["vm_ip"])
        self.app.work("启动本机虚拟机", start, done)

    def scan(self):
        address = self.vars["vm_ip"].get().strip()
        def done(result):
            if messagebox.askyesno("核对 Linux 主机身份", "请在 Linux 终端执行：\nssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256\n\n程序读到：\n" + result["fingerprint"] + "\n\n与 Linux 终端结果完全一致吗？"):
                self.cfg.update(host_fingerprint=result["fingerprint"], host_public_key=result["key"])
                self.snapshot(False)
                self.app.log("已保存经核对的 SSH 主机身份。")
        self.app.work("读取主机公钥", lambda: ssh.scan_key(address, 22), done)

    def load_request(self):
        path = filedialog.askopenfilename(filetypes=[("母端配对请求", "*.json")])
        if not path: return
        def load():
            request = node.validate_request(read_json(path))
            if not messagebox.askyesno("信任母端", "确认这是你要授权的母端：\nIP: " + request["controller_ip"] + "\n公钥: " + request["fingerprint"] + "\n\n应与母端程序导出时显示的一致。继续？"): return
            self.request = request
            self.request_label.set("母端 " + request["controller_ip"] + " · 公钥 " + request["fingerprint"][-12:])
        self.guarded(load)

    def task(self, title, action, request=False, done=None):
        def begin():
            cfg = self.snapshot()
            if request and not self.request: raise ValueError("请先导入母端配对请求")
            req = dict(self.request) if self.request else None
            self.app.work(title, lambda: action(cfg, req), done)
        self.guarded(begin)

    def install(self):
        self.task("安装节点公钥", lambda c, r: node.interactive_install(c, r, self.directory), True)

    def bootstrap(self):
        self.task("生成 Linux 初始化脚本", lambda c, r: {"script": str(node.bootstrap(c, r, self.directory))}, True)

    def forward(self):
        if self.direct.get(): messagebox.showinfo("直接连接", "直连模式无需 Windows 端口转发。完成公钥安装后直接导出节点卡。"); return
        self.task("请求配置 SSH 转发", lambda c, r: node.apply_forward(c, r, self.directory), True)

    def forward_receipt(self):
        def read():
            path = self.directory / "forward.result.json"
            if not path.exists(): raise ValueError("尚无转发结果，请先完成管理员授权")
            self.app.log(read_json(path))
        self.guarded(read)

    def card(self):
        output = filedialog.asksaveasfilename(initialfile="node-card.json", defaultextension=".json")
        if not output: return
        direct = self.direct.get()
        self.task("导出节点卡", lambda c, r: node.export_card(c, r, output, direct=direct), True)

    def check(self):
        def check(c, r):
            n = node.local_node(c, self.directory)
            result = {"ssh": ssh.command(n, "hostname; whoami").stdout.decode().strip()}
            for name, fn in [("spectre", lambda: runner.remote(n, "doctor")), ("bridge", lambda: ssh.skill(n))]:
                try: result[name] = fn()
                except Exception as exc: result[name + "_error"] = str(exc)
            return result
        self.task("检测节点", check)

    def detect(self):
        def done(result):
            for key, field in [("spectre", "spectre"), ("virtuoso", "virtuoso_executable"), ("license_file", "license_file")]:
                if result.get(key): self.vars[field].set(result[key])
            self.snapshot()
        self.task("探测 Cadence 路径", lambda c, r: node.detect_environment(node.local_node(c, self.directory)), done=done)

    def start_virtuoso(self):
        self.task("启动 Virtuoso", lambda c, r: node.start_virtuoso(c, node.local_node(c, self.directory)))


def launch(role, controller_root=None):
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes
        window = ctypes.windll.kernel32.GetConsoleWindow()
        if window: ctypes.windll.user32.ShowWindow(window, 0)
    App(role, controller_root).root.mainloop()

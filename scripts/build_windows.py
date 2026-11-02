"""Build two independent, portable onedir ZIPs. Run on Windows with PyInstaller."""
import hashlib
from importlib import metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    if sys.platform != "win32": raise SystemExit("Build Windows binaries on Windows")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    release = root / "release"; release.mkdir(exist_ok=True)
    for role, title in [("controller", "Controller"), ("node", "Node")]:
        name = "Virtuoso-LAN-" + title
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
                        "--name", name, "--paths", "src", "--collect-data", "virtuoso_lan_bridge",
                        "--add-data", str(root / "src/virtuoso_lan_bridge/runner/worker.py") + ":virtuoso_lan_bridge/runner",
                        "--add-data", str(root / "src/virtuoso_lan_bridge/vendor") + ":virtuoso_lan_bridge/vendor",
                        "--distpath", str(root / "dist"), "--workpath", str(root / "build"),
                        "--specpath", str(root / "build"), str(root / (role + "_app.py"))], check=True)
        folder = root / "dist" / name
        for file in ("README.md", "LICENSE", "NOTICE.md", "VALIDATION.md"):
            shutil.copy2(root / file, folder / file)
        shutil.copytree(root / "examples", folder / "examples", dirs_exist_ok=True)
        shutil.copytree(root / "src/virtuoso_lan_bridge/virtuoso", folder / "virtuoso", dirs_exist_ok=True)
        licenses = folder / "licenses"; licenses.mkdir(exist_ok=True)
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
        if python_license.exists(): shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
        for file in metadata.distribution("pyinstaller").files:
            if "license" in str(file).lower() or "copying" in str(file).lower():
                src = Path(metadata.distribution("pyinstaller").locate_file(file))
                if src.is_file(): shutil.copy2(src, licenses / ("PyInstaller-" + src.name))
        for directory in (Path(sys.base_prefix) / "tcl", folder / "_internal/_tcl_data", folder / "_internal/_tk_data"):
            if directory.exists():
                for src in directory.rglob("license*"):
                    if src.is_file(): shutil.copy2(src, licenses / (src.parent.name + "-" + src.name))
        env = dict(os.environ, VLB_STATE_DIR=str(root / ".local" / ("frozen-qa-" + role)))
        for command in ([str(folder / (name + ".exe")), "--help"],
                        [str(folder / (name + ".exe")), "--self-test-gui"]):
            subprocess.run(command, check=True, env=env, timeout=30)
        shutil.make_archive(str(release / (name + "-Windows-x64")), "zip", folder.parent, folder.name)
    zips = sorted(release.glob("*.zip"))
    (release / "SHA256SUMS.txt").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in zips), encoding="ascii")
    print("Release assets:", [p.name for p in zips])


if __name__ == "__main__": main()

# Third-party components

The two source files under `src/virtuoso_lan_bridge/vendor/` are copied
verbatim from [Arcadia-1/virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite),
commit `421721964c29f99163db1d292417b18d6be7af27`:

- `src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge.il`
- `src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge_daemon_3.py`

Their MIT license and original copyright notice are preserved in
`src/virtuoso_lan_bridge/vendor/LICENSE.virtuoso-bridge-lite`.
The installer derives a runtime copy of the IL file with `RBLocal = t`
to bind the daemon to loopback. The vendored source itself is unchanged.

This project is an independent companion, not an official Cadence or VMware product.
No Cadence binaries, license files, PDKs, user SSH keys, or VM disk images are distributed.
Windows releases also contain Python, Tcl/Tk and the PyInstaller bootloader;
their bundled license notices are included in the release directory.

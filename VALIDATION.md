# Verification

[简体中文](VALIDATION.zh-CN.md) | **English** · [User guide](README.en.md)

## v0.2.0 desktop workflow (2026-11-03)

- Windows automated tests: 69 passed, with one Linux-only integration test skipped. New coverage includes double-click locking, all-node preflight failure, preservation of an earlier UNKNOWN lock, check-only mode, configuration changes after checking, native-log/distribution/collection success requirements, separate result directories, and HTML escaping.
- Both Windows EXEs built successfully; CLI help and native Tk widget creation passed. The shortcut installer was executed with Windows PowerShell 5.1 and its shortcut targets and arguments were read back.
- The current local VM passed SSH, Spectre version, and Bridge `1+2=3` checks. The launcher recognized the already-running local VM.
- The current second computer's SSH entry timed out; network probes did not confirm reachability. The new packaged workflow returned `PREFLIGHT_FAILED`, saved its report, and dispatched no simulations.

The new workflow's live two-node success path therefore remains to be retested after the second computer becomes reachable. The v0.1.0 two-node RC evidence below is historical and does not establish current connectivity or a successful run in this session. No speedup was measured; automatic graphical Virtuoso recovery after cold boot remains unverified.

## v0.1.0 initial release

Verified on 2026-11-02 before the initial release:

| Check | Result |
| --- | --- |
| Windows Python tests | 49 passed; Linux-only integration test skipped on Windows |
| Both frozen Windows EXEs | CLI help and native Tk widget creation passed |
| Node EXE bootstrap | Generated a complete, compilable guest installer with vendored Bridge resources |
| Desktop node pairing | Host-key pinning and authenticated SSH through the existing Windows NAT entry passed |
| Two VM connections from the packaged controller | SSH authenticated on both; each Spectre version check returned 0 |
| Virtuoso over SSH tunnels | `1+2` returned `3` on both VMs |
| Role-specific SKILL entry points | Controller entry evaluated on the local Virtuoso; node entry evaluated on the remote Virtuoso; subsequent `1+2` passed on both |
| Real simulation distribution | Two RC netlists, assigned to different VMs; both returned process exit 0 and Spectre native error count 0 |
| Result collection | Eight files per task; all 16 files passed transfer hash verification |
| Local VMware lifecycle | Already-running VM recognized; no duplicate launch, power-off or reset |

Tests cover invalid public keys/addresses, host-key changes, SSH authentication
failure before pool admission, isolated trust files, preserving existing
authorized keys and `.cdsinit`, repeat installer execution, strict Bridge response
markers, duplicate jobs, uncertain submissions, native Spectre success/failure,
artifact identity/hash/path checks, and scheduler-lock reconciliation.

The Linux CI integration additionally exercises real process-group cancellation,
timeouts, detached workers and slot locking using a fake simulator; it does not
consume a Cadence license. CI results belong to the exact commit shown by GitHub.

The live machines already had SSH forwarding and running Virtuoso sessions.
Fresh Windows firewall/UAC setup, a cold VM boot, new `.cdsinit` autoload in a new
Virtuoso session, and two GUI windows operated together were not end-to-end tested
on fresh machines. The GUI widgets were created successfully; a separate automated
visual inspection was unavailable. Existing user designs were not modified.

The RC smoke test confirms dispatch and collection, not performance gains for a
large design, PDK compatibility, ADE import, multi-host single-analysis execution,
or unlimited license concurrency. Private connection settings, raw machine logs,
keys, licenses and simulation results are excluded from this repository and ZIPs.

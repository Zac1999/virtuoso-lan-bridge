# v0.1.0 verification

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

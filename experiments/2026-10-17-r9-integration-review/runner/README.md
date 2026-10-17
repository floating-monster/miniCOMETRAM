# Integrated COMETRAM runner

This runner targets the reviewed 53-transistor decoder-tap and VSS-M1
candidate. It creates a fresh run directory and connects generation,
DRC/LVS, Magic 413 RC extraction, capacitance reconstruction, read/write
simulation, strict replay checks and result hashing.

## Current host command

```bash
python3 /home/gwen/workspace/COMETRAM_R9/cometram_integrated_01/run.py
```

The copy here is a source checkpoint. It is not a self-contained clean-clone
release: the default host paths, cached Cargo dependencies, generation PDK,
local Docker image and reviewed replay references must already exist.
`--help` exposes the current path arguments; some reference paths remain
specific to the preserved R9 workspace.

## Dependencies

- Host Rust/Cargo and cached Substrate dependencies; Cargo runs locked/offline.
- Host generation PDK: `/home/gwen/workspace/skylarkRAM_R0/pdk`.
- Fixed image:
  `sha256:b12e325417c221e4db0d1ad09e1282edc81bf5e566c004cfef0c9415e4d75371`.
- Magic 8.3.413, Netgen 1.5.286, ngspice 41 and the image's internal PDK.
- Existing R9 read reference:
  `decoder_vss_m1_candidate_01_4or4fbvy/read_skylark_413_pair_02_jpf648ae/candidate`.
- Existing R9 write reference:
  `decoder_vss_m1_candidate_01_4or4fbvy/write_skylark_413_pair_01_wmtyaw8z/candidate`.

Magic 447 is not invoked by this runner. Host generation remains outside
Docker and is recorded as an external dependency.

## Checks and records

The runner checks input hashes, image tool versions and selected PDK hashes;
re-generates the candidate; compares its GDS element contents and schematic;
requires DRC zero and LVS unique 53-device match; checks actual R inclusion;
reconstructs capacitance with the audited weighted method; runs the existing
read/write tests with maximum internal step 2.5 ps; and compares measurements
against the reviewed 413 reference at log precision.

Generated runs are written under R9 as `integrated_skylark_01_*`.
`progress.txt`, per-stage logs, `verdict.txt` and `results.sha256` retain the
execution state. Compilation cache is separate at `integration_build_cache_01`.
Existing R9 reference source/GDS hashes are checked before and after the run.
No automatic restart, tool upgrade or failure suppression is performed.

The 2026-10-17 execution reached all stages but failed its strict numerical
replay criterion. A failed run does not produce the success-only automatic
archive; it was manually sealed without altering the original verdict.
See `../RESULTS.md` and `../LOCAL_ARCHIVE.md`.

## Limitations

This is a capacitance-preserving weighted approximation, not a validated
physical RC sign-off model. Coupling locations, continuous-time margins,
physical timing accuracy and broad PVT behavior remain unvalidated.
The write observation check uses the comparative 0.9 V midpoint at the
existing five sample times. No new original-threshold functional sign-off
criterion was introduced.

Audited source assets, test stimuli and original reports retain their
original bytes and comments. English documentation does not translate or
modify those evidence files.

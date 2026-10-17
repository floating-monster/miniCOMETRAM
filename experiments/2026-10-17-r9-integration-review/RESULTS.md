# COMETRAM R9: integrated execution checkpoint

Date: 2026-10-17
Status: **execution completed; strict numerical replay did not pass**.
This is a development checkpoint, not a physical-accuracy sign-off.

## Completed execution

The runner executed generation, DRC/LVS, Magic 413 RC extraction,
capacitance-preserving model reconstruction, and read/write simulations.

- DRC: zero reported errors.
- LVS: unique match, 53 devices on each side.
- Generated GDS: all element contents and multiplicities match the reviewed
  candidate when timestamps and cell/element output order are excluded.
  Original byte hashes are retained separately.
- RC model: 447 circuit nodes, 486 resistors, 53 MOS instances,
  and 21,731 generated capacitance records.
- Original net-pair capacitance total: 500.794664373 fF, preserved.
- Write storage observations: 28 split nodes at five existing sample times;
  all 140 observations match the expected state using the explicitly
  comparative 0.9 V midpoint classification.

## Strict replay failure retained

The original runner's exact measurement criterion was not met:
5 of 36 read measurements and 7 of 20 write measurements differed.
The largest reported difference was 1e-12 V.
No retrospective tolerance was introduced, and the failed verdict is retained.

A separate ledger comparison found differences before SPICE simulation:

- All 447 nodes were mapped one-to-one.
- All 53 MOS instances match in terminal connections, models and parameters.
- Original EXT net-pair capacitances match exactly.
- Two model resistances differ by +0.0004 and -0.0004 ohm.
- 15 raw split-node capacitances and 107 allocation weights differ.
- 17,978 allocated model capacitance pairs differ in value; pair sets match.
- Maximum allocated pair difference: 0.00000791246490 fF.

The weighted allocation propagates changes in its split-capacitance inputs.
These observations do not establish the sole cause of the measurement
variation or explain why the raw extraction values differed.

## Execution fixes

- Netgen metadata inspection uses batch mode to avoid Tk/display startup.
- Magic 413 export explicitly supplies the input search path with `-p`;
  otherwise this environment produced SPICE without the extracted R network.
- GDS checks compare full element contents and multiplicities, rather than
  requiring byte identity despite timestamp and output-order variation.
- Five previously reviewed MOS area/perimeter output precision differences
  are recorded explicitly. Unexpected differences still stop execution.

## Environment and limits

Generation uses host Rust/Cargo, cached dependencies pinned to Substrate
revision 60a8a4b8e150fb1a83d247e4b6da26a25b0309bc, and the host generation PDK.
Verification, extraction and simulation use the fixed SkylarkRAM image:

`sha256:b12e325417c221e4db0d1ad09e1282edc81bf5e566c004cfef0c9415e4d75371`

Magic 8.3.413 / Netgen 1.5.286 / ngspice 41, with the image's internal PDK.

The RC model remains a **capacitance-preserving weighted approximation**.
Physical coupling locations, physical timing accuracy, continuous-time
operating margins and broader PVT coverage are not validated.
The midpoint state check is not a noise-margin measurement or a replacement
for the original functional thresholds.

## Contents and next work

`runner/` contains the integrated runner and audited input assets.
`evidence/` contains selected original reports and measurement comparisons.
`MODEL_COMPARISON.md` summarizes the ledger comparison.
`LOCAL_ARCHIVE.md` identifies the full local archive.

Public documentation and filenames are English. Original execution logs,
audited source comments and generated evidence retain their original language
and bytes. Full waveforms and build caches are not committed.

The runner currently depends on existing Gwen-host R9 paths and preserved
413 replay references. A clean-clone, independent-host replay has not been
validated. See `runner/README.md` before attempting execution.

Next work will examine model limitations, allocation sensitivity and
continuous-time behavior to define variables, observations and stop criteria
for small-array expansion experiments.

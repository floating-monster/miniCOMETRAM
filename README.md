# miniCOMETRAM

Rust SRAM generator and circuit verification workflow.
Current design: experimental 2x1 SRAM circuit with 53 MOS devices.
Cargo package name remains cometram_r0.

## Contents

- src, Cargo.toml, Cargo.lock: generator and pinned dependencies
- scripts: verification and observation
- output: historical GDS and SPICE checked by exact hashes
- reference: earlier sources and design contracts
- Preserved experiment folders: baseline inputs and verification evidence

## Environment

Build the Docker environment using floating-monster/skylarkRAM.
Docker images and PDK trees are not included here.
Host verification requires Python 3 and sudo docker.

Select the locally built image:
export SKYLARKRAM_IMAGE=skyram-fresh:193r2-openram-r2

Without this variable, scripts use the historical local image ID.
A matching image tag alone does not establish identical contents or results.

## Generator

Requires host Rust and a compatible local SKY130 PDK tree.
Exact Rust version and host PDK setup remain to be documented.
Set COMETRAM_PDK_ROOT to the PDK directory accepted by Sky130OpenPdk.

Run from the repository root:
mkdir -p output
cargo run --locked

Back up historical output before generation: this command writes
output/comet_ram_2x1.gds and output/comet_ram_2x1.spice.
Verification scripts require the preserved baseline hashes.
Generation alone does not run DRC, LVS or simulation.

## Verification

Run from the repository root with SKYLARKRAM_IMAGE set:

    python3 scripts/verify_skyram_01.py
    python3 scripts/sim_skyram_01.py
    python3 scripts/rc2_skyram_01.py
    python3 scripts/all_nodes_01.py
    python3 scripts/read_windows_01.py

Each script creates a new result directory.
Several scripts consume preserved historical baseline directories.
These commands replay baselines; they do not automatically pass new
results from one stage to the next.

## Historical results and limits

- Preserved 53-device layout: zero DRC errors; unique LVS match.
- RC candidate: capacitance-preserving weighted approximation.
- Total and original-net-pair capacitance balance: PASS.
- All 397 circuit nodes observed at existing measurement times.
- Reference measurements numerically identical.
- Q/QB distance from the 0.9 V midpoint: 0.899998 V.
  This diagnostic is not SNM or validated logic-threshold margin.

Coupling spatial accuracy, dynamic timing accuracy, continuous-time
margin, original threshold margin and physical accuracy are unvalidated.
Verdicts describe their individual historical stages.
RC2_SIMULATION=NOT_RUN in the RC1 verdict describes that earlier stage.

Repository preparation added configurable image selection.
All five modified scripts completed replay in the publication copy.
Evidence: publication_evidence/20261016.
DRC/LVS passed; functional and RC2 reference measurements matched.
All-node and read-window observations completed.
Read stability remains REVIEW_REQUIRED; physical accuracy is unvalidated.
This replay used the existing image, not a fresh Docker rebuild.
Upstream dependencies retain their respective licenses.

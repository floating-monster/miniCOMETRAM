# Third-party notices

## Substrate

Source: https://github.com/ucb-substrate/substrate
Revision: 60a8a4b8e150fb1a83d247e4b6da26a25b0309bc
License: BSD-3-Clause
Copyright (c) 2022, Rahul Kumar

The Rust generator depends on substrate, subgates, subgeom and sky130_open_pdk
from this pinned repository. The upstream license is preserved in
LICENSES/Substrate-BSD-3-Clause.txt.

## OpenRAM

Source: https://github.com/VLSIDA/OpenRAM
Environment installation revision: b6a6f12642df6b84facc24a77f9a6f67a0d62dab
License of that installation: BSD-3-Clause
See LICENSES/OpenRAM-BSD-3-Clause.txt for the complete upstream notice.

OpenRAM was consulted as a reference for Magic extraction command ordering,
options and post-processing. miniCOMETRAM's primary generator uses Rust and
Substrate. This attribution does not claim that the consulted source snapshot
has been identified as identical to the environment installation revision.
Any copied upstream code remains subject to its original notices.

## Verification environment and other dependencies

Magic, Netgen, ngspice, Volare, SKY130 PDK data and device models are supplied
through the separately maintained skylarkRAM environment. Their own licenses
apply; this repository's Apache-2.0 license does not replace them.

Cargo.lock identifies additional Rust dependencies. The two BSD license files
here are not an exhaustive inventory of all transitive dependency licenses.
No upstream author or institution is represented as endorsing this project.

# RC ledger comparison

Compared the reviewed 413 model with the integrated re-extraction of the
same GDS element contents. Node identifiers were mapped using original net
names, coordinates, MOS identity/terminal roles and resistor-neighbor
structure. R and C values were not used to establish node identity.

| Item | Result |
|---|---|
| Node mapping | 447 nodes, one-to-one |
| MOS | All 53 terminal connections, models and parameters identical |
| Original EXT net-pair C | Identical |
| Model R | 486 branches; two values differ by +/-0.0004 ohm |
| Raw split-node C | 15 changed values; maximum raw-unit difference 0.01 |
| Allocation weights | 107 changed values; maximum difference 1.70064608135e-7 |
| Allocated model C | Same pair set; 17,978 changed values |
| Maximum model C pair difference | 0.00000791246490 fF |

Original total C is 500.794664373 fF in both runs. Decimal sums of printed
allocated C records differ at much smaller digits and both satisfy the
recorded net-pair preservation checks.

The raw resistor records and their exported SPICE representations were also
reported separately; their literal numeric records are not identical within
either export. This was not silently treated as a cross-run comparison.

These findings locate differences before simulation but do not prove a sole
cause of the small measurement differences. No retrospective numerical
acceptance tolerance was added.

The original comparison output is retained at
`evidence/raw-model-ledger-comparison.txt`. The comparison script is
`compare_rc_ledgers_01.py`; it is read-only and currently uses preserved R9 paths.

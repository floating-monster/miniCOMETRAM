set work [file dirname [file normalize [info script]]]
cd $work

if {[catch {
    gds polygon subcell true
    gds warning default
    gds read input.gds
    load comet_ram_2x1
    select top cell
    expand
    readspice reference.spice
    extract
    ext2sim labels on
    ext2sim cthresh 0
    puts "SIM_CTHRESH=[ext2sim cthresh]"
    ext2sim
    puts [exec python3 [file join $work fix_separator.py]]
    extresist simplify off
    extresist all
    ext2spice hierarchy off
    ext2spice format ngspice
    ext2spice renumber off
    ext2spice scale off
    ext2spice blackbox on
    ext2spice subcircuit top on
    ext2spice global off
    ext2spice extresist on
    ext2spice cthresh 0
    puts "SPICE_CTHRESH=[ext2spice cthresh]"
    ext2spice comet_ram_2x1
} message]} {
    puts stderr "OPENRAM_FLOW_ERROR=$message"
    puts stderr $::errorInfo
    quit -noprompt
}

puts "OPENRAM_FLOW_COMPLETED"
quit -noprompt

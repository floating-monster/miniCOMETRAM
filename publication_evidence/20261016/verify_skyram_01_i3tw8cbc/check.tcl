gds read /input/comet_ram_2x1.gds
load comet_ram_2x1
select top cell
drc style drc(full)
drc check
drc catchup
set count [drc list count total]
set details [drc listall why]
set f [open /verify/drc_report.txt w]
puts $f "DRC_COUNT=$count"
puts $f "DRC_DETAILS=$details"
close $f
puts "DRC_COUNT=$count"
extract all
feedback save /verify/extraction_feedback.txt
ext2spice lvs
ext2spice -o /verify/layout_raw.spice
puts "FULL_CHECK_SCRIPT_COMPLETED"
quit -noprompt

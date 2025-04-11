#!/bin/sh

echo "Generating RTL"
./../../vendor/pulp_platform_register_interface/vendor/lowrisc_opentitan/util/regtool.py -r -t rtl data/bus_sniffer.hjson

# Insert the comment above the assignment for reg_addr
# Searches for the pattern "assign reg_addr = reg_intf_req.addr;" and inserts the comment above it.
sed -i '/assign reg_addr = reg_intf_req.addr;/i /* verilator lint_off WIDTH */' ./rtl/bus_sniffer_reg_top.sv

# Insert the comment above the module declaration
# Searches for the pattern "module bus_sniffer_reg_top_intf #(" and inserts the comment above it.
# sed -i '/module bus_sniffer_reg_top_intf ;/i /* verilator lint_off DECLFILENAME */' ./rtl/bus_sniffer_reg_top.sv

awk 'BEGIN { found=0 } 
     /^module/ { 
         if (found >= 1) 
             print "/* verilator lint_off DECLFILENAME */"; 
         found++;
     } 
     { print }' ./rtl/bus_sniffer_reg_top.sv > tmp && mv tmp ./rtl/bus_sniffer_reg_top.sv



mkdir -p ../../../sw/device/lib/drivers/bus_sniffer/
touch ../../../sw/device/lib/drivers/bus_sniffer/bus_sniffer_regs.h

echo "Generating SW"
./../../vendor/pulp_platform_register_interface/vendor/lowrisc_opentitan/util/regtool.py --cdefines -o ../../../sw/device/lib/drivers/bus_sniffer/bus_sniffer_regs.h data/bus_sniffer.hjson

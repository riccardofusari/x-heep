#!/usr/bin/env python3
import pexpect
import sys
import re

# Memory‐mapped register addresses (adjust to your mapping!)
CTRL_ADDR    = 0x30080000   # SNI_CTRL
STATUS_ADDR  = 0x30080004   # SNI_STATUS
DATA_ADDR     = 0x30080008  # first of SNI_DATA0–3

# Bit‐positions in those registers
FRAME_READ_BIT   = 1 << 2   # in CTRL: bit2 = FRAME_READ
FRAME_AVAIL_BIT  = 1 << 2   # in STATUS: bit2 = FRAME_AVAIL
EMPTY_BIT        = 1 << 0   # in STATUS: bit0 = EMPTY

def send_cmd(child, cmd):
    child.sendline(cmd)
    child.expect(r"\(gdb\)\s*$")

def parse_word(output):
    # extract the first hex word after the address:
    #   0xADDR:    0xVAL  ...
    m = re.search(r":\s*(0x[0-9A-Fa-f]+)", output)
    if not m:
        raise ValueError("Can't parse word from:\n" + output)
    return int(m.group(1), 16)

def parse_4words(output):
    # extract the four hex values on the line after the address
    lines = output.splitlines()
    if len(lines) < 2:
        raise ValueError("Unexpected mdw output:\n" + output)
    toks = lines[1].split()
    # drop leading "0xADDR:" if present
    if toks[0].endswith(":"):
        toks = toks[1:]
    if len(toks) < 4:
        raise ValueError("Need 4 words, got " + str(toks))
    return [int(t, 16) for t in toks[:4]]

def combine(regs):
    return (regs[0] << 96) | (regs[1] << 64) | (regs[2] << 32) | regs[3]

def print_fields(v):
    src =    (v >> 124) & 0xF
    req_ts = (v >> 92)  & 0xFFFFFFFF
    resp_ts=(v >> 76)  & 0xFFFF
    addr =   (v >> 44)  & 0xFFFFFFFF
    data =   (v >> 12)  & 0xFFFFFFFF
    be =     (v >> 8)   & 0xF
    we =     (v >> 7)   & 1
    valid =  (v >> 6)   & 1
    gnt =    (v >> 5)   & 1
    res =    v & 0x1F
    names = {
        1:"CORE_INSTR",2:"CORE_DATA",3:"AO_PERIPH",4:"PERIPH",
        5:"RAM0",6:"RAM1",7:"FLASH",8:"DMA_READ",9:"DMA_WRITE",10:"DMA_ADDR"
    }
    print(f"""
source_id:      {names.get(src,f'UNKNOWN({src})')}
req_timestamp:  0x{req_ts:08X}
resp_timestamp: 0x{resp_ts:04X}
address:        0x{addr:08X}
data:           0x{data:08X}
byte_enable:    0x{be:X}
we:             {we}
valid:          {valid}
gnt:            {gnt}
reserved:       0x{res:X}
""")

def main():
    # launch GDB
    gdb_cmd = "/home/riccardo/tools/riscv/bin/riscv32-unknown-elf-gdb --nx --quiet /home/riccardo/git/hep/x-heep/sw/build/main.elf"
    child = pexpect.spawn(gdb_cmd, timeout=2000)
    
    # Wait for the initial prompt. Use a regex that allows for trailing whitespace.
    try:
        child.expect(r"\(gdb\)\s*$")
    except pexpect.TIMEOUT:
        print("Timeout waiting for initial GDB prompt")
        sys.exit(1)
    
    print("GDB prompt received.")


    send_cmd(child, "set pagination off")
    send_cmd(child, "set target-async on")
    send_cmd(child, "set confirm off")
    send_cmd(child, "set remotetimeout 5000")

    # Now connect to the target.
    send_cmd(child, "target remote localhost:3333")
    print("Connected to target via GDB.")

    # Load program.
    send_cmd(child, "load")
    print("Loaded")

    send_cmd(child, "break main")
    send_cmd(child, "c")


    print(">>> hit hardware‐halt, now pumping frames…")

    # loop until FIFO empty
    while True:
        # read status
       # 1) ask OpenOCD to read our status reg off the system bus:
        send_cmd(child, f"monitor mdw {STATUS_ADDR:#x} 1")
        status = parse_word(child.before.decode())
        # send_cmd(child, f"x/xw {STATUS_ADDR:#x}")
        # status = parse_word(child.before.decode())
        if status & FRAME_AVAIL_BIT == 0:
            # no frame available → done
            print(">>> FIFO empty, resuming execution.")
            break

        # read the 128‐bit frame
        send_cmd(child, f"x/4xw {DATA_ADDR:#x}")
        regs = parse_4words(child.before.decode())
        val  = combine(regs)
        print(f">>> frame = 0x{val:032X}")
        print_fields(val)

        # tell sniffer “I read it”
        send_cmd(child, f"set *(unsigned int*){CTRL_ADDR:#x} = {FRAME_READ_BIT}")

        # resume until next halt (i.e. until FIFO full again or we force empty)
        send_cmd(child, "c")

    # finally resume normal execution
    send_cmd(child, "c")
    child.sendline("quit")
    child.expect(pexpect.EOF)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import pexpect, sys, re

# === register map ===
CTRL_ADDR   = 0x30080000  # SNI_CTRL
STATUS_ADDR = 0x30080004  # SNI_STATUS
DATA_ADDR   = 0x30080008  # SNI_DATA0..3

# === bit masks ===
FRAME_READ_BIT  = 1 << 2  # write to CTRL to ACK a frame
FRAME_AVAIL_BIT = 1 << 2  # STATUS bit2 indicates frame_avail
EMPTY_BIT       = 1 << 0  # STATUS bit0 indicates fifo empty

# === helpers ===
def send(child, cmd):
    child.sendline(cmd)
    child.expect(r"\(gdb\)\s*$")

def parse_single(output):
    m = re.search(r":\s*(0x[0-9A-Fa-f]+)", output)
    if not m:
        raise RuntimeError(f"parse_single failed on:\n{output}")
    return int(m.group(1), 16)

def parse_4words(output):
    lines = output.splitlines()
    if len(lines) < 2:
        raise RuntimeError(f"parse_4words failed on:\n{output}")
    toks = lines[1].split()
    if toks[0].endswith(":"):
        toks = toks[1:]
    if len(toks) < 4:
        raise RuntimeError(f"need 4 words, got {toks}")
    return [int(t, 16) for t in toks[:4]]

def combine128(ws):
    return (ws[0]<<96)|(ws[1]<<64)|(ws[2]<<32)|ws[3]

def dump_frame(v):
    src     = (v>>124)&0xF
    req_ts  = (v>>92)&0xFFFFFFFF
    resp_ts = (v>>76)&0xFFFF
    addr    = (v>>44)&0xFFFFFFFF
    data    = (v>>12)&0xFFFFFFFF
    be      = (v>>8)&0xF
    we      = (v>>7)&1
    valid   = (v>>6)&1
    gnt     = (v>>5)&1
    names   = {
      1:"CORE_INSTR",2:"CORE_DATA",3:"AO_PERIPH",4:"PERIPH",
      5:"RAM0",6:"RAM1",7:"FLASH",8:"DMA_READ",9:"DMA_WRITE",10:"DMA_ADDR"
    }
    print(f"""
── FRAME ─────────────────────────────
  source    : {names.get(src,src)}
  req_ts    : 0x{req_ts:08X}
  resp_ts   : 0x{resp_ts:04X}
  address   : 0x{addr:08X}
  data      : 0x{data:08X}
  byte_en   : 0x{be:X}
  we        : {we}
  valid     : {valid}
  gnt       : {gnt}
───────────────────────────────────────
""")

# === main ===
def main():
    if len(sys.argv)!=2:
        print("Usage: bus_sniffer_pump.py <your.elf>")
        sys.exit(1)
    elf = sys.argv[1]

    # spawn GDB
    child = pexpect.spawn(
        f"/home/riccardo/tools/riscv/bin/riscv32-unknown-elf-gdb --nx --quiet {elf}",
        timeout=300
    )
    child.expect(r"\(gdb\)\s*$")

    # initial setup
    for cmd in (
      "set pagination off",
      "set target-async on",
      "set confirm off",
      "set remotetimeout 5000",
      "target remote localhost:3333",
      "load"
    #   "break main"
    ):
        send(child, cmd)

    print(">>> Starting main and entering pump loop")
    child.sendline("c")      # first continue
    child.expect(r"\(gdb\)\s*$")

    # Outer pump loop: continue → halt → drain → reset → continue …
    while True:
        # We assume we’ve just hit SIGTRAP from the bus‐sniffer halt.
        print(">>> HALT: draining FIFO…")

        # Drain until empty
        while True:
            send(child, f"monitor mdw {STATUS_ADDR:#x} 1")
            st = parse_single(child.before.decode())
            if not (st & FRAME_AVAIL_BIT):
                print(">>> FIFO EMPTY")
                break

            send(child, f"x/4xw {DATA_ADDR:#x}")
            ws = parse_4words(child.before.decode())
            dump_frame(combine128(ws))

            # ack frame
            send(child, f"set *(unsigned int*){CTRL_ADDR:#x} = {FRAME_READ_BIT}")

            # let the next frame fill the FIFO again
            send(child, "c")
            child.expect(r"\(gdb\)\s*$")

        # reset + re-enable FIFO
        print(">>> Reset+enable FIFO, resuming core")
        send(child, f"set *(unsigned int*){CTRL_ADDR:#x} = 0x2")  # RST_FIFO
        send(child, f"set *(unsigned int*){CTRL_ADDR:#x} = 0x1")  # EN

        # resume and wait either for next halt or program exit
        child.sendline("c")
        i = child.expect([r"\(gdb\)\s*$", pexpect.EOF, r"Program exited"])
        if i!=0:
            print(">>> Program terminated, exiting pump.")
            break
        # else we’ve hit another SIGTRAP — loop back to drain

    # finally quit GDB
    child.sendline("quit")
    child.expect(pexpect.EOF)

if __name__=="__main__":
    main()

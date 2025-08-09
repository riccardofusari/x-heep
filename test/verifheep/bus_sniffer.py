#!/usr/bin/env python3
import pexpect, re, sys

# --- MMIO map
CTRL_ADDR    = 0x30080000  # SNI_CTRL
STATUS_ADDR  = 0x30080004  # SNI_STATUS
DATA_ADDR    = 0x30080008  # SNI_DATA0..3

# --- CTRL bits
EN_BIT         = 1 << 0
RST_FIFO_BIT   = 1 << 1
FRAME_READ_BIT = 1 << 2

# --- STATUS bits
EMPTY_BIT       = 1 << 0
FULL_BIT        = 1 << 1
FRAME_AVAIL_BIT = 1 << 2

# ------------ GDB helpers ------------
def gsend(child, cmd):
    child.sendline(cmd)
    child.expect(r"\(gdb\)\s*$")

def gget(child, cmd):
    child.sendline(cmd)
    child.expect(r"\(gdb\)\s*$")
    return child.before.decode()

def read_word(child, addr):
    out = gget(child, f"x/wx {addr:#x}")
    m = re.search(r":\s*(0x[0-9A-Fa-f]+)", out)
    if not m:
        raise RuntimeError(f"x/wx parse failed:\n{out}")
    return int(m.group(1), 16)

def read_status_and_data(child):
    """
    Reads STATUS and the 4 data words in one shot:
      x/5xw STATUS_ADDR  ->  STATUS, DATA0, DATA1, DATA2, DATA3
    Returns (status, [w0,w1,w2,w3])
    """
    out = gget(child, f"x/5xw {STATUS_ADDR:#x}")
    toks = re.findall(r"0x[0-9A-Fa-f]+", out)
    # Expected: ["0xADDR", "0xSTATUS", "0xD0", "0xD1", "0xD2", "0xD3"]
    if len(toks) < 6:
        raise RuntimeError(f"x/5xw parse failed:\n{out}")
    vals = [int(t, 16) for t in toks[-5:]]  # drop the printed address
    status = vals[0]
    data4  = vals[1:]
    return status, data4

def write_word(child, addr, val):
    gsend(child, f"set *(unsigned int*){addr:#x} = {val:#x}")

def combine128(ws):
    return (ws[0] << 96) | (ws[1] << 64) | (ws[2] << 32) | ws[3]

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
    names   = {1:"CORE_INSTR",2:"CORE_DATA",3:"AO_PERIPH",4:"PERIPH",
               5:"RAM0",6:"RAM1",7:"FLASH",8:"DMA_READ",9:"DMA_WRITE",10:"DMA_ADDR"}
    print(f"""── FRAME ─────────────────────────────
  source    : {names.get(src,src)}
  req_ts    : 0x{req_ts:08X}
  resp_ts   : 0x{resp_ts:04X}
  address   : 0x{addr:08X}
  data      : 0x{data:08X}
  byte_en   : 0x{be:X}
  we        : {we}
  valid     : {valid}
  gnt       : {gnt}
───────────────────────────────────────""")

# ------------ main flow ------------
def main():
    elf = "/home/riccardo/git/hep/x-heep/sw/build/main.elf"
    gdb = "/home/riccardo/tools/riscv/bin/riscv32-unknown-elf-gdb"

    child = pexpect.spawn(f"{gdb} --nx --quiet {elf}", timeout=300)
    child.expect(r"\(gdb\)\s*$")
    print("GDB prompt received.")

    # Setup exactly as richiesto
    for cmd in [
        "set target-async on",
        "set pagination off",
        "set confirm off",
        "set remotetimeout 2000",
        "target remote localhost:3333",
        "load",
        "c",
    ]:
        gsend(child, cmd)

    print(">>> Starting program and entering pump loop")

    frames = []

    while True:
        # siamo qui perché 'c' si è fermato (SIGTRAP/SIGINT)
        print(">>> HALT detected")

        # --- initial_pop: prima di leggere, forza un pop con ACK (EN|FRAME_READ = 0x5)
        write_word(child, CTRL_ADDR, EN_BIT | FRAME_READ_BIT)

        # --- drain loop ---
        while True:
            status, data4 = read_status_and_data(child)
            v128 = combine128(data4)
            frames.append((status, data4))
            dump_frame(v128)

            # Se è EMPTY, leggi comunque (già fatto) e termina il drain
            if status & EMPTY_BIT:
                print(">>> STATUS: EMPTY → stop draining.")
                break

            # Altrimenti chiedi il prossimo frame con un altro ACK
            write_word(child, CTRL_ADDR, EN_BIT | FRAME_READ_BIT)

        # resetta la FIFO (2 → 1)
        print(">>> Reset FIFO (2 → 1)")
        write_word(child, CTRL_ADDR, RST_FIFO_BIT)      # 0x2
        write_word(child, CTRL_ADDR, EN_BIT)             # 0x1

        # continua: o nuovo SIGTRAP o fine programma
        child.sendline("c")
        i = child.expect([r"\(gdb\)\s*$",
                          r"Program exited",
                          r"exited with code",
                          pexpect.EOF])
        if i == 0:
            # nuovo halt → ripeti ciclo
            continue
        else:
            print(">>> Program ended, exiting.")
            break

    child.sendline("quit")
    child.expect(pexpect.EOF)

if __name__ == "__main__":
    try:
        main()
    except pexpect.TIMEOUT:
        print("TIMEOUT from GDB. Se capita durante accessi memoria, verifica che l’hardware risponda anche a core fermo.")
        sys.exit(1)

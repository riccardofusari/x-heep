#!/usr/bin/env python3
import pexpect, re, sys

# --- MMIO map
CTRL_ADDR    = 0x30080000  # SNI_CTRL
STATUS_ADDR  = 0x30080004  # SNI_STATUS
DATA_ADDR    = 0x30080008  # SNI_DATA0..3

# --- CTRL bits (valori consolidati con il tuo RTL)
EN_BIT            = 1 << 0
RST_FIFO_BIT      = 1 << 1      # level — write 1 to hold reset, 0 to release
FRAME_READ_BIT    = 1 << 2      # rw1c — write 1 to ack/advance
ENABLE_GATING_BIT = 1 << 3      # (non usato ora)

# --- STATUS bits
EMPTY_BIT        = 1 << 0
FULL_BIT         = 1 << 1
FRAME_AVAIL_BIT  = 1 << 2       # sticky (frame_pending nel tuo RTL)

# ------------------------ GDB helpers ------------------------

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

def read_n_words(child, addr, n):
    out = gget(child, f"x/{n}xw {addr:#x}")
    # Esempio: "0xADDR: 0xW0 0xW1 0xW2 ..."
    toks = re.findall(r"0x[0-9A-Fa-f]{1,8}", out)
    # il primo è l'indirizzo, poi n parole
    words = [int(t,16) for t in toks[-n:]]
    if len(words) != n:
        raise RuntimeError(f"x/{n}xw parse failed:\n{out}")
    return words

def write_word(child, addr, val):
    gsend(child, f"set *(unsigned int*){addr:#x} = {val:#x}")

# ------------------------ Frame decode ------------------------

# DATA0..3 mapping: DATA0=MSW, DATA3=LSW (come nel tuo RTL)
def combine128(ws):
    # ws[0]=DATA0 (MSW) ... ws[3]=DATA3 (LSW)
    return (ws[0] << 96) | (ws[1] << 64) | (ws[2] << 32) | ws[3]

# Bitfield aderente alla tua typedef da 128b
def decode_frame(v):
    src     = (v >> 124) & 0xF
    req_ts  = (v >> 92)  & 0xFFFFFFFF
    resp_ts = (v >> 76)  & 0xFFFF
    addr    = (v >> 44)  & 0xFFFFFFFF
    data    = (v >> 12)  & 0xFFFFFFFF
    be      = (v >> 8)   & 0xF
    we      = (v >> 7)   & 0x1
    valid   = (v >> 6)   & 0x1
    gnt     = (v >> 5)   & 0x1
    return src, req_ts, resp_ts, addr, data, be, we, valid, gnt

NAMES = {
    0x1:"CORE_INSTR", 0x2:"CORE_DATA", 0x3:"AO_PERIPH", 0x4:"PERIPH",
    0x5:"RAM0", 0x6:"RAM1", 0x7:"FLASH",
    0x8:"DMA_READ", 0x9:"DMA_WRITE", 0xA:"DMA_ADDR",
}

def dump_frame(v, raw_words=None, status=None):
    src, req_ts, resp_ts, addr, data, be, we, valid, gnt = decode_frame(v)
    src_name = NAMES.get(src, f"{src}")
    if status is not None:
        empty = 1 if (status & EMPTY_BIT) else 0
        full  = 1 if (status & FULL_BIT) else 0
        fav   = 1 if (status & FRAME_AVAIL_BIT) else 0
        print(f"STATUS: empty={empty} full={full} frame_avail={fav}")
    print("── FRAME ─────────────────────────────")
    print(f"  source    : {src_name}")
    print(f"  req_ts    : 0x{req_ts:08X}")
    print(f"  resp_ts   : 0x{resp_ts:04X}")
    print(f"  address   : 0x{addr:08X}")
    print(f"  data      : 0x{data:08X}")
    print(f"  byte_en   : 0x{be:X}")
    print(f"  we        : {we}")
    print(f"  valid     : {valid}")
    print(f"  gnt       : {gnt}")
    if raw_words:
        print("  raw w[DATA0..3]: " + ", ".join(f"0x{x:08X}" for x in raw_words))
    print("───────────────────────────────────────")

# ------------------------ Actions ------------------------

def ack_frame(child):
    # Scrivi sempre EN | FRAME_READ (0x1 | 0x4 = 0x5)
    write_word(child, CTRL_ADDR, EN_BIT | FRAME_READ_BIT)

def reset_fifo(child):
    # RST=1 (tiene reset), poi EN=1 (reset rilasciato)
    write_word(child, CTRL_ADDR, RST_FIFO_BIT)
    write_word(child, CTRL_ADDR, EN_BIT)

# ------------------------ Main ------------------------

def main():
    elf = "/home/riccardo/git/hep/x-heep/sw/build/main.elf"
    gdb = "/home/riccardo/tools/riscv/bin/riscv32-unknown-elf-gdb"

    child = pexpect.spawn(f"{gdb} --nx --quiet {elf}", timeout=300)
    child.expect(r"\(gdb\)\s*$")
    print("GDB prompt received.")

    # Init GDB come richiesto
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

    while True:
        # Aspetta un HALT (SIGTRAP / breakpoint)
        i = child.expect([r"\(gdb\)\s*$", r"Program exited", pexpect.EOF])
        if i != 0:
            print(">>> Program ended, exiting.")
            break

        print(">>> HALT detected")

        # Primo sguardo allo status (opzionale, solo diagnostica)
        st0 = read_word(child, STATUS_ADDR)
        empty0 = bool(st0 & EMPTY_BIT)
        full0  = bool(st0 & FULL_BIT)
        fav0   = bool(st0 & FRAME_AVAIL_BIT)
        print(f">>> STATUS@halt: empty={int(empty0)} full={int(full0)} frame_avail={int(fav0)}")

        # LOOP DI DRAIN:
        # ogni iterazione:
        #   1) ack (EN|FRAME_READ)
        #   2) x/5xw STATUS_ADDR -> [STATUS, DATA0,DATA1,DATA2,DATA3]
        #   3) dump frame
        #   4) se STATUS.empty=1 → break
        while True:
            ack_frame(child)
            words5 = read_n_words(child, STATUS_ADDR, 5)
            status = words5[0]
            data4  = words5[1:]

            v128 = combine128(data4)
            dump_frame(v128, raw_words=data4, status=status)

            if status & EMPTY_BIT:
                print(">>> STATUS: EMPTY → stop draining.")
                break

        # Reset FIFO come da sequenza richiesta (2 → 1)
        print(">>> Reset FIFO (2 → 1)")
        reset_fifo(child)

        # Continua l'esecuzione: torneremo qui al prossimo SIGTRAP
        child.sendline("c")

    # Chiudi GDB
    child.sendline("quit")
    child.expect(pexpect.EOF)

if __name__ == "__main__":
    try:
        main()
    except pexpect.TIMEOUT:
        print("TIMEOUT: se succede su letture x/.. verifica che la CPU non sia gated in HALT.")
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e)
        sys.exit(2)

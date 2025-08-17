#!/usr/bin/env python3
import argparse, os, sys, time, re, struct, pexpect, signal

# ----------------- MMIO map (aggiorna se necessario) -----------------
CTRL_ADDR    = 0x30080000  # SNI_CTRL
STATUS_ADDR  = 0x30080004  # SNI_STATUS
DATA_ADDR    = 0x30080008  # SNI_DATA0..3

# --- CTRL bits (DPI_ENABLE_BIT/HALT_ON_FULL_BIT -> 0 se non esistono a CSR)
EN_BIT           = 1 << 0
RST_FIFO_BIT     = 1 << 1
FRAME_READ_BIT   = 1 << 2
#DPI_ENABLE_BIT   = 1 << 3   # metti 0 se NON hai mappato il bit in HW
#HALT_ON_FULL_BIT = 0        # es. 1<<4 se hai un enable per l'halt-on-full

# --- STATUS bits (usati solo in modalità legacy o per debug sporadico)
EMPTY_BIT        = 1 << 0
FULL_BIT         = 1 << 1
FRAME_AVAIL_BIT  = 1 << 2

BIN_PATH_DEFAULT = "sniffer_frames.bin"  # file prodotto dalla DPI-C

# ----------------- GDB helpers -----------------
def gsend(child, cmd):
    child.sendline(cmd)
    child.expect(r"\(gdb\)\s*$")

def gget(child, cmd):
    child.sendline(cmd)
    child.expect(r"\(gdb\)\s*$")
    return child.before.decode()

def write_word(child, addr, val):
    gsend(child, f"set *(unsigned int*){addr:#x} = {val:#x}")

def read_status_and_data(child):
    out = gget(child, f"x/5xw {STATUS_ADDR:#x}")
    toks = re.findall(r"0x[0-9A-Fa-f]+", out)
    if len(toks) < 6:
        raise RuntimeError(f"x/5xw parse failed:\n{out}")
    vals = [int(t, 16) for t in toks[-5:]]
    status, data4 = vals[0], vals[1:]
    return status, data4

def combine128(ws):  # DATA0=MSW
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
    print(f"src={names.get(src,src):>10}  ts={req_ts:08X}/{resp_ts:04X}  "
          f"addr={addr:08X}  data={data:08X}  be={be:X} we={we} v{valid} g{gnt}")

# ----------------- File tail (DPI) -----------------
def iter_frames_from_file(path, start_at_end=False, poll_sec=0.01):
    # aspetta che il file appaia
    while not os.path.exists(path):
        time.sleep(poll_sec)
    f = open(path, "rb", buffering=0)
    if start_at_end:
        f.seek(0, os.SEEK_END)
    buf = b""
    try:
        while True:
            chunk = f.read(4096)
            if not chunk:
                time.sleep(poll_sec)
                continue
            buf += chunk
            while len(buf) >= 16:
                frame = buf[:16]; buf = buf[16:]
                # la DPI scrive 4x uint32_t little-endian
                w0, w1, w2, w3 = struct.unpack("<IIII", frame)
                yield (w0, w1, w2, w3)
    finally:
        f.close()

# ----------------- Modalità LEGACY (solo se vuoi il vecchio flusso) -----------------
def run_legacy(child):
    print(">>> LEGACY mode: halt → ack/read → continue")
    frames = 0
    while True:
        print(">>> HALT detected")
        # initial pop
        write_word(child, CTRL_ADDR, EN_BIT | FRAME_READ_BIT)
        while True:
            status, data4 = read_status_and_data(child)
            v128 = combine128(data4)
            dump_frame(v128)
            frames += 1
            if status & EMPTY_BIT:
                print(">>> STATUS: EMPTY → stop draining.")
                break
            write_word(child, CTRL_ADDR, EN_BIT | FRAME_READ_BIT)
        print(">>> Reset FIFO")
        write_word(child, CTRL_ADDR, RST_FIFO_BIT)
        write_word(child, CTRL_ADDR, EN_BIT)

        child.sendline("c")
        i = child.expect([r"\(gdb\)\s*$",
                          r"Program exited",
                          r"exited with code",
                          pexpect.EOF])
        if i == 0:
            continue
        else:
            print(f">>> Program ended. Frames: {frames}")
            break

# ----------------- Modalità DPI -----------------
def run_dpi(child, bin_path, max_frames=None, status_interval=0.0):
    # Lato DPI è già attiva via parametro RTL. NIENTE MMIO qui.
    print(">>> DPI mode: start target, stamo a parti pa tangente")
    child.sendline("continue")  # niente expect del prompt

    print(f">>> DPI mode: streaming da '{bin_path}' (Ctrl-C per uscire)")
    n = 0
    last_status_t = time.time()

    # attesa soft del file (max 5s)
    deadline = time.time() + 5.0
    while not os.path.exists(bin_path) and time.time() < deadline:
        time.sleep(0.05)
    if not os.path.exists(bin_path):
        print(f"ERRORE: '{bin_path}' non trovato. Sei nella stessa dir di Vtestharness?")
        print("Suggerimento: lancia lo script dalla cartella sim-verilator o passa --bin con path assoluto.")
        return

    try:
        for w0, w1, w2, w3 in iter_frames_from_file(bin_path, start_at_end=False):
            v128 = combine128([w0, w1, w2, w3])
            dump_frame(v128)
            n += 1
            if max_frames and n >= max_frames:
                print(f">>> Raggiunti {n} frame, stop.")
                break
            if status_interval > 0 and (time.time() - last_status_t) >= status_interval:
                try:
                    out = gget(child, f"x/wx {STATUS_ADDR:#x}")
                    m = re.search(r":\s*(0x[0-9A-Fa-f]+)", out)
                    if m:
                        st = int(m.group(1), 16)
                        print(f"[STATUS] empty={bool(st & EMPTY_BIT)} full={bool(st & FULL_BIT)} frame_av={bool(st & FRAME_AVAIL_BIT)}")
                except Exception:
                    pass
                last_status_t = time.time()
    except KeyboardInterrupt:
        print(f"\n>>> Interrotto. Frames letti: {n}")
    finally:
        try:
            child.sendline("\x03")  # ^C
            child.expect(r"\(gdb\)\s*$", timeout=1)
        except Exception:
            pass
        child.sendline("quit")
        try:
            child.expect(pexpect.EOF, timeout=1)
        except Exception:
            pass


# ----------------- main -----------------
def main():
    ap = argparse.ArgumentParser(description="Bus sniffer helper (DPI/Legacy)")
    ap.add_argument("--elf", default="/home/riccardo/git/hep/x-heep/sw/build/main.elf")
    ap.add_argument("--gdb", default="/home/riccardo/tools/riscv/bin/riscv32-unknown-elf-gdb")
    ap.add_argument("--mode", choices=["dpi","legacy"], default="dpi",
                    help="dpi = streaming via DPI file; legacy = halt/ack/read")
    ap.add_argument("--bin", default=BIN_PATH_DEFAULT, help="path del file prodotto dalla DPI")
    ap.add_argument("--max-frames", type=int, default=None, help="stop dopo N frame (solo dpi)")
    ap.add_argument("--status-interval", type=float, default=0.0,
                    help="stampa STATUS ogni X secondi (solo dpi)")
    args = ap.parse_args()

    child = pexpect.spawn(f"{args.gdb} --nx --quiet {args.elf}", timeout=300)
    child.expect(r"\(gdb\)\s*$")
    print("GDB prompt received.")

    for cmd in [
        "set target-async on",
        "set pagination off",
        "set confirm off",
        "set remotetimeout 2000",
        "target remote localhost:3333",
        "load",
    ]:
        gsend(child, cmd)

    if args.mode == "legacy":
        # parte e si ferma al primo halt; da lì segue il vecchio flusso
        gsend(child, "c")
        run_legacy(child)
    else:
        run_dpi(child, args.bin, args.max_frames, args.status_interval)

if __name__ == "__main__":
    try:
        main()
    except pexpect.TIMEOUT:
        print("TIMEOUT da GDB. Verifica OpenOCD/bridge e che l'hardware risponda.")
        sys.exit(1)

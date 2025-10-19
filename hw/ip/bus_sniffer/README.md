# Bus Sniffer + DPI + Python Helper — Quick Start

This README explains **what the bus sniffer does**, how the **DPI writer** works, what the **Python helper** is for, and the **exact steps** to apply the patch, build, run the Verilator simulation, and collect traces.

---

## What’s in here

* **Bus Sniffer IP (`hw/ip/bus_sniffer`)**
  A lightweight non-intrusive monitor that records transactions from several internal “channels” (core instruction/data, RAMs, DMA, peripherals, …).
  Each transaction is encoded into a **128-bit frame**:

  * `source_id, req_timestamp, resp_timestamp, address, data, byte_en, we, valid, gnt`
  * Frames are stored into an internal **FIFO** and surfaced through registers **and/or** streamed out via DPI for faster simulations.

* **DPI writer (`hw/ip/bus_sniffer/dpi/bus_sniffer_dpi.cc`)**
  When enabled, the sniffer “pops” frames from its FIFO and **pushes** them through a DPI-C function.
  A tiny C++ consumer writes:

  * `sniffer_frames.bin` (binary, 16 bytes per frame, 4×u32 little-endian)
  * `sniffer_frames.csv` (human-readable CSV with decoded fields)

  Optional console printing can be toggled with environment variables (see below).

* **Python helper (`test/verifheep/sniffer_dpi.py`)**
  Convenience script to:

  * run in **DPI mode**: tail the binary file and pretty-print frames as they appear
  * keep a **legacy mode** that drains the FIFO via MMIO/GDB (slower; mostly for debugging or for FPGA co-emulation)
  * connect to OpenOCD/GDB, `load`, and `continue` the target as usual

---

## Repository layout (relevant bits)

```
hw/ip/bus_sniffer/
  data/                 # HJSON register description
  rtl/                  # RTL: sniffer, FIFO, reg_top, packages
  dpi/                  # DPI-C consumer (writes .bin and .csv)
  bus_sniffer.sh        # one-shot generator for reg files + headers
  patches/
    x-heep-bus-sniffer.patch  # single patch to add/modify all files
    BASE_COMMIT               # exact base commit the patch applies to

test/verifheep/
  sniffer_dpi.py        # Python helper (DPI/legacy)
```

---

## What the DPI does (and how to control it)

* **DPI is enabled in RTL** via a parameter (already set in this branch).
  The sniffer pops from the FIFO and calls:

  ```c
  sniffer_dpi_push(stream_id=0, nwords=4, w0,w1,w2,w3);
  ```

* C++ consumer (single SPSC ring + background thread) writes:

  * `sniffer_frames.bin` — 16 bytes per frame (MSW first)
  * `sniffer_frames.csv` — `src,req_ts,resp_ts,address,data,be,we,valid,gnt`

* **Console prints** (to `stderr`) are *disabled by default*.
  To enable them at runtime (without recompiling):

  ```bash
  export SNIFFER_PRINT=1         # turn on console trace
  export SNIFFER_PRINT_EVERY=100 # (optional) print 1 every 100 frames
  ```

  To go back to silent mode, **unset** those variables.

* **Files are overwritten** on each new simulation run (fresh start).

---

## The Python helper

* **DPI mode** (recommended): it **does not** drain the FIFO via MMIO/GDB.
  It simply:

  1. connects to OpenOCD/GDB, `load` the ELF, `continue`;
  2. tails `sniffer_frames.bin` and decodes/prints frames on the host.

* **Legacy mode**: runs the old **halt → ack/read → continue** loop through the sniffer registers. This is much slower and generally **not needed** when DPI is on, but it’s useful for quick sanity checks on the register interface and mandatory for FPGA co-emulation.

Usage (examples further below).

---

## Apply the patch exactly (cleanly, every time)

This project ships a **single patch** you can apply to an **upstream X-HEEP** checkout.

### 0) Prerequisites

* Git, Python 3
* Verilator toolchain/environment used by X-HEEP
* OpenOCD and a RISC-V GDB in your `$PATH`

### 1) Clone upstream and create a **clean worktree** at the base commit

> The patch was generated against a specific upstream state; we store that hash in `BASE_COMMIT`.

```bash
git clone https://github.com/esl-epfl/x-heep.git
cd x-heep

# (Optional) add the patched repo as a sibling directory if you haven’t already
# ln -s /path/to/your/patched/repo ./x-heep-patched

# Read the base commit the patch is made against:
BASE=$(cat hw/ip/bus_sniffer/patches/BASE_COMMIT 2>/dev/null || true)
# If you’re running from a bare upstream clone, copy patches from your patched repo:
# cp -r ../x-heep-patched/hw/ip/bus_sniffer/patches hw/ip/bus_sniffer/

# Create a clean worktree at BASE (recommended) so patch applies 1:1
git worktree add ../x-heep-apply "$BASE"
cd ../x-heep-apply
```

> Why a worktree? Because your local tree might already have diverged; applying the patch to the exact base guarantees a clean apply.

### 2) Apply the patch

```bash
git apply --check ../x-heep/hw/ip/bus_sniffer/patches/x-heep-bus-sniffer.patch
git apply --whitespace=fix ../x-heep/hw/ip/bus_sniffer/patches/x-heep-bus-sniffer.patch #Only if needed
```

* If you see only “trailing whitespace” warnings: **that’s fine**.
* If the patch fails: you’re likely not at the right base. Re-check `BASE_COMMIT` and step (1).

### 3) Generate the register package and SW headers

From the worktree root:

```bash
(cd hw/ip/bus_sniffer && ./bus_sniffer.sh)
```

This runs `regtool.py` to generate:

* `rtl/bus_sniffer_reg_top.sv`, `rtl/bus_sniffer_reg_pkg.sv` (and inserts a couple of Verilator lint pragmas)
* `sw/device/lib/drivers/bus_sniffer/bus_sniffer_regs.h`

---

## Build & run (Verilator + OpenOCD + GDB)

This is the minimal flow (same sequence you used before):

```bash
# 0) generate mcu
make mcu-gen

# 1) build the simulation with JTAG DPI enabled
make verilator-sim FUSESOC_PARAM="--JTAG_DPI=1"

# 2) build the demo app
make app PROJECT=example_asm

# 3) run the sim (it will wait for OpenOCD/GDB depending on your config)
make run-app-verilator PROJECT=example_asm
```

In **another terminal** (from repo root):

```bash
# 4) start OpenOCD (same config we’ve been using)
export JTAG_VPI_PORT=4567
openocd -f ./tb/core-v-mini-mcu.cfg
```

In a **third terminal**:

```bash
# 5) start GDB and connect
$RISCV/bin/riscv32-unknown-elf-gdb ./sw/build/main.elf

# inside GDB:
set target-async on 
set pagination off
set confirm off
set remotetimeout 2000
target remote localhost:3333
load
c
```

* Simulation will start running your program.
* **DPI will silently write** `sniffer_frames.bin` and `sniffer_frames.csv` in the build/sim-verilator folder (e.g. `build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator/`).
* If you set `SNIFFER_PRINT=1`, you’ll also see periodic prints in the **simulator stderr**.

---

## Using the Python helper

From the **sim-verilator** directory:

```bash
# Tail DPI file and pretty-print frames (no MMIO draining):
../../../test/bus_sniffer/sniffer_dpi.py \
  --mode dpi \
  --elf /absolute/path/to/sw/build/main.elf \
  --gdb /absolute/path/to/riscv32-unknown-elf-gdb \
  --bin ./sniffer_frames.bin \
  --max-frames 1000
```

Legacy (register-drain) mode, if you need it:

```bash
../../../test/bus_sniffer/sniffer_dpi.py --mode legacy \
  --elf /absolute/path/to/sw/build/main.elf \
  --gdb /absolute/path/to/riscv32-unknown-elf-gdb
```

> If you see “Cannot access memory” in legacy mode, it usually means OpenOCD/GDB didn’t attach yet, or the target isn’t running.

---

## Notes & Tips

* **Duplicates in CSV** can be expected for some flows (e.g. instruction fetch visible on both `CORE_INSTR` and backing `RAM0` channel). That’s **not** an RTL bug; it reflects the system architecture.
* **No halt/gating requirement**: the DPI drain runs continuously and does **not** require the CPU to be halted to export frames.
* **Disable DPI quickly**: in RTL, you can set the sniffer parameter `DPI_ENABLE = 1'b0` for a pure MMIO-only flow.
* **Addresses**: the sniffer registers live at `0x3008_0000` by default:

  * `SNI_CTRL` @ `+0x00` (enable, reset FIFO, frame\_ack, …)
  * `SNI_STATUS` @ `+0x04` (empty/full/frame\_avail)
  * `SNI_DATA0..3` @ `+0x08 .. +0x14` (DATA0 is MSW)
* **Reg generation is mandatory** after patching: always run `hw/ip/bus_sniffer/bus_sniffer.sh` once.
* **Trailing whitespace warnings** on `git apply` are harmless. If you want zero noise, use `--whitespace=fix`.
* **Clean rebuild** in case of weird sim behavior:

  ```bash
  make clean
  rm -rf build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator
  make verilator-sim FUSESOC_PARAM="--JTAG_DPI=1"
  ```

---

## One-shot: Everything with the following commands

```bash
# fresh upstream clone
git clone https://github.com/esl-epfl/x-heep.git
cd x-heep
git checkout feature-bus-sniffer
git worktree add ../x-heep-apply $(cat hw/ip/bus_sniffer/patches/BASE_COMMIT)
cd ../x-heep-apply
git apply --whitespace=fix ../x-heep/hw/ip/bus_sniffer/patches/x-heep-bus-sniffer.patch
(cd hw/ip/bus_sniffer && ./bus_sniffer.sh)
make mcu-gen
make verilator-sim FUSESOC_PARAM="--JTAG_DPI=1"
make app PROJECT=example_asm
make run-app-verilator PROJECT=example_asm
```

OpenOCD & GDB in two more terminals, then collect `sniffer_frames.csv` in the sim output directory.

---

If you get stuck at any step, check:

* that you’re on the **correct base commit** (patch must be applied to that),
* that you ran the **register generator** script once,
* that **OpenOCD** is up and GDB is connected when using the Python helper.

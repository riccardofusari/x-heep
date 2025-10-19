#!/usr/bin/env python3
"""
Driver Python per x-heep bus_sniffer (senza firmware):
- Avvia GDB, carica ELF, configura lo sniffer.
- Mette la CPU in run in modo NON-BLOCCANTE.
- Genera traffico usando OpenOCD System Bus Access via telnet (mww/mdw) mentre la CPU corre,
  così le transazioni passano sul system bus e lo sniffer le cattura (DPI -> sniffer_frames.csv/bin).

Modalità:
  --method openocd-sba  (consigliata): telnet OpenOCD mww/mdw a CPU in run
  --method gdb-set      (servizio)   : GDB set* (richiede halt; in genere NON si vede nello sniffer)

Opzioni di attesa:
  --linger-sec N                : tieni vivo il driver per N secondi prima di chiudere
  --until-idle-csv PATH --idle-timeout S : resta vivo finché PATH cresce; chiudi dopo S secondi di inattività

Nota: i file DPI si chiamano tipicamente 'sniffer_frames.csv' e 'sniffer_frames.bin'
      nella cartella di simulazione Verilator (sim-verilator). Non 'sniffer_dpi.csv'.
"""
from __future__ import annotations
import argparse
import os
import socket
import re
import sys
import time
from dataclasses import dataclass
from typing import Optional, List

import pexpect

# ----------------- MMIO base & bits -----------------
CTRL_ADDR   = 0x3008_0000  # SNI_CTRL
STATUS_ADDR = 0x3008_0004  # SNI_STATUS

# CTRL bits
EN_BIT            = 1 << 0
RST_FIFO_BIT      = 1 << 1
FRAME_READ_BIT    = 1 << 2
ENABLE_GATING_BIT = 1 << 3
DPI_EN_BIT        = 1 << 4  # richiede supporto runtime in HJSON/RTL (altrimenti serve param a compile-time)

# STATUS bits
EMPTY_BIT       = 1 << 0
FULL_BIT        = 1 << 1
FRAME_AVAIL_BIT = 1 << 2

# ----------------- GDB wrapper -----------------
class GDBSession:
    def __init__(self, gdb: str, elf: str, timeout_s: int = 300):
        self.child = pexpect.spawn(f"{gdb} --nx --quiet {elf}", timeout=timeout_s)
        self._expect_prompt()

    def _expect_prompt(self):
        self.child.expect(r"\(gdb\)\s*$")

    def send(self, cmd: str):
        self.child.sendline(cmd)
        self._expect_prompt()

    def send_noblock(self, cmd: str):
        self.child.sendline(cmd)

    def quit(self):
        try:
            self.child.sendline("quit")
            self.child.expect(pexpect.EOF, timeout=2)
        except Exception:
            pass

# ----------------- OpenOCD telnet (4444) -----------------
class OpenOCDTelnet:
    def __init__(self, host="127.0.0.1", port=4444, timeout=2.0):
        self.s = socket.create_connection((host, port), timeout)
        self.s.settimeout(timeout)
        self._read_until_prompt()

    def _read_until_prompt(self) -> str:
        data = b""
        while True:
            try:
                chunk = self.s.recv(4096)
                if not chunk:
                    break
                data += chunk
                if data.endswith(b"> "):
                    break
            except socket.timeout:
                break
        return data.decode(errors="ignore")

    def cmd(self, line: str) -> str:
        self.s.sendall((line + "\n").encode())
        return self._read_until_prompt()

    def mww(self, addr: int, val: int) -> str:
        return self.cmd(f"mww 0x{addr:08x} 0x{val & 0xFFFF_FFFF:08x}")

    def mdw(self, addr: int, count: int = 1) -> List[int]:
        out = self.cmd(f"mdw 0x{addr:08x} {count}")
        vals = [int(m.group(1), 16) for m in re.finditer(r"0x[0-9A-Fa-f]+:\s*(0x[0-9A-Fa-f]+)", out)]
        return vals

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass

# ----------------- Driver -----------------
@dataclass
class DriverConfig:
    gdb: str
    elf: str
    target: str = "localhost:3333"
    mode: str = "dpi"              # "dpi" | "legacy"
    halt_on_connect: bool = False

    # OpenOCD telnet
    ocd_host: str = "127.0.0.1"
    ocd_port: int = 4444
    prefer_sba: bool = True

    # Traffico
    method: str = "openocd-sba"     # "openocd-sba" | "gdb-set"
    dma_base: int = 0x20010000
    count: int = 16
    stride: int = 4
    pattern: str = "inc"            # "inc" | "const"
    const_val: int = 0xCAFEBABE
    tgap_us: int = 0                # gap tra write SBA per separare i frame

    # Marker
    marker_start: Optional[int] = None
    marker_end:   Optional[int] = None
    marker_val_start: int = 0xA5A5A5A5
    marker_val_end:   int = 0x5A5A5A5A

    # Attesa/uscita
    linger_sec: float = 0.0
    until_idle_csv: Optional[str] = None
    idle_timeout: float = 2.0

class Driver:
    def __init__(self, cfg: DriverConfig):
        self.cfg = cfg
        self.gdb = GDBSession(cfg.gdb, cfg.elf)
        self.ocd: Optional[OpenOCDTelnet] = None

    # --- Session management ---
    def connect_and_load(self):
        for cmd in [
            "set target-async on",
            "set pagination off",
            "set confirm off",
            "set remotetimeout 10000",
            f"target remote {self.cfg.target}",
            "load",
        ]:
            self.gdb.send(cmd)
        if not self.cfg.halt_on_connect:
            self.continue_run(nonblocking=True)

    def continue_run(self, nonblocking=True):
        if nonblocking:
            self.gdb.send_noblock("c")
        else:
            self.gdb.send("c")

    def halt(self):
        self.gdb.child.sendcontrol("c")
        self.gdb._expect_prompt()

    # --- SBA helpers ---
    def ensure_ocd(self):
        if self.ocd is None:
            self.ocd = OpenOCDTelnet(self.cfg.ocd_host, self.cfg.ocd_port)
            if self.cfg.prefer_sba:
                try:
                    self.ocd.cmd("riscv set_prefer_sba on")
                except Exception:
                    pass

    def sba_write32(self, addr: int, val: int):
        self.ensure_ocd()
        self.ocd.mww(addr, val)

    def sba_read32(self, addr: int) -> int:
        self.ensure_ocd()
        vals = self.ocd.mdw(addr, 1)
        return vals[0] if vals else 0

    def sba_write_burst(self, base: int, count: int, stride: int,
                        pattern: str = "inc", const_val: int = 0xDEADBEEF,
                        tgap_us: int = 0):
        val = const_val & 0xFFFF_FFFF
        for i in range(count):
            addr = base + i * stride
            if pattern == "inc":
                val = i & 0xFFFF_FFFF
            self.sba_write32(addr, val)
            if tgap_us:
                time.sleep(tgap_us / 1e6)

    # --- Sniffer control (via GDB, subito dopo load quando la CPU è in halt) ---
    def set_sniffer_gdb(self, enable=True, dpi_enable=True, reset_fifo=True):
        ctrl = (EN_BIT if enable else 0) | (DPI_EN_BIT if dpi_enable else 0) | (RST_FIFO_BIT if reset_fifo else 0)
        self.gdb.send(f"set *(unsigned int*){CTRL_ADDR:#x} = {ctrl:#x}")

    # --- STATUS via SBA (safe a CPU in run) ---
    def read_sniffer_status_sba(self) -> int:
        return self.sba_read32(STATUS_ADDR)

    # --- markers ---
    def sba_marker(self, addr: int, val: int):
        self.sba_write32(addr, val)

    def close(self):
        try:
            if self.ocd: self.ocd.close()
        finally:
            self.gdb.quit()

# ----------------- Utilità attesa -----------------

def wait_until_idle(csv_path: str, idle_s: float):
    last_size = os.path.getsize(csv_path) if os.path.exists(csv_path) else 0
    last_t = time.time()
    while True:
        time.sleep(0.1)
        now = time.time()
        size = os.path.getsize(csv_path) if os.path.exists(csv_path) else 0
        if size > last_size:
            last_size = size
            last_t = now
        if (now - last_t) >= idle_s:
            break

# ----------------- CLI -----------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="x-heep bus_sniffer driver (OpenOCD SBA)")
    ap.add_argument("--elf", required=True)
    ap.add_argument("--gdb", required=True)
    ap.add_argument("--target", default="localhost:3333")
    ap.add_argument("--mode", choices=["dpi", "legacy"], default="dpi")
    ap.add_argument("--halt-on-connect", action="store_true")

    ap.add_argument("--method", choices=["openocd-sba", "gdb-set"], default="openocd-sba")
    ap.add_argument("--ocd-host", default="127.0.0.1")
    ap.add_argument("--ocd-port", type=int, default=4444)
    ap.add_argument("--no-prefer-sba", dest="prefer_sba", action="store_false")

    ap.add_argument("--dma-base", type=lambda s:int(s,0), default=0x20010000)
    ap.add_argument("--count", type=int, default=16)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--pattern", choices=["inc","const"], default="inc")
    ap.add_argument("--const-val", type=lambda s:int(s,0), default=0xCAFEBABE)
    ap.add_argument("--tgap-us", type=int, default=0)

    ap.add_argument("--marker-start", type=lambda s:int(s,0), default=None)
    ap.add_argument("--marker-end",   type=lambda s:int(s,0), default=None)
    ap.add_argument("--marker-val-start", type=lambda s:int(s,0), default=0xA5A5A5A5)
    ap.add_argument("--marker-val-end",   type=lambda s:int(s,0), default=0x5A5A5A5A)

    ap.add_argument("--linger-sec", type=float, default=0.0)
    ap.add_argument("--until-idle-csv", type=str, default=None)
    ap.add_argument("--idle-timeout", type=float, default=2.0)
    return ap.parse_args()

# ----------------- main -----------------

def main():
    args = parse_args()
    cfg = DriverConfig(
        gdb=args.gdb,
        elf=args.elf,
        target=args.target,
        mode=args.mode,
        halt_on_connect=args.halt_on_connect,
        ocd_host=args.ocd_host,
        ocd_port=args.ocd_port,
        prefer_sba=args.prefer_sba,
        method=args.method,
        dma_base=args.dma_base,
        count=args.count,
        stride=args.stride,
        pattern=args.pattern,
        const_val=args.const_val,
        tgap_us=args.tgap_us,
        marker_start=args.marker_start,
        marker_end=args.marker_end,
        marker_val_start=args.marker_val_start,
        marker_val_end=args.marker_val_end,
        linger_sec=args.linger_sec,
        until_idle_csv=args.until_idle_csv,
        idle_timeout=args.idle_timeout,
    )

    drv = Driver(cfg)
    try:
        print("[driver] Connecting to OpenOCD and loading ELF…")
        drv.connect_and_load()

        print(f"[driver] Configuring sniffer: EN=1, DPI_EN={'1' if args.mode=='dpi' else '0'}")
        drv.set_sniffer_gdb(enable=True, dpi_enable=(args.mode=='dpi'), reset_fifo=True)

        if not args.halt_on_connect:
            print("[driver] Continuing CPU execution (non-blocking)…")
            drv.continue_run(nonblocking=True)

        if cfg.method == "openocd-sba" and cfg.marker_start is not None:
            print(f"[driver] SBA marker start @ {cfg.marker_start:#x} = {cfg.marker_val_start:#x}")
            drv.sba_marker(cfg.marker_start, cfg.marker_val_start)

        if cfg.method == "openocd-sba":
            print(f"[driver] SBA write burst: base={cfg.dma_base:#x} count={cfg.count} stride={cfg.stride} pattern={cfg.pattern} tgap_us={cfg.tgap_us}")
            drv.sba_write_burst(cfg.dma_base, cfg.count, cfg.stride, cfg.pattern, cfg.const_val, cfg.tgap_us)
        else:
            if not args.halt_on_connect:
                print("[driver] GDB-set richiesto: fermo il core…")
                drv.halt()
            print(f"[driver] GDB-set write burst: base={cfg.dma_base:#x} count={cfg.count} stride={cfg.stride} pattern={cfg.pattern}")
            for i in range(cfg.count):
                addr = cfg.dma_base + i * cfg.stride
                val  = (i & 0xFFFF_FFFF) if cfg.pattern == "inc" else (cfg.const_val & 0xFFFF_FFFF)
                drv.gdb.send(f"set *(unsigned int*){addr:#x} = {val:#x}")

        if cfg.method == "openocd-sba" and cfg.marker_end is not None:
            print(f"[driver] SBA marker end @ {cfg.marker_end:#x} = {cfg.marker_val_end:#x}")
            drv.sba_marker(cfg.marker_end, cfg.marker_val_end)

        # Attesa opzionale
        if cfg.until_idle_csv:
            print(f"[driver] Waiting until CSV idle: {cfg.until_idle_csv} (idle {cfg.idle_timeout}s)…")
            wait_until_idle(cfg.until_idle_csv, cfg.idle_timeout)
        if cfg.linger_sec > 0:
            print(f"[driver] Lingering for {cfg.linger_sec}s…")
            time.sleep(cfg.linger_sec)

        # STATUS via SBA (non blocca)
        try:
            st = drv.read_sniffer_status_sba()
            print(f"[driver] STATUS (SBA): empty={(st & EMPTY_BIT)!=0} full={(st & FULL_BIT)!=0} frame_av={(st & FRAME_AVAIL_BIT)!=0}")
        except Exception as e:
            print(f"[driver] STATUS via SBA failed: {e}")

    except pexpect.TIMEOUT:
        print("TIMEOUT from GDB. Check OpenOCD/bridge and target readiness.")
        sys.exit(1)
    finally:
        drv.close()

if __name__ == "__main__":
    main()

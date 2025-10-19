#!/usr/bin/env python3
"""
Manual-style SBA driver + monitor/scoreboard for x-heep bus_sniffer.

What it does (no GDB reads while running):
  1) Connects to OpenOCD telnet (4444), optionally 'resume' the CPU.
  2) Enables sniffer (EN) and DPI_EN via MMIO write to SNI_CTRL (0x3008_0000).
  3) Generates a burst of SBA writes (mww) to a given base/stride/pattern.
  4) EITHER:
      - parses sniffer_frames.csv (if DPI is active) and verifies expected frames, OR
      - drains frames LEGACY-style via MMIO (using SBA) and verifies them.

This is a self-contained "Day 1 sanity" tool: it proves that what we drive appears in the sniffer.

CSV columns expected: src,req_ts,resp_ts,address,data,be,we,valid,gnt

Usage examples:
  # DPI path (recommended if DPI compiled):
  python3 tb/sba_probe_and_check.py \
    --resume \
    --enable-dpi \
    --base 0x20010000 --count 16 --stride 4 --pattern inc \
    --csv build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator/sniffer_frames.csv \
    --wait-csv 2.0 --timeout 5.0

  # Legacy drain via SBA (works even if DPI not compiled):
  python3 tb/sba_probe_and_check.py \
    --resume --enable-dpi \
    --base 0x20010000 --count 8 --stride 4 --pattern inc \
    --drain-legacy 16
"""
from __future__ import annotations
import argparse
import os
import re
import socket
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

SNI_CTRL   = 0x3008_0000
SNI_STATUS = 0x3008_0004
SNI_DATA0  = 0x3008_0008  # through +0x14

EN_BIT      = 1 << 0
DPI_EN_BIT  = 1 << 4
FRAME_READ  = 1 << 2

class OpenOCDTelnet:
    def __init__(self, host: str = "127.0.0.1", port: int = 4444, timeout: float = 2.0):
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
        return [int(m.group(1), 16) for m in re.finditer(r"0x[0-9A-Fa-f]+:\s*(0x[0-9A-Fa-f]+)", out)]

    def close(self):
        try: self.s.close()
        except Exception: pass

@dataclass
class Burst:
    base: int
    count: int
    stride: int
    pattern: str  # inc|const
    const_val: int

    def expected_map(self) -> Dict[int, int]:
        exp = {}
        for i in range(self.count):
            addr = self.base + i*self.stride
            val = (i & 0xFFFF_FFFF) if self.pattern == "inc" else (self.const_val & 0xFFFF_FFFF)
            exp[addr] = val
        return exp

# -------- CSV monitor/scoreboard --------

def parse_csv(csv_path: str) -> List[dict]:
    rows: List[dict] = []
    if not os.path.exists(csv_path):
        return rows
    with open(csv_path, "r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        idx = {name: i for i, name in enumerate(header)}
        for line in f:
            parts = line.strip().split(",")
            if len(parts) != len(header):
                continue
            try:
                row = {
                    "address": int(parts[idx["address"]], 16),
                    "data": int(parts[idx["data"]], 16),
                    "we": int(parts[idx["we"]]),
                    "be": int(parts[idx["be"]], 16),
                    "src": int(parts[idx["src"]]),
                }
                rows.append(row)
            except Exception:
                continue
    return rows

def scoreboard_from_csv(csv_path: str, burst: Burst, verbose: bool = True) -> Tuple[bool, List[str]]:
    exp = burst.expected_map()
    rows = parse_csv(csv_path)
    seen: Dict[int, int] = {}
    for r in rows:
        if r["we"] != 1:
            continue
        a = r["address"]
        if a in exp and a not in seen:
            seen[a] = r["data"]
            if verbose:
                print(f"[CSV] addr={a:#010x} data={r['data']:#010x} src={r['src']} be={r['be']:x}")
        if len(seen) == len(exp):
            break
    missing = [f"miss {a:#010x}" for a in exp if a not in seen]
    mismatch = [f"mismatch {a:#010x}: got {seen[a]:#010x} exp {exp[a]:#010x}" for a in seen if seen[a] != exp[a]]
    ok = (len(missing) == 0 and len(mismatch) == 0)
    return ok, missing + mismatch

# -------- Legacy drain via SBA (no DPI needed) --------

def combine128(ws: List[int]) -> int:
    return (ws[0]<<96)|(ws[1]<<64)|(ws[2]<<32)|ws[3]

def dump_frame(v: int):
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
    print(f"src={names.get(src,src):>10} ts={req_ts:08X}/{resp_ts:04X} addr={addr:08X} data={data:08X} be={be:X} we={we} v{valid} g{gnt}")

def drain_legacy_sba(ocd: OpenOCDTelnet, max_frames: int = 16) -> List[int]:
    frames: List[int] = []
    for _ in range(max_frames):
        ocd.mww(SNI_CTRL, EN_BIT | FRAME_READ)
        st = ocd.mdw(SNI_STATUS, 1)[0]
        d = ocd.mdw(SNI_DATA0, 4)
        v = combine128(d)
        frames.append(v)
        if st & 0x1:  # EMPTY
            break
    for v in frames:
        dump_frame(v)
    return frames

# -------- Main flow --------

def main():
    ap = argparse.ArgumentParser(description="SBA probe + scoreboard for x-heep bus_sniffer")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4444)
    ap.add_argument("--resume", action="store_true", help="send 'resume' before driving")
    ap.add_argument("--enable-dpi", action="store_true", help="write EN|DPI_EN to SNI_CTRL before driving")
    ap.add_argument("--base", type=lambda s:int(s,0), default=0x20010000)
    ap.add_argument("--count", type=int, default=16)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--pattern", choices=["inc","const"], default="inc")
    ap.add_argument("--const-val", type=lambda s:int(s,0), default=0xCAFEBABE)
    ap.add_argument("--tgap-us", type=int, default=200)
    ap.add_argument("--csv", type=str, default=None, help="path to sniffer_frames.csv (DPI)")
    ap.add_argument("--wait-csv", type=float, default=1.0, help="seconds to wait after burst for CSV to grow")
    ap.add_argument("--timeout", type=float, default=3.0, help="overall extra wait seconds before parsing CSV")
    ap.add_argument("--drain-legacy", type=int, default=0, help="if >0, drain up to N frames via SBA MMIO (no DPI needed)")
    args = ap.parse_args()

    ocd = OpenOCDTelnet(args.host, args.port, 2.0)

    # Prefer SBA on RISC-V (ignore errors if not supported)
    try:
        print(ocd.cmd("riscv set_prefer_sba on").strip())
    except Exception:
        pass

    if args.resume:
        print(ocd.cmd("resume").strip())

    # Enable sniffer + DPI_EN (runtime) if requested
    if args.enable_dpi:
        print(f"[SBA] enable EN|DPI_EN @ {SNI_CTRL:#010x}")
        ocd.mww(SNI_CTRL, EN_BIT | DPI_EN_BIT)
        rb = ocd.mdw(SNI_CTRL, 1)[0]
        print(f"[SBA] SNI_CTRL readback: {rb:#010x}")
    else:
        # at least turn EN on
        ocd.mww(SNI_CTRL, EN_BIT)

    # Generate the burst
    burst = Burst(args.base, args.count, args.stride, args.pattern, args.const_val)
    print(f"[SBA] burst: base={args.base:#010x} count={args.count} stride={args.stride} pattern={args.pattern} tgap_us={args.tgap_us}")
    exp = burst.expected_map()
    for i,(addr,val) in enumerate(exp.items()):
        ocd.mww(addr, val)
        if args.tgap_us:
            time.sleep(args.tgap_us/1e6)

    # Option A: drain legacy via SBA (works even if DPI is off)
    if args.drain_legacy > 0:
        print(f"[LEGACY] draining up to {args.drain_legacy} frames via SBA…")
        drain_legacy_sba(ocd, args.drain_legacy)

    # Option B: parse CSV (DPI)
    if args.csv:
        csv_path = os.path.abspath(args.csv)
        # Wait a bit for DPI to flush
        t_end = time.time() + max(args.wait_csv, 0.0) + max(args.timeout, 0.0)
        last = -1
        while time.time() < t_end:
            try:
                size = os.path.getsize(csv_path)
            except FileNotFoundError:
                size = -1
            if size != last and size > 0:
                last = size
                time.sleep(args.wait_csv)
                break
            time.sleep(0.1)
        if not os.path.exists(csv_path):
            print(f"[CSV] file not found: {csv_path}")
        else:
            ok, issues = scoreboard_from_csv(csv_path, burst, verbose=True)
            print("[SCOREBOARD]", "PASS" if ok else "FAIL")
            for i in issues:
                print(" -", i)

    ocd.close()

if __name__ == "__main__":
    main()

# Save this as test_legacy_mode.py
from driver import XHeepSBADriver, OperationMode
import time
import socket
import json

# ==============================
# Config bus sniffer (Legacy)
# ==============================

SNIFFER_BASE_ADDR       = 0x30080000

SNIFFER_CTRL_ADDR       = SNIFFER_BASE_ADDR + 0x00
SNIFFER_STATUS_ADDR     = SNIFFER_BASE_ADDR + 0x04
SNIFFER_DATA0_ADDR      = SNIFFER_BASE_ADDR + 0x08
SNIFFER_DATA1_ADDR      = SNIFFER_BASE_ADDR + 0x0C
SNIFFER_DATA2_ADDR      = SNIFFER_BASE_ADDR + 0x10
SNIFFER_DATA3_ADDR      = SNIFFER_BASE_ADDR + 0x14

# STATUS bits
STATUS_EMPTY_BIT        = 1 << 0
STATUS_FULL_BIT         = 1 << 1
STATUS_FRAME_AVAIL_BIT  = 1 << 2

# CTRL bits (come da hjson)
CTRL_FRAME_READ_BIT     = 1 << 2  # FRAME_READ bit

# Singola transazione SBA “speciale”
TEST_WRITE_ADDR    = 0x00001000
TEST_WRITE_DATA    = 0xBEEFBEEF

# Porta Telnet di OpenOCD per SBA
OPENOCD_TELNET_HOST = "localhost"
OPENOCD_TELNET_PORT = 4444

FRAME_DUMP_FILE    = "legacy_sniffer_frames.json"


# ==============================
# Channel map (source_id → nome)
# ==============================

CHANNEL_MAP = {
    0: "CH_CORE_INSTR",
    1: "CH_CORE_DATA",
    2: "CH_AO_PERIPH",
    3: "CH_PERIPH",
    4: "CH_RAM0",
    5: "CH_RAM1",
    6: "CH_FLASH",
    7: "CH_DMA_READ",
    8: "CH_DMA_WRITE",
    9: "CH_DMA_ADDR",
}


# ==============================
# Utils: SBA via Telnet
# ==============================

def sba_single_write_via_telnet(addr: int, data: int,
                                host: str = OPENOCD_TELNET_HOST,
                                port: int = OPENOCD_TELNET_PORT):
    """
    UNA singola write SBA via Telnet/OpenOCD:
      mww <addr> <data>
    Deve essere chiamata QUANDO la CPU è in esecuzione.
    """
    print(f"💣 [SBA] mww 0x{addr:08x} 0x{data:08x} via Telnet ({host}:{port})")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect((host, port))

        # Consuma eventuale banner iniziale
        try:
            time.sleep(0.2)
            _ = s.recv(4096)
        except socket.timeout:
            pass

        cmd = f"mww 0x{addr:08x} 0x{data:08x}\n".encode("ascii")
        s.sendall(cmd)

        # Leggi eventuale risposta (non fondamentale)
        try:
            time.sleep(0.2)
            _ = s.recv(4096)
        except socket.timeout:
            pass

    print("✅ [SBA] Write command sent.")


# ==============================
# Decode frame RTL
# ==============================

def decode_sniffer_frame(w0: int, w1: int, w2: int, w3: int) -> dict:
    """
    Decodifica i 4 word 32-bit nel frame RTL bus_sniffer_frame_t.
    Layout:
      [127:124] source_id
      [123:108] req_timestamp (16b)
      [107:92]  resp_timestamp (16b)
      [91:60]   address (32b)
      [59:28]   data    (32b)
      [27:24]   byte_enable (4b)
      [23]      we
      [22]      valid
      [21]      gnt
      [20:0]    reserved
    """
    full = (w0 << 96) | (w1 << 64) | (w2 << 32) | w3

    source_id      = (full >> 124) & 0xF
    req_timestamp  = (full >> 108) & 0xFFFF
    resp_timestamp = (full >> 92)  & 0xFFFF
    address        = (full >> 60)  & 0xFFFFFFFF
    data           = (full >> 28)  & 0xFFFFFFFF
    byte_enable    = (full >> 24)  & 0xF
    we             = (full >> 23)  & 0x1
    valid          = (full >> 22)  & 0x1
    gnt            = (full >> 21)  & 0x1

    chan_name = CHANNEL_MAP.get(source_id, f"UNKNOWN({source_id})")

    return {
        "source_id": source_id,
        "channel": chan_name,
        "req_ts": req_timestamp,
        "resp_ts": resp_timestamp,
        "address": address,
        "data": data,
        "byte_enable": byte_enable,
        "we": bool(we),
        "valid": bool(valid),
        "gnt": bool(gnt),
        "raw_words": {
            "w0": w0,
            "w1": w1,
            "w2": w2,
            "w3": w3,
        },
    }


def pretty_print_decoded_frame(index: int, f: dict):
    rw = f["raw_words"]
    tr_type = "WRITE" if f["we"] else "READ"
    print(
        f"  📦 Frame {index:3d}: "
        f"{f['channel']:<12s} {tr_type:<5s} "
        f"addr=0x{f['address']:08X} data=0x{f['data']:08X} "
        f"be=0x{f['byte_enable']:X} "
        f"req_ts={f['req_ts']:6d} resp_dt={f['resp_ts']:6d} "
        f"(raw w0={rw['w0']:08X} w1={rw['w1']:08X} w2={rw['w2']:08X} w3={rw['w3']:08X})"
    )


# ==============================
# Drain FIFO (LEGACY handshake)
# ==============================

def drain_sniffer_fifo(driver: XHeepSBADriver, max_frames: int = 2048):
    """
    Svuota la FIFO in legacy mode:
      - finché EMPTY=0:
        - genera un impulso su FRAME_READ (CTRL)
        - legge DATA0..3
        - decodifica il frame
    NON usiamo FRAME_AVAIL, perché nel RTL attuale si alza solo dopo un pop.
    """
    read  = driver._gdb_read_word
    write = driver._gdb_write_word

    frames = []
    frame_count = 0

    print("\n📥 Draining sniffer FIFO by pulsing FRAME_READ until EMPTY=1...")

    while frame_count < max_frames:
        status = read(SNIFFER_STATUS_ADDR)
        empty  = bool(status & STATUS_EMPTY_BIT)

        if empty:
            print("ℹ️  FIFO EMPTY=1 → done.")
            break

        # 1) Impulso su FRAME_READ (read-modify-write)
        ctrl_val = read(SNIFFER_CTRL_ADDR)
        ctrl_val |= CTRL_FRAME_READ_BIT
        write(SNIFFER_CTRL_ADDR, ctrl_val)

        # Piccola pausa per permettere il pop
        time.sleep(0.001)

        # 2) Leggi i 4 word (frame RTL)
        w0 = read(SNIFFER_DATA0_ADDR)
        w1 = read(SNIFFER_DATA1_ADDR)
        w2 = read(SNIFFER_DATA2_ADDR)
        w3 = read(SNIFFER_DATA3_ADDR)

        decoded = decode_sniffer_frame(w0, w1, w2, w3)
        frames.append(decoded)
        pretty_print_decoded_frame(frame_count, decoded)

        frame_count += 1

    print(f"✅ Drained {frame_count} frame(s) from sniffer FIFO.")

    # Salva anche su file
    with open(FRAME_DUMP_FILE, "w") as f:
        json.dump(frames, f, indent=2)

    print(f"📝 Decoded frames saved to {FRAME_DUMP_FILE}")
    return frames


def check_beefbeef_in_frames(frames):
    """
    Controlla se esiste una transazione con data=0xBEEFBEEF.
    """
    target = TEST_WRITE_DATA & 0xFFFFFFFF
    found = False

    for i, fr in enumerate(frames):
        if fr["data"] == target:
            tr_type = "WRITE" if fr["we"] else "READ"
            print(
                f"🎯 Found 0x{target:08X} in frame {i}: "
                f"{fr['channel']} {tr_type} addr=0x{fr['address']:08X}"
            )
            found = True

    if not found:
        print("✅ 0xBEEFBEEF Has been found in decoded frames.")
    return found


# ==============================
# Main test (Legacy + sniffer)
# ==============================

def main():
    driver = None
    try:
        mode = OperationMode.LEGACY
        print(f"🚀 Starting Legacy Sniffer FIFO Test (GDB mode)...")

        print("Connecting to OpenOCD GDB server on localhost:3333...")
        driver = XHeepSBADriver(mode=mode)

        print("\n🔍 Testing connection...")
        if not driver.test_connection():
            print("❌ Cannot proceed - connection failed")
            return

        time.sleep(1)

        # --------------------------------------------------------------
        # 1) Configura bus sniffer per FIFO legacy (come run_fifo_test_via_gdb)
        # --------------------------------------------------------------
        fifo_ctl_addr = SNIFFER_CTRL_ADDR


        print(f"Writing 0x2 to 0x{fifo_ctl_addr:08x} (reset FIFO)")
        driver._gdb_write_word(fifo_ctl_addr, 0x2)
        time.sleep(0.05)

        print(f"Writing 0x11 to 0x{fifo_ctl_addr:08x} (disable DPI)")
        driver._gdb_write_word(fifo_ctl_addr, 0x10)
        time.sleep(0.05)

        # Enable filling (0x1)
        print(f"Writing 0x1 to 0x{fifo_ctl_addr:08x} (enable filling)")
        driver._gdb_write_word(fifo_ctl_addr, 0x1)
        time.sleep(0.05)

        # --------------------------------------------------------------
        # 2) Continua la CPU
        # --------------------------------------------------------------
        print("▶️  Continuing CPU execution...")
        driver._gdb_continue()

        # --------------------------------------------------------------
        # 3) ORA la CPU gira: fai la write SBA 0xBEEFBEEF
        #    (durante l'esecuzione, non prima!)
        # --------------------------------------------------------------
        # time.sleep(0.1)  # piccolo delay per sicurezza
        # sba_single_write_via_telnet(TEST_WRITE_ADDR, TEST_WRITE_DATA)

        # --------------------------------------------------------------
        # 4) Aspetta che il bus sniffer fermi il core (SIGTRAP/SIGINT)
        # --------------------------------------------------------------
        print("⏳ Waiting for stop (SIGTRAP/SIGINT) from bus sniffer...")
        try:
            payload, sig = driver._gdb_wait_for_stop(timeout=30.0)
            if sig == 5:
                print(f"✅ Received SIGTRAP (signal {sig}). Payload: {payload}")
            elif sig == 2:
                print(f"✅ Received SIGINT (signal {sig}). Payload: {payload}")
            else:
                print(f"⚠️  Target stopped with signal {sig}. Payload: {payload}")
        except Exception as e:
            print(f"❌ Did not get stop reply: {e}")
            return

        # --------------------------------------------------------------
        # 5) CPU ferma, FIFO piena: svuota FIFO e decodifica frame
        # --------------------------------------------------------------
        frames = drain_sniffer_fifo(driver)

        # 6) Cerca 0xBEEFBEEF nei frame
        check_beefbeef_in_frames(frames)

    except Exception as e:
        print(f"❌ Main error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            driver.close()

if __name__ == "__main__":
    main()

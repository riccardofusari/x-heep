# Save this as test_basic_traffic.py
from driver import XHeepSBADriver, OperationMode
from monitor import BusMonitor, BusTransaction, TransactionType
from scoreboard import BusScoreboard
import time
import os
import json


def analyze_sniffer_results(sba_start_time: int = None):
    """Analyze bus sniffer output after test completion with timing support"""
    print("\n" + "="*60)
    print("ANALYZING BUS SNIFFER RESULTS")
    if sba_start_time:
        print(f"Focusing on transactions after req_ts={sba_start_time}")
    print("="*60)

    csv_path = os.path.expanduser(
        "../../../../build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator/sniffer_frames.csv"
    )

    monitor = BusMonitor()
    all_transactions = monitor.parse_csv(csv_path)

    # Shadow vs RAM mirror check
    expected_pairs, actual_pairs = monitor.build_expected_and_actual_pairs(
        "shadow_write_data.json"
    )
    scoreboard = BusScoreboard()
    scoreboard.compare_shadow_vs_ram_mirror(expected_pairs, actual_pairs)

    if not all_transactions:
        print("❌ No transactions found in sniffer output")
        return False

    # Print overall statistics
    monitor.print_statistics()
    # monitor.print_timing_statistics()

    # Filter transactions by time if specified
    if sba_start_time:
        transactions = all_transactions
        print(
            f"\n Showing transactions after SBA operations started (req_ts >= {sba_start_time})"
        )
        monitor.print_transactions(limit=10, min_req_ts=sba_start_time)
    else:
        transactions = all_transactions
        # Try to auto-detect SBA start time (look for first PERIPH write)
        periph_writes = [
            t
            for t in all_transactions
            if t.channel == "PERIPH" and t.transaction_type == TransactionType.WRITE
        ]
        if periph_writes:
            sba_start_time = min(t.req_ts for t in periph_writes)
            print(f"Auto-detected SBA start around req_ts={sba_start_time}")
            transactions = monitor.filter_by_time_range(sba_start_time)

    # These should match the writes in your main() function
    expected_writes = [
        (0x00000000, 0xA5A5A5A5, "Burst write to address 0"),
        (0x00000004, 0x5A5A5A5A, "Burst write to address 4"),
        (0x00000008, 0xF0F0F0F0, "Burst write to address 8"),
        (0x0000000C, 0x0F0F0F0F, "Burst write to address C"),
        (0x2002FFF8, 0xA5A5A5A5, "DMA region write"),
        (0x2002FFFC, 0x5A5A5A5A, "DMA region write"),
        (0x20030000, 0xF0F0F0F0, "DMA region write"),
        (0x20030004, 0x0F0F0F0F, "DMA region write"),
        (0x00000ED4, 0xA5A5A5A5, "Data RAM write"),
        (0x00000ED8, 0x5A5A5A5A, "Data RAM write"),
        (0x00000EDC, 0xF0F0F0F0, "Data RAM write"),
        (0x00000EE0, 0x0F0F0F0F, "Data RAM write"),
    ]

    # Expected from real SBA writes performed by the driver
    for addr, data in XHeepSBADriver.performed_sba_writes:
        scoreboard.add_expected_write(
            addr, data, description=f"SBA write to 0x{addr:08x}"
        )

    # Observed from sniffer CSV
    scoreboard.add_observed_transactions(transactions)
    # scoreboard.print_results()

    return True


def test_dma_memcpy(driver):
    """
    DMA Test #1: DMA memory-to-memory transfer (SRAM0 → SRAM1).
    This configures the DMA via SBA driver and relies on the sniffer
    to capture read/write transactions.
    """

    print("\n" + "="*60)
    print("Running DMA Memory-to-Memory Transfer Test (SRAM0 → SRAM1)")
    print("="*60)

    # ----------------------------
    # DMA register map
    # ----------------------------
    DMA_BASE = 0x20030000
    DMA_SRC_PTR_OFFSET = 0x00
    DMA_DST_PTR_OFFSET = 0x04
    DMA_SIZE_OFFSET = 0x10   # Using SIZE_D1 = 16 from your notes
    DMA_MODE_OFFSET = 0x38   # 56 decimal
    DMA_STATUS_OFFSET = 0x14  # 20 decimal
    DMA_TRANSACTION_IFR_OFFSET = 0x60  # 96 decimal

    # ----------------------------
    # Test Buffers
    # ----------------------------
    SRAM0_BASE = 0x00000000     # RAM0_START_ADDR
    SRAM1_BASE = 0x00008000     # RAM1_START_ADDR
    LENGTH_WORDS = 16           # Transfer 16 words (64 bytes)

    # Prepare test pattern
    src_data = [(i * 0x11111111) & 0xFFFFFFFF for i in range(LENGTH_WORDS)]

    print(
        f"Writing source buffer ({LENGTH_WORDS} words) to SRAM0 @ 0x{SRAM0_BASE:08x}"
    )
    driver.write_burst(SRAM0_BASE, src_data)

    # ----------------------------
    # Configure DMA
    # ----------------------------
    print("Configuring DMA registers...")

    driver.write_word(DMA_BASE + DMA_SRC_PTR_OFFSET, SRAM0_BASE)
    driver.write_word(DMA_BASE + DMA_DST_PTR_OFFSET, SRAM1_BASE)
    driver.write_word(DMA_BASE + 0x0C, 16)
    driver.write_word(DMA_BASE + DMA_MODE_OFFSET, 0)
    # driver.write_word(DMA_BASE + DMA_SIZE_OFFSET, LENGTH_WORDS)

    # MODE = 1 → start DMA
    print("▶️ Starting DMA…")
    driver.write_word(DMA_BASE + DMA_MODE_OFFSET, 1)

    # ----------------------------
    # Wait for DMA completion
    # ----------------------------
    DMA_STATUS_WINDOW_DONE_RESVAL = 1
    DMA_STATUS_WINDOW_DONE_MASK = 0x1

    print("⏳ Waiting for DMA to complete…")

    timeout = 200  # number of polls
    poll_interval = 0.05  # seconds

    completed = False
    while timeout > 0:
        status = driver.read_word(DMA_BASE + DMA_STATUS_OFFSET)
        if (status & DMA_STATUS_WINDOW_DONE_MASK) == DMA_STATUS_WINDOW_DONE_RESVAL:
            completed = True
            break
        time.sleep(poll_interval)
        timeout -= 1

    if not completed:
        print("❌ DMA did NOT complete (timeout)")
        return False

    print("✅ DMA Completed Successfully")

    # ----------------------------
    # Verify destination buffer
    # ----------------------------
    print("Reading back destination SRAM1…")

    dst_data = []
    for i in range(LENGTH_WORDS):
        val = driver.read_word(SRAM1_BASE + i * 4)
        dst_data.append(val)

    if dst_data == src_data:
        print("✅ DMA memcpy PASS – data matches!")
        for i in range(LENGTH_WORDS):
            print(f"  {i:02d}: src=0x{src_data[i]:08x} dst=0x{dst_data[i]:08x}")
    else:
        print("❌ DMA memcpy FAIL – mismatch detected!")
        for i in range(LENGTH_WORDS):
            print(f"  {i:02d}: src=0x{src_data[i]:08x} dst=0x{dst_data[i]:08x}")

    return True


def find_sba_start_time() -> int:
    """
    Try to find when SBA operations started by looking for patterns
    Returns a suggested req_ts value to use as filter
    """
    csv_path = os.path.expanduser(
        "../../../../build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator/sniffer_frames.csv"
    )

    monitor = BusMonitor()
    transactions = monitor.parse_csv(csv_path)

    if not transactions:
        return 0

    # Look for PERIPH writes (typical for SBA)
    periph_writes = [
        t
        for t in transactions
        if t.channel == "PERIPH" and t.transaction_type == TransactionType.WRITE
    ]

    if periph_writes:
        first_periph_write = min(periph_writes, key=lambda t: t.req_ts)
        print(f"First PERIPH write at req_ts={first_periph_write.req_ts}")
        return first_periph_write.req_ts

    # Fallback: use the overall max timestamp minus a buffer
    max_ts = max(t.req_ts for t in transactions)
    suggested_start = max(0, max_ts - 1000)  # Go back 1000 cycles
    print(f"Suggested start time: req_ts={suggested_start}")
    return suggested_start


def main():
    driver = None
    try:
        # Forza direttamente la modalità SBA
        mode = OperationMode.SBA

        print(f"Starting SBA Traffic Generator...")
        print("Connecting to OpenOCD Telnet on localhost:4444...")
        driver = XHeepSBADriver(mode=mode)

        # Test connection first
        print("\nTesting connection...")
        if not driver.test_connection():
            print("❌ Cannot proceed - connection failed")
            return

        time.sleep(1)

        # === SBA branch (identico al ramo SBA precedente) ===
        print("\n" + "=" * 50)
        print("Generating READ-ONLY traffic for sniffer monitoring...")
        print("=" * 50)


        read_addresses_sram0 = [
            0x00000000,
            0x00000004,
            0x00000008,
            0x0000000C,
            0x00000010,
            0x00000014,
            0x00000018,
            0x0000001C,
            0x00000020,
            0x00000024,
            0x00000028,
            0x0000002C,
            0x00000030,
            0x00000034,
        ]
        read_addresses_sram1 = [
            0x00008000,
            0x00008004,
            0x00008008,
            0x0000800C,
            0x00008010,
            0x00008014,
            0x00008018,
            0x0000801C,
            0x00008020,
            0x00008024,
            0x00008028,
            0x0000802C,
            0x00008030,
            0x00008034,
        ]


        driver.enable_dpi()
        print("\nReading SRAM0 data before running DMA Test #1 (SRAM0 → SRAM1)")
        driver.generate_read_only_traffic(read_addresses_sram0)
        print("\nReading SRAM1 data before running DMA Test #1 (SRAM0 → SRAM1)")
        driver.generate_read_only_traffic(read_addresses_sram1)
        print("\nRunning DMA Test #1 (SRAM0 → SRAM1)")
        test_dma_memcpy(driver)
        print("\nReading SRAM1 data after running DMA Test #1 (SRAM0 → SRAM1)")
        driver.generate_read_only_traffic(read_addresses_sram1)

        print(f"\nSBA traffic generation complete!")

        # Auto-detect start time (no user input)
        sba_start_time = find_sba_start_time()
        analyze_sniffer_results(sba_start_time)

        print("\n✅ SBA verification sequence completed.")

    except Exception as e:
        print(f"❌ Main error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        if driver:
            driver.close()


if __name__ == "__main__":
    main()

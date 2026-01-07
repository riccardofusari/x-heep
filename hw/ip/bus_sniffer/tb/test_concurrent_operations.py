#!/usr/bin/env python3
"""
Bus Sniffer Validation Test - Concurrent CPU + DMA Operations

This test validates the bus sniffer's ability to capture concurrent transactions
from multiple bus masters. It uses the X-HEEP testbench infrastructure to:

1. Load and execute firmware that generates known traffic patterns
2. Parse the sniffer CSV output
3. Verify captured transactions against expected patterns using scoreboard
4. Report pass/fail status with detailed diagnostics

Test Architecture:
    - Firmware: main.c generates CPU + DMA concurrent traffic
    - Driver: XHeepSBADriver controls simulation via OpenOCD
    - Monitor: BusMonitor parses sniffer CSV output
    - Scoreboard: BusScoreboard verifies expected vs actual transactions

Expected Sniffer Observations:
    - CORE_DATA: CPU buffer initialization, computation, verification
    - DMA_READ: 128 reads (64 from Buffer A + 64 from Buffer D)
    - DMA_WRITE: 128 writes (64 to Buffer C + 64 to Buffer E)
    - AO_PERIPH: Timer register accesses, UART writes
    - RAM0/RAM1: Slave-side view of memory transactions

Author: Bus Sniffer Validation Team
Date: 2025
"""

from driver import XHeepSBADriver, OperationMode
from monitor import BusMonitor, CSV_PATH, TransactionType
from scoreboard import BusScoreboard
import time
import os

# ==============================================================================
# CONFIGURATION - Must match main.c
# ==============================================================================

# Firmware result location and status codes
RESULT_ADDR         = 0x00008100
VAL_RUNNING         = 0x11111111
VAL_PASS            = 0xAAAAAAAA
VAL_FAIL_DMA1       = 0xDEAD0001
VAL_FAIL_DMA2       = 0xDEAD0002
VAL_FAIL_VERIFY1    = 0xDEAD0011
VAL_FAIL_VERIFY2    = 0xDEAD0012

# Memory map - RAM0 (0x00000000 - 0x00007FFF)
RAM0_BUFFER_A       = 0x00002000    # DMA #1 source (64 bytes)
RAM0_BUFFER_B       = 0x00002100    # CPU computation source (64 bytes)
RAM0_WORKSPACE      = 0x00002200    # CPU workspace (256 bytes)
RAM0_BUFFER_D       = 0x00002300    # DMA #2 source (64 bytes)

# Memory map - RAM1 (0x00008000 - 0x0000FFFF)
RAM1_BUFFER_C       = 0x00008000    # DMA #1 destination (64 bytes)
RAM1_BUFFER_E       = 0x00008200    # DMA #2 destination (64 bytes)
RAM1_TIMER_COUNT    = 0x00008300    # Timer event counter (4 bytes)

# Buffer size
BUFFER_SIZE         = 64

# Test patterns (must match main.c)
PATTERN_A_BASE      = 0xA0
PATTERN_B_BASE      = 0xB0
PATTERN_D_BASE      = 0xD0

# Peripheral address ranges
UART_BASE           = 0x200A0000
UART_END            = 0x200A0FFF
UART_WDATA          = UART_BASE + 0x18
TIMER_BASE          = 0x20050000
TIMER_END           = 0x2005FFFF


# ==============================================================================
# PATTERN GENERATION FUNCTIONS
# ==============================================================================

def expected_pattern_a(i):
    """Generate expected pattern A value for index i."""
    return PATTERN_A_BASE | (i & 0x0F)

def expected_pattern_b(i):
    """Generate expected pattern B value for index i."""
    return PATTERN_B_BASE | (i & 0x0F)

def expected_pattern_d(i):
    """Generate expected pattern D value for index i."""
    return PATTERN_D_BASE | (i & 0x0F)


# ==============================================================================
# FIRMWARE INTERACTION
# ==============================================================================

def wait_for_firmware_result(driver, timeout_s=30.0):
    """
    Wait for firmware to complete and return result status.
    
    Args:
        driver: XHeepSBADriver instance
        timeout_s: Maximum wait time in seconds
        
    Returns:
        True if PASS, False if FAIL, None if timeout
    """
    start = time.time()
    print(f"⏳ Waiting for firmware (timeout {timeout_s}s)...")

    while True:
        try:
            val = driver.read_word(RESULT_ADDR)
            
            if val == VAL_PASS:
                print("✅ Firmware PASS")
                return True
            elif val == VAL_FAIL_DMA1:
                print("❌ Firmware FAIL: DMA #1 launch error")
                return False
            elif val == VAL_FAIL_DMA2:
                print("❌ Firmware FAIL: DMA #2 launch error")
                return False
            elif val == VAL_FAIL_VERIFY1:
                print("❌ Firmware FAIL: DMA #1 data verification error")
                return False
            elif val == VAL_FAIL_VERIFY2:
                print("❌ Firmware FAIL: DMA #2 data verification error")
                return False
                
            if time.time() - start > timeout_s:
                print(f"⚠️  Timeout waiting for firmware (last value: 0x{val:08X})")
                return None
                
            time.sleep(0.5)
            
        except Exception as e:
            print(f"⚠️  Error reading result: {e}")
            return None


# ==============================================================================
# SNIFFER ANALYSIS FUNCTIONS
# ==============================================================================

def analyze_phase0_init(monitor, scoreboard):
    """
    Analyze Phase 0: Buffer initialization by CPU.
    
    Expected: CPU writes patterns to buffers A, B, D and clears C, E, workspace.
    """
    print("\n" + "=" * 70)
    print("PHASE 0: Buffer Initialization")
    print("=" * 70)

    core_data = monitor.filter_by_channel(["CORE_DATA"])
    writes = [t for t in core_data if t.transaction_type == TransactionType.WRITE]

    # Count writes to each buffer
    buf_a_writes = [t for t in writes if RAM0_BUFFER_A <= t.address < RAM0_BUFFER_A + BUFFER_SIZE]
    buf_b_writes = [t for t in writes if RAM0_BUFFER_B <= t.address < RAM0_BUFFER_B + BUFFER_SIZE]
    buf_c_writes = [t for t in writes if RAM1_BUFFER_C <= t.address < RAM1_BUFFER_C + BUFFER_SIZE]
    buf_d_writes = [t for t in writes if RAM0_BUFFER_D <= t.address < RAM0_BUFFER_D + BUFFER_SIZE]
    buf_e_writes = [t for t in writes if RAM1_BUFFER_E <= t.address < RAM1_BUFFER_E + BUFFER_SIZE]

    print(f"  Buffer A writes @ 0x{RAM0_BUFFER_A:08X}: {len(buf_a_writes)}")
    print(f"  Buffer B writes @ 0x{RAM0_BUFFER_B:08X}: {len(buf_b_writes)}")
    print(f"  Buffer C writes @ 0x{RAM1_BUFFER_C:08X}: {len(buf_c_writes)}")
    print(f"  Buffer D writes @ 0x{RAM0_BUFFER_D:08X}: {len(buf_d_writes)}")
    print(f"  Buffer E writes @ 0x{RAM1_BUFFER_E:08X}: {len(buf_e_writes)}")

    # Add expected writes to scoreboard
    for i in range(BUFFER_SIZE):
        scoreboard.add_expected_write(RAM0_BUFFER_A + i, expected_pattern_a(i), 
                                      f"Buffer A init [{i}]")
        scoreboard.add_expected_write(RAM0_BUFFER_B + i, expected_pattern_b(i),
                                      f"Buffer B init [{i}]")
        scoreboard.add_expected_write(RAM0_BUFFER_D + i, expected_pattern_d(i),
                                      f"Buffer D init [{i}]")

    passed = (len(buf_a_writes) == BUFFER_SIZE and 
              len(buf_b_writes) == BUFFER_SIZE and
              len(buf_d_writes) == BUFFER_SIZE)
    
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  Status: {status}")

    return {
        'buf_a_writes': len(buf_a_writes),
        'buf_b_writes': len(buf_b_writes),
        'buf_c_writes': len(buf_c_writes),
        'buf_d_writes': len(buf_d_writes),
        'buf_e_writes': len(buf_e_writes),
        'passed': passed
    }


def analyze_dma_transfer(monitor, scoreboard, transfer_num, src_addr, dst_addr, pattern_func):
    """
    Analyze a DMA transfer.
    
    Args:
        monitor: BusMonitor instance
        scoreboard: BusScoreboard instance
        transfer_num: 1 or 2
        src_addr: Source buffer address
        dst_addr: Destination buffer address
        pattern_func: Function to generate expected pattern
    """
    print("\n" + "=" * 70)
    print(f"PHASE {transfer_num}: DMA Transfer #{transfer_num}")
    print(f"  Source: 0x{src_addr:08X} -> Destination: 0x{dst_addr:08X}")
    print("=" * 70)

    # Analyze DMA reads from source
    dma_reads = monitor.filter_by_channel(["DMA_READ"])
    src_reads = [t for t in dma_reads if src_addr <= t.address < src_addr + BUFFER_SIZE]
    print(f"  DMA reads from source: {len(src_reads)}")

    # Analyze DMA writes to destination
    dma_writes = monitor.filter_by_channel(["DMA_WRITE"])
    dst_writes = [t for t in dma_writes 
                  if t.transaction_type == TransactionType.WRITE
                  and dst_addr <= t.address < dst_addr + BUFFER_SIZE]
    print(f"  DMA writes to destination: {len(dst_writes)}")

    # Verify data pattern in writes
    pattern_errors = 0
    for t in dst_writes:
        offset = t.address - dst_addr
        expected = pattern_func(offset)
        actual = t.data & 0xFF
        if actual != expected:
            pattern_errors += 1
            
        # Add to scoreboard
        scoreboard.add_expected_write(t.address, expected, 
                                      f"DMA #{transfer_num} write [{offset}]")

    print(f"  Pattern verification errors: {pattern_errors}")

    passed = (len(src_reads) == BUFFER_SIZE and 
              len(dst_writes) == BUFFER_SIZE and 
              pattern_errors == 0)
    
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  Status: {status}")

    return {
        'dma_reads': len(src_reads),
        'dma_writes': len(dst_writes),
        'pattern_errors': pattern_errors,
        'passed': passed
    }


def analyze_cpu_computation(monitor):
    """
    Analyze CPU computation phases (concurrent with DMA).
    """
    print("\n" + "=" * 70)
    print("CPU COMPUTATION (Concurrent with DMA)")
    print("=" * 70)

    core_data = monitor.filter_by_channel(["CORE_DATA"])

    # CPU reads from Buffer B
    reads = [t for t in core_data if t.transaction_type == TransactionType.READ]
    buf_b_reads = [t for t in reads if RAM0_BUFFER_B <= t.address < RAM0_BUFFER_B + BUFFER_SIZE]
    print(f"  CPU reads from Buffer B: {len(buf_b_reads)}")

    # CPU writes to workspace
    writes = [t for t in core_data if t.transaction_type == TransactionType.WRITE]
    workspace_writes = [t for t in writes if RAM0_WORKSPACE <= t.address < RAM0_WORKSPACE + 256]
    print(f"  CPU writes to workspace: {len(workspace_writes)}")

    passed = len(buf_b_reads) > 0
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  Status: {status}")

    return {
        'buf_b_reads': len(buf_b_reads),
        'workspace_writes': len(workspace_writes),
        'passed': passed
    }


def analyze_concurrency(monitor):
    """
    Verify that CPU and DMA transactions were interleaved (concurrent).
    """
    print("\n" + "=" * 70)
    print("CONCURRENCY ANALYSIS")
    print("=" * 70)

    # Get DMA read timestamps
    dma_reads = monitor.filter_by_channel(["DMA_READ"])
    dma_buf_a = [t for t in dma_reads if RAM0_BUFFER_A <= t.address < RAM0_BUFFER_A + BUFFER_SIZE]

    # Get CPU read timestamps
    core_data = monitor.filter_by_channel(["CORE_DATA"])
    cpu_reads = [t for t in core_data 
                 if t.transaction_type == TransactionType.READ
                 and RAM0_BUFFER_B <= t.address < RAM0_BUFFER_B + BUFFER_SIZE]

    if not dma_buf_a or not cpu_reads:
        print("  ⚠️  Cannot analyze concurrency - missing transactions")
        return {'interleaved': 0, 'passed': False}

    # Calculate timestamp windows
    dma_timestamps = [t.req_ts for t in dma_buf_a]
    cpu_timestamps = [t.req_ts for t in cpu_reads]

    dma_start, dma_end = min(dma_timestamps), max(dma_timestamps)
    cpu_start, cpu_end = min(cpu_timestamps), max(cpu_timestamps)

    print(f"  DMA #1 window: {dma_start} - {dma_end} cycles")
    print(f"  CPU window:    {cpu_start} - {cpu_end} cycles")

    # Check for overlap
    overlap_start = max(dma_start, cpu_start)
    overlap_end = min(dma_end, cpu_end)

    if overlap_start < overlap_end:
        interleaved = sum(1 for t in cpu_reads if dma_start <= t.req_ts <= dma_end)
        print(f"  ✅ Overlap detected: {interleaved} CPU transactions during DMA")
        return {'interleaved': interleaved, 'passed': True}
    else:
        print("  ⚠️  No overlap detected - sequential execution")
        return {'interleaved': 0, 'passed': False}


def analyze_timer_uart_traffic(monitor):
    """
    Analyze timer and UART peripheral traffic.
    """
    print("\n" + "=" * 70)
    print("TIMER/UART PERIPHERAL TRAFFIC")
    print("=" * 70)

    periph_tx = monitor.filter_by_channel(["AO_PERIPH", "PERIPH"])

    # UART writes
    uart_writes = [t for t in periph_tx
                   if t.transaction_type == TransactionType.WRITE 
                   and UART_BASE <= t.address <= UART_END]
    uart_wdata = [t for t in uart_writes if t.address == UART_WDATA]
    print(f"  UART writes: {len(uart_writes)}")
    print(f"  UART WDATA writes @ 0x{UART_WDATA:08X}: {len(uart_wdata)}")

    # Timer register accesses
    timer_tx = [t for t in periph_tx if TIMER_BASE <= t.address <= TIMER_END]
    print(f"  Timer register accesses: {len(timer_tx)}")

    # RAM1 counter writes
    core_data = monitor.filter_by_channel(["CORE_DATA"])
    counter_writes = [t for t in core_data
                      if t.transaction_type == TransactionType.WRITE 
                      and t.address == RAM1_TIMER_COUNT]
    print(f"  Timer counter writes @ 0x{RAM1_TIMER_COUNT:08X}: {len(counter_writes)}")

    passed = len(timer_tx) > 0 and len(counter_writes) > 0
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  Status: {status}")

    return passed


def analyze_ram_channels(monitor):
    """
    Analyze RAM slave channel observations.
    """
    print("\n" + "=" * 70)
    print("RAM SLAVE CHANNEL ANALYSIS")
    print("=" * 70)

    ram0 = monitor.filter_by_channel(["RAM0"])
    ram0_reads = [t for t in ram0 if t.transaction_type == TransactionType.READ]
    ram0_writes = [t for t in ram0 if t.transaction_type == TransactionType.WRITE]
    print(f"  RAM0: {len(ram0_reads)} reads, {len(ram0_writes)} writes")

    ram1 = monitor.filter_by_channel(["RAM1"])
    ram1_reads = [t for t in ram1 if t.transaction_type == TransactionType.READ]
    ram1_writes = [t for t in ram1 if t.transaction_type == TransactionType.WRITE]
    print(f"  RAM1: {len(ram1_reads)} reads, {len(ram1_writes)} writes")

    return {
        'ram0_reads': len(ram0_reads),
        'ram0_writes': len(ram0_writes),
        'ram1_reads': len(ram1_reads),
        'ram1_writes': len(ram1_writes)
    }


# ==============================================================================
# MAIN ANALYSIS FUNCTION
# ==============================================================================

def run_analysis():
    """
    Run complete sniffer output analysis.
    
    Returns:
        True if all checks pass, False otherwise
    """
    print("\n" + "=" * 80)
    print("BUS SNIFFER ANALYSIS - CONCURRENT CPU + DMA TEST")
    print("=" * 80)

    # Initialize monitor and scoreboard
    monitor = BusMonitor()
    scoreboard = BusScoreboard()
    
    csv_path = os.path.abspath(CSV_PATH)

    if not os.path.exists(csv_path):
        print(f"❌ CSV file not found: {csv_path}")
        return False

    print(f"\n📂 Analyzing: {csv_path}")
    
    # Parse CSV
    all_transactions = monitor.parse_csv(csv_path)
    if not all_transactions:
        print("❌ No transactions found in CSV")
        return False

    # Print statistics
    monitor.print_statistics()

    # Add observed transactions to scoreboard
    scoreboard.add_observed_transactions(all_transactions)

    # Run analysis phases
    results = {}
    
    results['phase0'] = analyze_phase0_init(monitor, scoreboard)
    
    results['dma1'] = analyze_dma_transfer(
        monitor, scoreboard, 1,
        RAM0_BUFFER_A, RAM1_BUFFER_C, expected_pattern_a
    )
    
    results['dma2'] = analyze_dma_transfer(
        monitor, scoreboard, 2,
        RAM0_BUFFER_D, RAM1_BUFFER_E, expected_pattern_d
    )
    
    results['cpu'] = analyze_cpu_computation(monitor)
    results['concurrency'] = analyze_concurrency(monitor)
    results['timer'] = analyze_timer_uart_traffic(monitor)
    results['ram'] = analyze_ram_channels(monitor)

    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    checks = [
        ("Phase 0 - Buffer initialization", results['phase0']['passed']),
        ("DMA #1 transfer (A -> C)", results['dma1']['passed']),
        ("DMA #2 transfer (D -> E)", results['dma2']['passed']),
        ("CPU computation", results['cpu']['passed']),
        ("Concurrency verification", results['concurrency']['passed']),
        ("Timer/UART traffic", results['timer']),
    ]

    all_passed = True
    for name, passed in checks:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print("\n" + "=" * 80)
    if all_passed:
        print("🎉 ALL CHECKS PASSED")
    else:
        print("⚠️  SOME CHECKS FAILED")
    print("=" * 80)

    return all_passed


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    """
    Main test entry point.
    
    1. Connect to simulation via OpenOCD
    2. Enable DPI sniffer
    3. Resume CPU to execute firmware
    4. Wait for firmware completion
    5. Analyze sniffer CSV output
    6. Report results
    """
    print("\n" + "=" * 80)
    print("X-HEEP BUS SNIFFER VALIDATION TEST")
    print("Concurrent CPU + DMA Operations")
    print("=" * 80)

    driver = None
    firmware_ok = False

    try:
        # Connect to simulation
        print("\n🔗 Connecting to OpenOCD...")
        driver = XHeepSBADriver(mode=OperationMode.SBA)
        driver.test_connection()
        time.sleep(0.5)

        # Enable sniffer
        print("\n📡 Enabling DPI sniffer...")
        driver.enable_dpi()
        time.sleep(0.2)

        # Resume CPU
        print("\n▶️  Resuming CPU to execute firmware...")
        driver._resume_cpu()
        time.sleep(0.2)

        # Wait for firmware completion
        firmware_ok = wait_for_firmware_result(driver, timeout_s=30.0)

        time.sleep(1.0)

    except Exception as e:
        print(f"\n❌ Error during test execution: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        if driver:
            try:
                driver.close()
            except:
                pass

    # Analyze sniffer output
    print("\n" + "=" * 80)
    analysis_ok = run_analysis()

    # Final result
    print("\n" + "=" * 80)
    print("FINAL RESULT")
    print("=" * 80)
    
    if firmware_ok and analysis_ok:
        print("✅ TEST PASSED - All firmware and sniffer checks successful")
        return 0
    elif firmware_ok:
        print("⚠️  PARTIAL - Firmware passed but sniffer analysis has issues")
        return 1
    else:
        print("❌ TEST FAILED")
        return 1


if __name__ == "__main__":
    exit(main())
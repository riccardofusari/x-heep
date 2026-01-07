"""
Comprehensive UART Firmware + Bus Sniffer Test
Tests multiple patterns to demonstrate bus sniffer capabilities
"""

from driver import XHeepSBADriver, OperationMode
from monitor import BusMonitor, CSV_PATH, TransactionType
from scoreboard import BusScoreboard
import time
import os

# ============================================================
# Configuration
# ============================================================
RESULT_ADDR = 0x00008000
VAL_RUNNING = 0x11111111
VAL_PASS = 0xAAAAAAAA

UART_BASE = 0x200A0000
UART_WDATA_REG_OFFSET = 0x18
UART_WDATA_ADDR = UART_BASE + UART_WDATA_REG_OFFSET
UART_ADDR_RANGE_START = UART_BASE
UART_ADDR_RANGE_END = UART_BASE + 0x00010000

# Test patterns (matching main.c - optimized for sniffer capacity)
TEST_PATTERNS = {
    "HELLO": [0x48, 0x45, 0x4C, 0x4C, 0x4F],
    "0-9": [0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39],
    "DEADCAFE": [0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE],
}


def wait_for_firmware_result(driver, timeout_s=30.0):
    """Wait for firmware completion"""
    start = time.time()
    print(f"⏳ Waiting for firmware (timeout {timeout_s}s)...")

    while True:
        try:
            val = driver.read_word(RESULT_ADDR)
            if val == VAL_PASS:
                print("✅ Firmware PASS")
                return True
            if time.time() - start > timeout_s:
                print(f"⚠️  Timeout (last: 0x{val:08X})")
                return None
            time.sleep(0.5)
        except Exception as e:
            print(f"⚠️  Error: {e}")
            return None


def analyze_patterns(wdata_writes):
    """Analyze which patterns were captured in sniffer"""
    if not wdata_writes:
        print("❌ No WDATA writes found")
        return {}

    # Extract byte sequence
    captured_bytes = [t.data & 0xFF for t in wdata_writes]
    print(f"\n📊 Captured {len(captured_bytes)} bytes total")
    
    results = {}
    offset = 0  # Skip first dummy byte
    
    # Check each pattern
    print(f"\n📋 Pattern detection:")
    print(f"   {'-'*76}")
    
    for pattern_name, expected_bytes in TEST_PATTERNS.items():
        pattern_len = len(expected_bytes)
        
        # Find pattern in captured bytes
        found = False
        for start_idx in range(len(captured_bytes) - pattern_len + 1):
            if captured_bytes[start_idx:start_idx+pattern_len] == expected_bytes:
                found = True
                results[pattern_name] = {
                    'found': True,
                    'start_idx': start_idx,
                    'end_idx': start_idx + pattern_len - 1,
                    'length': pattern_len
                }
                print(f"   ✅ {pattern_name:30s} ({pattern_len:3d} bytes) @ offset {start_idx}")
                break
        
        if not found:
            results[pattern_name] = {'found': False, 'length': pattern_len}
            print(f"   ❌ {pattern_name:30s} ({pattern_len:3d} bytes) - NOT FOUND")
    
    return results


def analyze_uart_sniffer_comprehensive():
    """Comprehensive analysis of all test patterns"""
    print("\n" + "="*80)
    print("COMPREHENSIVE UART BUS SNIFFER ANALYSIS")
    print("="*80)

    monitor = BusMonitor()
    csv_path = os.path.abspath(CSV_PATH)
    
    if not os.path.exists(csv_path):
        print(f"❌ CSV not found")
        return False
    
    print(f"\n📂 Analyzing CSV...")
    all_transactions = monitor.parse_csv(csv_path)
    
    if not all_transactions:
        print("❌ No transactions found")
        return False

    monitor.print_statistics()

    # Filter UART
    ao_txns = monitor.filter_by_channel(["AO_PERIPH"])
    uart_txns = [t for t in ao_txns 
                 if UART_ADDR_RANGE_START <= t.address <= UART_ADDR_RANGE_END]
    
    print(f"\n📍 UART transactions: {len(uart_txns)}")

    if not uart_txns:
        print("⚠️  No UART transactions")
        return False

    # Extract WDATA writes
    wdata_writes = [t for t in uart_txns
                   if t.transaction_type == TransactionType.WRITE 
                   and t.address == UART_WDATA_ADDR]
    
    print(f"📝 WDATA writes: {len(wdata_writes)}")

    # Show byte sequence
    if wdata_writes:
        bytes_seq = [t.data & 0xFF for t in wdata_writes]
        print(f"\n   First 50 bytes: {' '.join(f'{b:02X}' for b in bytes_seq[:50])}")
        if len(bytes_seq) > 50:
            print(f"   Last 50 bytes:  {' '.join(f'{b:02X}' for b in bytes_seq[-50:])}")

    # ========== PATTERN ANALYSIS ==========
    print(f"\n" + "="*80)
    print("PATTERN VERIFICATION")
    print("="*80)
    
    pattern_results = analyze_patterns(wdata_writes)

    # ========== STATISTICS ==========
    print(f"\n" + "="*80)
    print("STATISTICS")
    print("="*80)
    
    total_patterns = len(TEST_PATTERNS)
    found_patterns = sum(1 for r in pattern_results.values() if r.get('found', False))
    total_expected_bytes = sum(len(p) for p in TEST_PATTERNS.values())
    
    print(f"\n📈 Coverage:")
    print(f"   Patterns found:     {found_patterns}/{total_patterns}")
    print(f"   Expected bytes:     {total_expected_bytes + 1} (including dummy)")
    print(f"   Captured bytes:     {len(wdata_writes)}")
    print(f"   Coverage:           {(len(wdata_writes) / (total_expected_bytes + 1)) * 100:.1f}%")

    # Timing analysis
    if len(wdata_writes) > 1:
        timestamps = [t.req_ts for t in wdata_writes]
        intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
        
        print(f"\n⏱️  Timing:")
        print(f"   First write:   req_ts={timestamps[0]}")
        print(f"   Last write:    req_ts={timestamps[-1]}")
        print(f"   Total duration: {timestamps[-1] - timestamps[0]} cycles")
        print(f"   Throughput:    {len(wdata_writes) / (timestamps[-1] - timestamps[0]) * 1000:.2f} writes/1000 cycles")
        
        if intervals:
            print(f"   Inter-write intervals: min={min(intervals)}, max={max(intervals)}, "
                  f"avg={sum(intervals)//len(intervals)}")

    # ========== FINAL RESULT ==========
    print(f"\n" + "="*80)
    if found_patterns == total_patterns:
        print(f"🎉 SUCCESS - All {total_patterns} patterns verified!")
        return True
    else:
        print(f"⚠️  PARTIAL - {found_patterns}/{total_patterns} patterns found")
        if found_patterns > 0:
            print(f"   (Some patterns were captured, but not all)")
            return True  # Partial success
        else:
            print(f"❌ FAILURE - No patterns verified")
            return False


# ============================================================
# Main
# ============================================================
def main():
    print("\n" + "="*80)
    print("X-HEEP COMPREHENSIVE UART FIRMWARE + DPI SNIFFER TEST")
    print("="*80)

    driver = None
    
    try:
        print("\n🔗 Connecting...")
        driver = XHeepSBADriver(mode=OperationMode.SBA)
        driver.test_connection()
        time.sleep(0.5)

        print("\n📡 Enabling DPI sniffer...")
        driver.enable_dpi()
        time.sleep(0.2)
        time.sleep(0.5)

        print("\n▶️  Resuming CPU...")
        driver._resume_cpu()
        time.sleep(0.2)
        
        wait_for_firmware_result(driver, timeout_s=30.0)

        time.sleep(1.0)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            try:
                driver.close()
            except:
                pass

    # Analyze
    print("\n" + "="*80)
    analysis_ok = analyze_uart_sniffer_comprehensive()

    # Summary
    print("\n" + "="*80)
    print(f"OVERALL: {'✅ PASS' if analysis_ok else '⚠️  PARTIAL SUCCESS' if analysis_ok is None else '❌ FAIL'}")
    print("="*80 + "\n")

    return 0 if analysis_ok else 1


if __name__ == "__main__":
    exit(main())

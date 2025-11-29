# Save this as test_basic_traffic.py
from driver import XHeepSBADriver, OperationMode
from monitor import BusMonitor, BusTransaction, TransactionType
from scoreboard import BusScoreboard
import time
import os

def get_user_mode():
    """Ask user for operation mode"""
    print("🎛️  Select Operation Mode:")
    print("1. SBA Mode (Current implementation - uses Telnet port 4444)")
    print("2. Legacy Mode (GDB control - uses GDB port 3333)")
    
    while True:
        choice = input("Enter choice (1 or 2): ").strip()
        if choice == '1':
            return OperationMode.SBA
        elif choice == '2':
            return OperationMode.LEGACY
        else:
            print("Invalid choice. Please enter 1 or 2.")

def analyze_sniffer_results(sba_start_time: int = None):
    """Analyze bus sniffer output after test completion with timing support"""
    print("\n" + "="*60)
    print("📊 ANALYZING BUS SNIFFER RESULTS")
    if sba_start_time:
        print(f"⏰ Focusing on transactions after req_ts={sba_start_time}")
    print("="*60)
    
    csv_path = os.path.expanduser("~/soc_task/x-heep-apply/build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator/sniffer_frames.csv")
    
    monitor = BusMonitor()
    all_transactions = monitor.parse_csv(csv_path)
    
    if not all_transactions:
        print("❌ No transactions found in sniffer output")
        return False
    
    # Print overall statistics
    monitor.print_statistics()
    monitor.print_timing_statistics()
    
    # Filter transactions by time if specified
    if sba_start_time:
        transactions = monitor.filter_by_time_range(sba_start_time)
        print(f"\n🔍 Showing transactions after SBA operations started (req_ts >= {sba_start_time})")
        monitor.print_transactions(limit=10, min_req_ts=sba_start_time)
    else:
        transactions = all_transactions
        # Try to auto-detect SBA start time (look for first PERIPH write)
        periph_writes = [t for t in all_transactions if t.channel == "PERIPH" and t.transaction_type == TransactionType.WRITE]
        if periph_writes:
            sba_start_time = min(t.req_ts for t in periph_writes)
            print(f"🤖 Auto-detected SBA start around req_ts={sba_start_time}")
            transactions = monitor.filter_by_time_range(sba_start_time)
    
    # Create and populate scoreboard
    scoreboard = BusScoreboard()
    
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
    
    for address, data, description in expected_writes:
        scoreboard.add_expected_write(address, data, description=description)
    
    scoreboard.add_observed_transactions(transactions)
    scoreboard.print_results()
    
    return True

def find_sba_start_time() -> int:
    """
    Try to find when SBA operations started by looking for patterns
    Returns a suggested req_ts value to use as filter
    """
    csv_path = os.path.expanduser("~/soc_task/x-heep-apply/build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator/sniffer_frames.csv")
    
    monitor = BusMonitor()
    transactions = monitor.parse_csv(csv_path)
    
    if not transactions:
        return 0
    
    # Look for PERIPH writes (typical for SBA)
    periph_writes = [t for t in transactions if t.channel == "PERIPH" and t.transaction_type == TransactionType.WRITE]
    
    if periph_writes:
        first_periph_write = min(periph_writes, key=lambda t: t.req_ts)
        print(f"🔍 First PERIPH write at req_ts={first_periph_write.req_ts}")
        return first_periph_write.req_ts
    
    # Fallback: use the overall max timestamp minus a buffer
    max_ts = max(t.req_ts for t in transactions)
    suggested_start = max(0, max_ts - 1000)  # Go back 1000 cycles
    print(f"🔍 Suggested start time: req_ts={suggested_start}")
    return suggested_start

def main():
    driver = None
    try:
        # Get mode from user
        mode = get_user_mode()
        
        # Initialize driver with selected mode
        print(f"🚀 Starting {'SBA' if mode == OperationMode.SBA else 'Legacy'} Traffic Generator...")
        
        if mode == OperationMode.SBA:
            print("Connecting to OpenOCD Telnet on localhost:4444...")
            driver = XHeepSBADriver(mode=mode)
        else:
            print("Connecting to OpenOCD GDB server on localhost:3333...")
            driver = XHeepSBADriver(mode=mode)
        
        # Test connection first
        print("\n🔍 Testing connection...")
        if not driver.test_connection():
            print("❌ Cannot proceed - connection failed")
            return
        
        time.sleep(1)
        
        if mode == OperationMode.LEGACY:
            # Run the legacy command sequence
            print("\n" + "="*50)
            print("Running Legacy Mode Command Sequence...")
            print("="*50)
            driver.run_fifo_test_via_gdb()

            # driver.run_legacy_sequence()
            
        else:  # SBA Mode
            print("\n" + "="*50)
            print("Generating READ-ONLY traffic for sniffer monitoring...")
            print("="*50)
            
            # Generate diverse read traffic that should be visible in sniffer
            read_addresses = [
                # Instruction memory region (from your successful reads)
                0x20010000, 0x20010004, 0x20010008, 0x2001000C, 0x20010010,
                #DMA
                0x20030000, 0x20030004,0x20030008,0x2003000C,
                # Different memory regions
                0x00000000, 0x00000004, 0x00000008,
                0x20000000, 0x20000004, 0x20000008,0x20000010,
                0x00000ED4,0x00000ED8, 0x00000EDC, 0x00000EE0,
            ]
            read_addresses_1 = [
                0x00000000, 0x00000004, 0x00000008,
                0x2000000c, 0x20000010, 0x20000014,0x20000018,
                #DMA
                0x20030000, 0x20030004,0x20030008,0x2003000C,
                0x00000ED4,0x00000ED8, 0x00000EDC, 0x00000EE0,
            ]
            burst_data = [0xA5A5A5A5, 0x5A5A5A5A, 0xF0F0F0F0, 0x0F0F0F0F]
            
            driver.generate_read_only_traffic(read_addresses)
            driver.write_burst(0x00000000, burst_data)      
            driver.write_burst(0x2002FFF8, burst_data)
            driver.write_burst(0x00000ED4, burst_data)
            driver.generate_read_only_traffic(read_addresses_1)
            
            print(f"\n📊 SBA traffic generation complete!")
            print(f"🎯 Check your sniffer output - you should see these read transactions!")
            
            # Ask user for timing filter
            print("\n⏰ Timing Analysis Options:")
            print("1. Analyze all transactions")
            print("2. Analyze transactions after specific req_ts")
            print("3. Auto-detect SBA start time")
            
            choice = input("Select option (1-3): ").strip()
            
            sba_start_time = None
            if choice == "2":
                try:
                    sba_start_time = int(input("Enter req_ts to start from: ").strip())
                except ValueError:
                    print("❌ Invalid input, using auto-detection")
                    sba_start_time = find_sba_start_time()
            elif choice == "3":
                sba_start_time = find_sba_start_time()
            
            # Analyze sniffer results with timing filter
            analyze_sniffer_results(sba_start_time)
        
    except Exception as e:
        print(f"❌ Main error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            driver.close()

if __name__ == "__main__":
    main()
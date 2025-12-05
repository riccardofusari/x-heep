# Save this as scoreboard.py
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from monitor import BusTransaction, TransactionType, BusSource
import time

class ScoreboardResult(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"

@dataclass
class ExpectedTransaction:
    address: int
    expected_data: Optional[int] = None
    transaction_type: TransactionType = TransactionType.WRITE
    channel: Optional[str] = None
    description: str = ""
    tolerance_cycles: int = 100  # Allow transactions to be out of order within this window
    timestamp: Optional[int] = 0

@dataclass
class ComparisonResult:
    expected: ExpectedTransaction
    actual: Optional[BusTransaction] = None
    result: ScoreboardResult = ScoreboardResult.PENDING
    error_message: str = ""
    cycle_delta: int = 0

    def __str__(self):
        status_icon = "✅" if self.result == ScoreboardResult.PASS else "❌" if self.result == ScoreboardResult.FAIL else "⏳"
        base_str = f"{status_icon} {self.expected.description}"

        if self.actual:
            base_str += f" | Actual: {self.actual.channel} 0x{self.actual.address:08x}=0x{self.actual.data:08x}"

        if self.error_message:
            base_str += f" | Error: {self.error_message}"

        return base_str

class BusScoreboard:
    def __init__(self):
        self.expected_transactions: List[ExpectedTransaction] = []
        self.comparison_results: List[ComparisonResult] = []
        self.observed_transactions: List[BusTransaction] = []
        
    def compare_shadow_vs_ram_mirror(self, expected_pairs, actual_pairs):
        print("\n📘 Actual RAM1 writes:")

        # Reorganize actual_pairs by DATA only
        actual_by_data = {}
        for (addr, data), entries in actual_pairs.items():
            if data not in actual_by_data:
                actual_by_data[data] = []
            # keep original entries (ts, src) + addr for debug
            for (req_ts, src_name) in entries:
                actual_by_data[data].append((addr, req_ts, src_name))

        # Print actual RAM1 writes
        # for data, entries in actual_by_data.items():
        #     for (addr, req_ts, src_name) in entries:
        #         print(f"  actual: addr=0x{addr:08x}, data=0x{data:08x}, "
        #             f"src={src_name}, ts={req_ts}")

        print("\n📗 Comparison Expected vs Actual (DATA only):")

        # Now compare only on DATA
        for (exp_addr, exp_data) in expected_pairs:
            if exp_data not in actual_by_data:
                print(f"❌ Missing DATA in RAM1: data=0x{exp_data:08x}")
            else:
                for (addr, req_ts, src_name) in actual_by_data[exp_data]:
                    print(f"✅ Match: data=0x{exp_data:08x}, "
                        f"addr=0x{addr:08x}, src={src_name}, ts={req_ts}")



    def add_expected(self, expected: ExpectedTransaction):
        """Add an expected transaction to the scoreboard"""
        self.expected_transactions.append(expected)
        # Create a pending result for this expectation
        self.comparison_results.append(ComparisonResult(expected=expected))

    def add_expected_write(self, address: int, expected_data: int,
                          channel: Optional[str] = None, description: str = "",
                          tolerance_cycles: int = 100) -> ExpectedTransaction:
        """Convenience method to add an expected write transaction"""
        expected = ExpectedTransaction(
            address=address,
            expected_data=expected_data,
            transaction_type=TransactionType.WRITE,
            channel=channel,
            description=description or f"Write 0x{expected_data:08x} to 0x{address:08x}",
            tolerance_cycles=tolerance_cycles
        )
        self.add_expected(expected)
        return expected

    def add_expected_read(self, address: int, expected_data: Optional[int] = None,
                         channel: Optional[str] = None, description: str = "",
                         tolerance_cycles: int = 100) -> ExpectedTransaction:
        """Convenience method to add an expected read transaction"""
        expected = ExpectedTransaction(
            address=address,
            expected_data=expected_data,
            transaction_type=TransactionType.READ,
            channel=channel,
            description=description or f"Read from 0x{address:08x}",
            tolerance_cycles=tolerance_cycles
        )
        self.add_expected(expected)
        return expected

    def add_observed_transactions(self, transactions: List[BusTransaction]):
        """Add observed transactions from the monitor"""
        self.observed_transactions.extend(transactions)
        self._compare_transactions()

    def _compare_transactions(self):
        """Compare observed transactions with expected ones"""
        # Reset all results to pending
        for result in self.comparison_results:
            if result.result == ScoreboardResult.PENDING:
                result.actual = None
                result.error_message = ""

        # For each expected transaction, find matching observed transaction
        for expected_idx, expected in enumerate(self.expected_transactions):
            result = self.comparison_results[expected_idx]

            if result.result != ScoreboardResult.PENDING:
                continue  # Already checked

            # Find matching transaction
            matching_transactions = []
            for observed in self.observed_transactions:
                if self._transactions_match(expected, observed):
                    matching_transactions.append(observed)

            if matching_transactions:
                # Use the first matching transaction
                best_match = self._find_best_match(expected, matching_transactions)
                result.actual = best_match
                result.cycle_delta = abs(expected_idx - self.observed_transactions.index(best_match))

                # Check if data matches (for writes and expected reads)
                if expected.expected_data is not None and best_match.data != expected.expected_data:
                    result.result = ScoreboardResult.FAIL
                    result.error_message = (f"Data mismatch: expected 0x{expected.expected_data:08x}, "
                                          f"got 0x{best_match.data:08x}")
                else:
                    result.result = ScoreboardResult.PASS
            else:
                result.result = ScoreboardResult.FAIL
                result.error_message = "No matching transaction found"

    def _transactions_match(self, expected: ExpectedTransaction, observed: BusTransaction) -> bool:
        """Check if observed transaction matches expected criteria"""
        # Check address
        if expected.address != observed.address:
            return False

        # Check transaction type
        if expected.transaction_type != observed.transaction_type:
            return False

        # Check channel if specified
        # if expected.channel and expected.channel != observed.channel:
        #     return False
        # if expected.channel and expected.channel not in (observed.channel, "CORE_DATA", "PERIPH"):
        #     return False


        return True

    def _find_best_match(self, expected: ExpectedTransaction,
                        candidates: List[BusTransaction]) -> BusTransaction:
        """Find the best matching transaction from candidates"""
        # For now, return the first candidate
        # In a more sophisticated implementation, you might consider timing
        # return candidates[0]
        # Choose the observed transaction *closest in time*
        # return min(candidates, key=lambda t: t.req_ts)
        # Since req_ts is the real timestamp, match nearest in time
        return min(candidates, key=lambda t: abs(t.timestamp - expected.timestamp))

    def get_summary(self) -> Dict[str, Any]:
        """Get scoreboard summary"""
        total = len(self.comparison_results)
        passed = sum(1 for r in self.comparison_results if r.result == ScoreboardResult.PASS)
        failed = sum(1 for r in self.comparison_results if r.result == ScoreboardResult.FAIL)
        pending = total - passed - failed

        return {
            "total_expected": total,
            "passed": passed,
            "failed": failed,
            "pending": pending,
            "coverage": (passed / total * 100) if total > 0 else 0
        }

    def print_results(self):
        """Print formatted scoreboard results"""
        summary = self.get_summary()

        print("\n" + "="*70)
        print("🎯 BUS TRANSACTION SCOREBOARD")
        print("="*70)

        # Print summary
        print(f"📊 Summary: {summary['passed']} ✅ / {summary['failed']} ❌ / "
              f"{summary['pending']} ⏳ (Coverage: {summary['coverage']:.1f}%)")

        # Print detailed results
        print("\n" + "="*70)
        print("Detailed Results:")
        print("="*70)

        for i, result in enumerate(self.comparison_results, 1):
            print(f"{i:3d}. {result}")

        print("="*70)

        # Print failures at the end for easy review
        failures = [r for r in self.comparison_results if r.result == ScoreboardResult.FAIL]
        if failures:
            print("\n🚨 FAILURE DETAILS:")
            for failure in failures:
                print(f"   ❌ {failure.expected.description}")
                print(f"      Error: {failure.error_message}")

    def reset(self):
        """Reset the scoreboard"""
        self.expected_transactions.clear()
        self.comparison_results.clear()
        self.observed_transactions.clear()

    def verify_test_sequence(self, test_name: str, expected_writes: List[Tuple[int, int, str]],
                           observed_transactions: List[BusTransaction]) -> bool:
        """
        Convenience method to verify a complete test sequence
        """
        print(f"\n🧪 Verifying test: {test_name}")
        print("-" * 50)

        self.reset()

        # Add expected writes
        for address, data, description in expected_writes:
            self.add_expected_write(address, data, description=description)

        # Add observed transactions
        self.add_observed_transactions(observed_transactions)

        # Print results
        self.print_results()

        summary = self.get_summary()
        return summary['failed'] == 0 and summary['pending'] == 0

# Example usage and test functions
def create_example_scoreboard():
    """Create an example scoreboard for demonstration"""
    scoreboard = BusScoreboard()

    # Add expected transactions based on your driver.py operations
    scoreboard.add_expected_write(0x00000000, 0xA5A5A5A5,
                                 description="Write test pattern to address 0")
    scoreboard.add_expected_write(0x00000004, 0x5A5A5A5A,
                                 description="Write test pattern to address 4")
    scoreboard.add_expected_write(0x2002FFF8, 0xF0F0F0F0,
                                 description="Write to DMA region")
    scoreboard.add_expected_write(0x00000ED4, 0x0F0F0F0F,
                                 description="Write to data RAM")

    # Add expected reads
    scoreboard.add_expected_read(0x20010000, None,
                                description="Read from instruction memory")
    scoreboard.add_expected_read(0x20030000, None,
                                description="Read from DMA controller")

    return scoreboard

def analyze_sniffer_output(csv_file_path: str, bin_file_path: str = None):
    """
    Complete analysis workflow for bus sniffer output
    """
    from monitor import BusMonitor

    print("🚀 Starting Bus Sniffer Analysis")
    print("="*60)

    # Initialize monitor and parse data
    monitor = BusMonitor()

    # Try CSV first, then binary
    transactions = []
    if csv_file_path:
        transactions = monitor.parse_csv(csv_file_path)

    if not transactions and bin_file_path:
        transactions = monitor.parse_binary(bin_file_path)

    if not transactions:
        print("❌ No transactions found in either file")
        return None, None

    # Print statistics
    monitor.print_statistics()

    # Print first few transactions
    monitor.print_transactions(limit=10)

    # Create and populate scoreboard
    scoreboard = create_example_scoreboard()
    scoreboard.add_observed_transactions(transactions)

    # Print scoreboard results
    scoreboard.print_results()

    return monitor, scoreboard

if __name__ == "__main__":
    # Example usage
    import os
    csv_path = os.path.expanduser("../../../../build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator/sniffer_frames.csv")
    analyze_sniffer_output(csv_path)

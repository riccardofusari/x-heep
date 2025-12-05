# Save this as monitor.py
import csv
import struct
from enum import Enum
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json

CSV_PATH = "../../../../build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator/sniffer_frames.csv"
class BusSource(Enum):
    CORE_INSTR = 1
    CORE_DATA = 2
    AO_PERIPH = 3
    PERIPH = 4
    RAM0 = 5
    RAM1 = 6
    FLASH = 7
    DMA_READ = 8
    DMA_WRITE = 9
    DMA_ADDR = 10

class TransactionType(Enum):
    READ = "READ"
    WRITE = "WRITE"

@dataclass
class BusTransaction:
    timestamp: int
    source: BusSource
    address: int
    data: int
    transaction_type: TransactionType
    byte_enable: int
    channel: str
    req_ts: int  # Request timestamp
    resp_ts: int  # Response timestamp

    def __str__(self):
        return (f"Transaction(req_ts={self.req_ts}, resp_ts={self.resp_ts}, src={self.channel}, "
                f"addr=0x{self.address:08x}, data=0x{self.data:08x}, "
                f"type={self.transaction_type.value})")



@dataclass
class DataPair:
    address: int
    data: int
    req_ts: int

class BusMonitor:
    def __init__(self):
        self.transactions: List[BusTransaction] = []
        self.source_mapping = {
            1: ("CORE_INSTR", BusSource.CORE_INSTR),
            2: ("CORE_DATA", BusSource.CORE_DATA),
            3: ("AO_PERIPH", BusSource.AO_PERIPH),
            4: ("PERIPH", BusSource.PERIPH),
            5: ("RAM0", BusSource.RAM0),
            6: ("RAM1", BusSource.RAM1),
            7: ("FLASH", BusSource.FLASH),
            8: ("DMA_READ", BusSource.DMA_READ),
            9: ("DMA_WRITE", BusSource.DMA_WRITE)
        }

    def parse_csv(self, csv_file_path: str) -> List[BusTransaction]:
        """
        Parse CSV output from bus sniffer with proper timing support
        """
        print(f"📊 Parsing bus sniffer CSV: {csv_file_path}")
        self.transactions = []

        try:
            with open(csv_file_path, 'r') as file:
                csv_reader = csv.reader(file)

                # Read and analyze header
                headers = next(csv_reader)
                print(f"📋 CSV Header: {headers}")

                successful_parses = 0
                failed_parses = 0

                for row_num, row in enumerate(csv_reader, 2):
                    transaction = self._parse_sniffer_row(row, row_num)
                    if transaction:
                        self.transactions.append(transaction)
                        successful_parses += 1
                    else:
                        failed_parses += 1
                        if failed_parses <= 5 and row_num <= 10:
                            print(f"⚠️  Failed to parse row {row_num}: {row}")

                print(f"✅ Successfully parsed {successful_parses} transactions")
                if failed_parses > 0:
                    print(f"⚠️  Failed to parse {failed_parses} rows")

                return self.transactions

        except FileNotFoundError:
            print(f"❌ CSV file not found: {csv_file_path}")
            return []
        except Exception as e:
            print(f"❌ Error parsing CSV: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def translate_src(self, value):
        """Return friendly src label."""
        SRC_MAP = {
            1: "CORE_INSTR",
            2: "CORE_DATA",
            3: "AO_PERIPH",
            4: "PERIPH",
            5: "RAM0",
            6: "RAM1",
            7: "FLASH",
            8: "DMA_READ",
            9: "DMA_WRITE",
            10: "DMA_ADDR",
        }
        try:
            val = int(value)
            return SRC_MAP.get(val, f"UNKNOWN({val})")
        except ValueError:
            return value
            
    
    def build_expected_and_actual_pairs(self, shadow_json_path: str):
        """
        Build two lists:
        - expected_pairs: from shadow_write_data.json (RAM0 expected values)
        - actual_pairs: writes captured in CSV for channel = RAM1 (src=6)

        Each item is DataPair(address, data, req_ts)
        """

        # -------------------------------
        # 1. LOAD expected (shadow) data
        # -------------------------------
        try:
            with open("shadow_write_data.json") as f:
                shadow = json.load(f)

            BASE_ADDR = shadow["base_addr"]
            DATA      = shadow["data_words"]
        except Exception as e:
            print(f"❌ Failed to load shadow file: {e}")
            return [], []

        expected_pairs = [
            (BASE_ADDR + i * 4, DATA[i])
            for i in range(len(DATA))
        ]
        # for entry in shadow:
        #     try:
        #         addr = int(entry["address"], 16)
        #         data = int(entry["data"], 16)
        #         req_ts = entry.get("req_ts", 0)
        #         expected_pairs.append(DataPair(addr, data, req_ts))
        #     except Exception as e:
        #         print(f"⚠️ Bad shadow entry: {entry} ({e})")

        print(f"📘 Loaded {len(expected_pairs)} expected shadow write pairs")

        # -------------------------------
        # 2. BUILD actual_pairs from CSV
        # -------------------------------
        # actual_pairs = []
        # actual_pairs = {pair: [] for pair in expected_pairs}
        actual_pairs = {}
        INTERESTING_SRC_IDS = { 5,6,8,9}
        DEBUG_SRC_IDS = { 8}
        INTERESTING_ADDR = {0x00000000,0x00000004,0x00000008,0x0000000c,0x00000010,0x00000014,0x00000018,0x0000001c,0x00000020,0x00000024,0x00000028,0x0000002c,0x00000030,0x00000034,0x00000038,0x0000003c,
                            0X00008000,0X00008004,0X00008008,0X0000800c,0X00008010,0X00008014,0X00008018,0X0000801c,0X00008020,0X00008024,0X00008028,0X0000802c,0X00008030,0X00008034,0X00008038,0X0000803c}

        # Read CSV
        with open(CSV_PATH, newline='') as csvfile:
            reader = csv.DictReader(csvfile)

            for row in reader:
                # try:
                #     addr = int(row["address"], 16)
                #     data = int(row["data"], 16)
                #     we = int(row.get("we", "0"), 0)
                #     src_raw = row.get("src", "?")
                #     src_id = int(src_raw)
                #     src_name = translate_src(src_raw)
                #     req_ts = int(row.get("req_ts", "0"))
                try:
                    addr = int(row["address"], 16)
                except:
                    print("Bad address:", row["address"])
                    continue

                if addr not in INTERESTING_ADDR:
                    # print("🎛️OOps3")
                    continue
                    
                src_raw = row.get("src", "?")

                try:#src_name = translate_src(src_raw)
                    src_id = int(src_raw)
                    # if src_id in DEBUG_SRC_IDS:
                    #     print("found 8 src_id")
                except:
                    print("Bad src:", src_raw)
                    continue
                # Only consider interesting sources (5, 6, 8, 9)
                if src_id not in INTERESTING_SRC_IDS:
                    # print("🎛️OOps3")
                    continue
                try:
                    data = int(row["data"], 16)
                except:
                    print("Bad data:", row["data"])
                    continue

                try:
                    we = int(row.get("we", "0"), 0)
                except:
                    print("Bad we:", row.get("we"))
                    we = 0   # safe fallback

                try:
                    src_name = self.translate_src(src_raw)
                except:
                    print("Bad src_name:", src_name)
                    continue
                try:
                    req_ts = int(row.get("req_ts", "0"))
                except:
                    print("Bad ts:", row.get("req_ts"))
                    req_ts = 0

                # except Exception:
                #     print("🎛️OOps1")
                #     continue

                # Only consider WRITE operations
                # if we != 1 and src_id!=8:
                #     # print("🎛️OOps2")
                #     continue

                
                # Compare against expected pairs
                for exp_addr, exp_data in expected_pairs:
                    # print("🎛️OOps4")
                    if data == exp_data:
                        # print("🎛️OOps5")
            #             print(f"🔎 MATCH: addr=0x{addr:08X}, exp_data=0x{exp_data:08X}, "
            #   f"src={src_name}, req_ts={req_ts}")
                        # actual_pairs[(addr, data)].append((req_ts, src_name))
                        key = (addr, data)
                        if key not in actual_pairs:
                            actual_pairs[key] = []
                        actual_pairs[key].append((req_ts, src_name))

        print(f"📙 Extracted {len(actual_pairs)} actual RAM1/DMA_WRITE/DMA_READ write pairs")

        return expected_pairs, actual_pairs

    def _parse_sniffer_row(self, row: List[str], row_num: int) -> Optional[BusTransaction]:
        """Parse a row from the sniffer CSV with proper timing"""
        if len(row) < 9:
            return None

        try:
            # Based on your CSV format: ['src', 'req_ts', 'resp_ts', 'address', 'data', 'be', 'we', 'valid', 'gnt']
            src_value = int(row[0])
            req_ts = int(row[1])  # Request timestamp
            resp_ts = int(row[2])  # Response timestamp
            address = int(row[3], 16)  # Address in hex
            data = int(row[4], 16)     # Data in hex
            be_str = row[5]            # Byte enable (hex)
            we_str = row[6]            # Write enable
            valid = int(row[7])        # Valid flag
            gnt = int(row[8])          # Grant flag

            # Only process valid transactions
            if gnt != 1:
                return None

            # Parse byte enable (F means all bytes)
            if be_str.upper() == 'F':
                be = 0xF
            else:
                be = int(be_str, 16)

            # Parse write enable
            # Write-enable only counts if valid is high
            we = 1 if (we_str == "1" and valid == 1) else 0


            # Map source value to channel
            if src_value in self.source_mapping:
                channel_name, source_enum = self.source_mapping[src_value]
            else:
                channel_name = f"UNKNOWN_{src_value}"
                source_enum = BusSource(src_value)

            transaction_type = TransactionType.WRITE if we == 1 else TransactionType.READ

            # Use request timestamp as main timestamp
            timestamp = req_ts

            return BusTransaction(
                timestamp=timestamp,
                source=source_enum,
                address=address,
                data=data,
                transaction_type=transaction_type,
                byte_enable=be,
                channel=channel_name,
                req_ts=req_ts,
                resp_ts=None   # <- remove misleading timing
            )

        except (ValueError, IndexError) as e:
            if row_num <= 10:  # Only show first 10 errors
                print(f"⚠️  Error parsing row {row_num}: {e}")
            return None

    def _parse_write_enable(self, we_str: str) -> int:
        """Parse write enable from string"""
        if not we_str:
            return 0

        we_str = we_str.upper()
        if we_str in ['1', 'W', 'WRITE']:
            return 1
        elif we_str in ['0', 'R', 'READ']:
            return 0
        else:
            try:
                return int(we_str)
            except ValueError:
                return 0

    def filter_by_time_range(self, start_time: int, end_time: Optional[int] = None) -> List[BusTransaction]:
        """Filter transactions by request timestamp range"""
        if end_time is None:
            filtered = [t for t in self.transactions if t.req_ts >= start_time]
        else:
            filtered = [t for t in self.transactions if start_time <= t.req_ts <= end_time]

        print(f"⏰ Filtered to {len(filtered)} transactions from req_ts={start_time}" +
              (f" to {end_time}" if end_time else " onwards"))
        return filtered

    def filter_by_response_time(self, start_time: int, end_time: Optional[int] = None) -> List[BusTransaction]:
        """Filter transactions by response timestamp range"""
        if end_time is None:
            filtered = [t for t in self.transactions if t.resp_ts >= start_time]
        else:
            filtered = [t for t in self.transactions if start_time <= t.resp_ts <= end_time]

        print(f"⏰ Filtered to {len(filtered)} transactions from resp_ts={start_time}" +
              (f" to {end_time}" if end_time else " onwards"))
        return filtered

    def find_sba_transactions(self, sba_start_time: int) -> List[BusTransaction]:
        """
        Find transactions that likely correspond to SBA operations
        SBA transactions typically come from PERIPH source after a certain time
        """
        # SBA transactions usually come from PERIPH source and happen after SBA start
        sba_candidates = []

        for transaction in self.transactions:
            if (transaction.req_ts >= sba_start_time and
                transaction.transaction_type == TransactionType.WRITE and
                transaction.channel in ("CORE_DATA", "PERIPH")):
                sba_candidates.append(transaction)


        print(f"🔍 Found {len(sba_candidates)} potential SBA transactions after req_ts={sba_start_time}")
        return sba_candidates

    # def get_time_statistics(self) -> Dict[str, Any]:
    #     """Get timing statistics about transactions"""
    #     if not self.transactions:
    #         return {}

    #     req_times = [t.req_ts for t in self.transactions]
    #     resp_times = [t.resp_ts for t in self.transactions]

    #     return {
    #         "req_ts_range": {
    #             "min": min(req_times),
    #             "max": max(req_times),
    #             "avg": sum(req_times) // len(req_times)
    #         },
    #         "resp_ts_range": {
    #             "min": min(resp_times),
    #             "max": max(resp_times),
    #             "avg": sum(resp_times) // len(resp_times)
    #         },
    #         "latency_stats": {
    #             "min": min(t.resp_ts - t.req_ts for t in self.transactions),
    #             "max": max(t.resp_ts - t.req_ts for t in self.transactions),
    #             "avg": sum(t.resp_ts - t.req_ts for t in self.transactions) // len(self.transactions)
    #         }
    #     }

    def get_time_statistics(self):
        """
        Since resp_ts does not represent actual bus response latency,
        we disable latency calculation and only report timestamp ranges.
        """
        if not self.transactions:
            return None

        timestamps = [t.timestamp for t in self.transactions]

        return {
            "req_ts_min": min(timestamps),
            "req_ts_max": max(timestamps),
            "count": len(timestamps),
        }


    # def print_timing_statistics(self):
    #     """Print timing statistics"""
    #     stats = self.get_time_statistics()

    #     if not stats:
    #         print("⏰ No timing data available")
    #         return

    #     print("\n" + "="*60)
    #     print("⏰ TIMING STATISTICS")
    #     print("="*60)
    #     print(f"Request Timestamp Range: {stats['req_ts_range']['min']} - {stats['req_ts_range']['max']}")
    #     print(f"Response Timestamp Range: {stats['resp_ts_range']['min']} - {stats['resp_ts_range']['max']}")
    #     # print(f"Average Latency (resp_ts - req_ts): {stats['latency_stats']['avg']} cycles")
    #     # print(f"Min/Max Latency: {stats['latency_stats']['min']} / {stats['latency_stats']['max']} cycles")
    #     print("Request Timestamp Range: {} - {}".format(stats["req_ts_min"], stats["req_ts_max"]))
    #     print("Number of Transactions: {}".format(stats["count"]))
    #     print("="*60)

    # ... [keep all the existing methods like filter_by_channel, filter_by_address_range, etc.] ...

    def print_timing_statistics(self):
        stats = self.get_time_statistics()
        if stats is None:
            print("No transactions recorded.")
            return

        print("\n⏰ TIMING STATISTICS")
        print("============================================================")
        print(f"Request Timestamp Range: {stats['req_ts_min']} - {stats['req_ts_max']}")
        print(f"Number of Transactions: {stats['count']}")

    def filter_by_channel(self, channels: List[str]) -> List[BusTransaction]:
        """Filter transactions by source channel"""
        filtered = [t for t in self.transactions if t.channel in channels]
        print(f"🔍 Filtered to {len(filtered)} transactions from channels: {channels}")
        return filtered

    def filter_by_address_range(self, start_addr: int, end_addr: int) -> List[BusTransaction]:
        """Filter transactions by address range"""
        filtered = [t for t in self.transactions if start_addr <= t.address <= end_addr]
        print(f"🔍 Filtered to {len(filtered)} transactions in address range 0x{start_addr:08x}-0x{end_addr:08x}")
        return filtered

    def filter_by_type(self, transaction_type: TransactionType) -> List[BusTransaction]:
        """Filter transactions by type (READ/WRITE)"""
        filtered = [t for t in self.transactions if t.transaction_type == transaction_type]
        print(f"🔍 Filtered to {len(filtered)} {transaction_type.value} transactions")
        return filtered

    def get_transaction_statistics(self) -> Dict[str, Any]:
        """Get statistics about captured transactions"""
        if not self.transactions:
            return {}

        stats = {
            "total_transactions": len(self.transactions),
            "read_count": len(self.filter_by_type(TransactionType.READ)),
            "write_count": len(self.filter_by_type(TransactionType.WRITE)),
            "channels": {},
            "address_range": {
                "min": min(t.address for t in self.transactions),
                "max": max(t.address for t in self.transactions)
            }
        }

        for transaction in self.transactions:
            channel = transaction.channel
            if channel not in stats["channels"]:
                stats["channels"][channel] = 0
            stats["channels"][channel] += 1

        return stats

    def print_statistics(self):
        """Print formatted statistics"""
        stats = self.get_transaction_statistics()

        if not stats:
            print("📊 No transactions to analyze")
            return

        print("\n" + "="*60)
        print("📊 BUS SNIFFER STATISTICS")
        print("="*60)
        print(f"Total Transactions: {stats['total_transactions']}")
        print(f"Read Operations: {stats['read_count']}")
        print(f"Write Operations: {stats['write_count']}")
        print(f"Address Range: 0x{stats['address_range']['min']:08x} - 0x{stats['address_range']['max']:08x}")

        print("\nTransactions by Channel:")
        for channel, count in stats['channels'].items():
            print(f"  {channel}: {count} transactions")
        print("="*60)

    def find_transactions(self, address: Optional[int] = None,
                         data: Optional[int] = None,
                         channel: Optional[str] = None,
                         transaction_type: Optional[TransactionType] = None,
                         min_req_ts: Optional[int] = None) -> List[BusTransaction]:
        """Find transactions matching specific criteria including timing"""
        results = self.transactions

        if address is not None:
            results = [t for t in results if t.address == address]
        if data is not None:
            results = [t for t in results if t.data == data]
        if channel is not None:
            results = [t for t in results if t.channel == channel]
        if transaction_type is not None:
            results = [t for t in results if t.transaction_type == transaction_type]
        if min_req_ts is not None:
            results = [t for t in results if t.req_ts >= min_req_ts]

        return results

    def export_to_csv(self, output_file: str):
        """Export transactions to CSV file"""
        if not self.transactions:
            print("❌ No transactions to export")
            return False

        try:
            with open(output_file, 'w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(['req_ts', 'resp_ts', 'channel', 'source', 'address', 'data', 'type', 'byte_enable'])

                for transaction in self.transactions:
                    writer.writerow([
                        transaction.req_ts,
                        transaction.resp_ts,
                        transaction.channel,
                        transaction.source.value,
                        f"0x{transaction.address:08x}",
                        f"0x{transaction.data:08x}",
                        transaction.transaction_type.value,
                        transaction.byte_enable
                    ])

            print(f"✅ Transactions exported to {output_file}")
            return True

        except Exception as e:
            print(f"❌ Error exporting to CSV: {e}")
            return False

    def print_transactions(self, limit: int = 20, min_req_ts: Optional[int] = None):
        """Print first N transactions, optionally filtered by time"""
        transactions_to_show = self.transactions
        if min_req_ts is not None:
            transactions_to_show = [t for t in transactions_to_show if t.req_ts >= min_req_ts]

        if not transactions_to_show:
            print("❌ No transactions to display")
            return

        time_filter_msg = f" (after req_ts={min_req_ts})" if min_req_ts else ""
        print(f"\n📋 First {min(limit, len(transactions_to_show))} transactions{time_filter_msg}:")
        print("-" * 100)
        for i, transaction in enumerate(transactions_to_show[:limit]):
            print(f"{i+1:3d}. {transaction}")

        if len(transactions_to_show) > limit:
            print(f"... and {len(transactions_to_show) - limit} more transactions")

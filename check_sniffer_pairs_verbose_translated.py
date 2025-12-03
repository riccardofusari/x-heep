import csv
import json

# Path to your sniffer CSV file
CSV_PATH = "build/openhwgroup.org_systems_core-v-mini-mcu_0/sim-verilator/sniffer_frames.csv"

with open("shadow_write_data.json") as f:
    shadow = json.load(f)

BASE_ADDR = shadow["base_addr"]
DATA      = shadow["data_words"]

print(f"Loaded {len(DATA)} expected words written at 0x{BASE_ADDR:08X}")
# Auto-generate (address, data) pairs
expected_pairs = [
    (BASE_ADDR + i * 4, DATA[i])
    for i in range(len(DATA))
]
# Define expected (address, data) pairs
# expected_pairs = [
#     # (0x00000000, 0xa5a5a5a5),
#     # (0x00000004, 0x5a5a5a5a),
#     # (0x00000008, 0xf0f0f0f0),
#     # (0x0000000c, 0x0f0f0f0f),
#     # (0x2002fff8, 0xa5a5a5a5),
#     # (0x2002fffc, 0x5a5a5a5a),
#     # (0x20030000, 0xf0f0f0f0),
#     # (0x20030004, 0x0f0f0f0f),
#     # (0x00000ed4, 0xa5a5a5a5),
#     # (0x00000ed8, 0x5a5a5a5a),
#     # (0x00000edc, 0xf0f0f0f0),
#     # (0x00000ee0, 0x0f0f0f0f),
#     (0x00008000, 0x00000000),
#     (0x00008004, 0x11111111),
#     (0x00008008, 0x22222222),
#     (0x0000800c, 0x33333333),
#     (0x00008010, 0x44444444),
#     (0x00008014, 0x55555555),
#     (0x00008018, 0x66666666),
#     (0x0000801c, 0x77777777),
#     (0x00008020, 0x88888888),
#     (0x00008024, 0x99999999),
#     (0x00008028, 0xaaaaaaaa),
#     (0x0000802c, 0xbbbbbbbb),
#     (0x00008030, 0xcccccccc),
#     (0x00008034, 0xdddddddd),
#     (0x00008038, 0xeeeeeeee),
#     (0x0000803c, 0xffffffff),
# ]

# Source name translation table
SRC_MAP = {
    1: "CORE_INSTR",
    2: "CORE_DATA",
    3: "AO_PERIPH",
    4: "PERIPH",
    5:"RAM0",6:"RAM1",7:"FLASH",8:"DMA_READ",9:"DMA_WRITE",10:"DMA_ADDR",
}

def translate_src(value):
    """Convert numeric or string source ID into readable label."""
    try:
        val = int(value)
        return SRC_MAP.get(val, f"UNKNOWN({val})")
    except ValueError:
        # sometimes 'src' might already be a name
        return value if value else "UNKNOWN"

# Store matches with details
found_details = {pair: [] for pair in expected_pairs}

# Read the CSV
with open(CSV_PATH, newline='') as csvfile:
    reader = csv.DictReader(csvfile)
    
    for row in reader:
        try:
            addr = int(row["address"], 16)
            data = int(row["data"], 16)
            we = int(row.get("we", "0"), 0)
            src_raw = row.get("src", "?")
            src = translate_src(src_raw)
            req_ts = int(row.get("req_ts", "0"))
        except Exception:
            continue  # skip malformed rows

        # Only check write transactions
        if we == 1:
            for exp_addr, exp_data in expected_pairs:
                if addr == exp_addr and data == exp_data:
                    found_details[(exp_addr, exp_data)].append((req_ts, src))

# Print results
print("=================================================")
print("🔍 SBA Sniffer Pair Check Results (Translated Sources)")
print("=================================================")

total_found = 0
for (addr, data), occurrences in found_details.items():
    if occurrences:
        print(f"✅ Found Write: addr=0x{addr:08x}, data=0x{data:08x}")
        for (req_ts, src) in occurrences:
            print(f"     ↳ req_ts={req_ts:<10} src={src}")
        total_found += len(occurrences)
    else:
        print(f"❌ Missing Write: addr=0x{addr:08x}, data=0x{data:08x}")

print("=================================================")
print(f"Total Found Occurrences: {total_found}")
print(f"Unique Expected Pairs Found: {sum(1 for v in found_details.values() if v)} / {len(expected_pairs)}")
print("=================================================")

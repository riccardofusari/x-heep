# Save this as driver.py
import socket
import time
import struct
import re
import json
from enum import Enum

class BusSource(Enum):
    CORE_INSTR = 1
    CORE_DATA = 2
    AO_PERIPH = 3
    PERIPH = 4
    DMA_READ = 8
    DMA_WRITE = 9

class OperationMode(Enum):
    SBA = 1
    LEGACY = 2

class XHeepSBADriver:
    performed_sba_writes = []
    def __init__(self, host='localhost', port=4444, mode=OperationMode.SBA):
        self.mode = mode
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        if self.mode == OperationMode.LEGACY:
            # For legacy mode, we use port 3333 (GDB port)
            self.socket.connect((host, 3333))
            print("✅ Connected to OpenOCD GDB server on port 3333")
            self._setup_legacy_mode()
        else:
            # SBA mode uses port 4444 (Telnet port)
            self.socket.connect((host, port))
            print("✅ Connected to OpenOCD Telnet on port 4444")
            self._wait_for_prompt()
            self._resume_cpu()
        
    def _setup_legacy_mode(self):
        """Setup GDB connection for legacy mode"""
        try:
            # Send initial GDB commands
            commands = [
                "set target-async on",
                "set pagination off", 
                "set confirm off",
                "set remotetimeout 2000",
                "load"
            ]
            
            for cmd in commands:
                self._send_gdb_command(cmd)
            
            print("✅ Legacy mode setup complete")
            
        except Exception as e:
            print(f"❌ Legacy mode setup failed: {e}")
            raise

    def _send_gdb_packet(self, payload):
        # Compute checksum
        checksum = sum(ord(c) for c in payload) % 256
        packet = f"${payload}#{checksum:02x}"
        self.socket.sendall(packet.encode())
        # Wait for acknowledgment '+'
        ack = self.socket.recv(1)
        if ack != b'+':
            raise Exception("No ACK from GDB server")
        # Receive response
        response = b""
        while True:
            chunk = self.socket.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"#" in chunk:
                break
        return response

    def _send_gdb_command(self, command, delay=0.2):
        """Send command to GDB server"""
        try:
            # Clear buffer
            self.socket.settimeout(0.1)
            try:
                while True:
                    data = self.socket.recv(1024)
                    if not data:
                        break
            except:
                pass
            self.socket.settimeout(10.0)
            
            # Send command
            full_command = f"{command}\n"
            self.socket.send(full_command.encode())
            
            time.sleep(delay)
            
            # Read response - GDB responses end with specific patterns
            response = b""
            start_time = time.time()
            while time.time() - start_time < 5.0:
                try:
                    chunk = self.socket.recv(1024)
                    if not chunk:
                        break
                    response += chunk
                    # GDB responses often end with (gdb) prompt or specific markers
                    if b"(gdb)" in response or response.endswith(b"^done"):
                        break
                except socket.timeout:
                    continue
            
            response_text = response.decode('utf-8', errors='ignore')
            return response_text
            
        except Exception as e:
            raise Exception(f"GDB command failed: {e}")

    def _wait_for_prompt(self):
        """Wait for OpenOCD prompt (SBA mode only)"""
        time.sleep(2.0)
        data = b""
        while not data.endswith(b"> "):
            try:
                chunk = self.socket.recv(1024)
                if not chunk:
                    break
                data += chunk
            except socket.timeout:
                break

    def _halt_cpu(self):
        """Halt CPU execution"""
        if self.mode == OperationMode.LEGACY:
            response = self._send_gdb_command("monitor halt")
            print("✅ CPU halted via GDB")
        else:
            try:
                self._send_command("halt")
                time.sleep(0.5)
                response = self._send_command("riscv is_halted")
                if "1" in response:
                    print("✅ CPU successfully halted")
                else:
                    print("⚠️  Could not verify CPU halt state")
            except Exception as e:
                print(f"⚠️  CPU halt attempt: {e}")

    def _resume_cpu(self):
        """Resume CPU execution"""
        if self.mode == OperationMode.LEGACY:
            response = self._send_gdb_command("continue")
            print("✅ CPU resumed via GDB")
        else:
            try:
                self._send_command("resume")
                time.sleep(0.5)
                response = self._send_command("riscv is_resumed")
                if "1" in response:
                    print("✅ CPU successfully resumed")
                else:
                    print("⚠️  Could not verify CPU resume state")
            except Exception as e:
                print(f"⚠️  CPU resume attempt: {e}")

    def step_cpu(self):
        """Step one instruction (Legacy mode only)"""
        if self.mode != OperationMode.LEGACY:
            print("⚠️  Step operation only available in Legacy mode")
            return None
            
        response = self._send_gdb_command("stepi")
        print("✅ CPU stepped one instruction")
        return response

    def get_pc(self):
        """Get program counter value"""
        if self.mode == OperationMode.LEGACY:
            response = self._send_gdb_command("info reg pc")
            # Parse PC value from response
            lines = response.split('\n')
            for line in lines:
                if 'pc' in line.lower():
                    print(f"📍 PC value: {line.strip()}")
                    return line.strip()
            return response
        else:
            # For SBA mode, you might need different approach
            print("⚠️  PC read not implemented for SBA mode")
            return None

    def set_memory(self, address, value):
        """Set memory value (Legacy mode)"""
        if self.mode == OperationMode.LEGACY:
            cmd = f"set *(int*)0x{address:08x} = 0x{value:x}"
            response = self._send_gdb_command(cmd)
            print(f"✅ Set memory 0x{address:08x} = 0x{value:x}")
            return response
        else:
            # Fall back to SBA write for legacy mode
            return self.write_word(address, value)

    def load_program(self):
        """Load program (Legacy mode)"""
        if self.mode != OperationMode.LEGACY:
            print("⚠️  Load operation only available in Legacy mode")
            return None
            
        response = self._send_gdb_command("load")
        print("✅ Program loaded")
        return response

    def set_breakpoint(self, location="main"):
        """Set breakpoint (Legacy mode)"""
        if self.mode != OperationMode.LEGACY:
            print("⚠️  Breakpoints only available in Legacy mode")
            return None
            
        response = self._send_gdb_command(f"break {location}")
        print(f"✅ Breakpoint set at {location}")
        return response

    def _send_command(self, command, delay=0.2):
        """Send command to OpenOCD (SBA mode only)"""
        if self.mode != OperationMode.SBA:
            print("⚠️  This method is for SBA mode only")
            return None
            
        try:
            self.socket.settimeout(0.1)
            try:
                while True:
                    self.socket.recv(1024)
            except:
                pass
            self.socket.settimeout(10.0)
            
            full_command = f"{command}\n"
            self.socket.send(full_command.encode())
            
            time.sleep(delay)
            
            response = b""
            start_time = time.time()
            while time.time() - start_time < 5.0:
                try:
                    chunk = self.socket.recv(1024)
                    if not chunk:
                        break
                    response += chunk
                    if response.endswith(b"> "):
                        break
                except socket.timeout:
                    continue
            
            response_text = response.decode('utf-8', errors='ignore')
            
            lines = response_text.split('\n')
            clean_lines = []
            for line in lines:
                line = line.strip()
                if not line or line == command or line == '>':
                    continue
                if line.startswith(command):
                    continue
                clean_lines.append(line)
            
            return '\n'.join(clean_lines)
            
        except Exception as e:
            raise Exception(f"OpenOCD command failed: {e}")
    
    def enable_dpi(self):
        """Enable DPI Mode (SBA mode)"""
        print("🔍 Enabling DPI mode (SBA)...")
        try:
            self.write_word(0x30080000 , 0x11)
            print("✅ DPI Mode Enabled ")
        except Exception as e:
            print(f"❌ Failed to enable DPI Mode: {e}")
            return False

    def write_word(self, address, value, retries=3):
        """Write 32-bit word (SBA mode)"""
        if self.mode != OperationMode.SBA:
            return self.set_memory(address, value)
            
        print("Generating Write Word traffic for sniffer monitoring...")
        self.performed_sba_writes.append((address, value))
        cmd = f"mww 0x{address:08x} 0x{value:08x}"
        
        for attempt in range(retries):
            try:
                response = self._send_command(cmd, delay=0.3)
                if "Broken pipe" in response or "dmi_scan failed" in response:
                    if attempt < retries - 1:
                        print(f"   ⚠️  Write attempt {attempt + 1} failed, retrying...")
                        time.sleep(0.5)
                        continue
                    else:
                        raise Exception("JTAG connection unstable during write")
                return response
            except Exception as e:
                if attempt < retries - 1:
                    print(f"   ⚠️  Write exception, retrying...: {e}")
                    time.sleep(0.5)
                else:
                    raise e
        return None
    
    def read_word(self, address):
        """Read 32-bit word (SBA mode)"""
        if self.mode != OperationMode.SBA:
            print("⚠️  Read word operation optimized for SBA mode")
            # For legacy mode, you might use GDB memory examination
            return 0
            
        cmd = f"mdw 0x{address:08x}"
        response = self._send_command(cmd)
        
        match = re.search(r':\s*([0-9a-fA-F]+)', response)
        if match:
            hex_value = match.group(1)
            if len(hex_value) < 8:
                hex_value = hex_value.zfill(8)
            try:
                return int(hex_value, 16)
            except ValueError:
                raise Exception(f"Failed to parse hex value: {hex_value}")
        
        raise Exception(f"Failed to parse read response: '{response}'")
    
    def write_burst(self, address, data_list):
        """Write multiple words"""
        success_count = 0
        # NEW: store written data for later tools/tests
        shadow = {
            "base_addr": address,
            "data_words": data_list
        }

        with open("shadow_write_data.json", "w") as f:
            json.dump(shadow, f)
        for i, value in enumerate(data_list):
            try:
                if self.mode == OperationMode.SBA:
                    self.write_word(address + i*4, value)
                else:
                    self.set_memory(address + i*4, value)
                success_count += 1
                print(f"   ✅ Write: addr=0x{address + i*4:08x} data=0x{value:08x}")
                time.sleep(0.1)
            except Exception as e:
                print(f"   ❌ Failed write: addr=0x{address + i*4:08x} error: {e}")
        return success_count
    
    def read_burst(self, address, count):
        """Read multiple words (SBA mode only)"""
        if self.mode != OperationMode.SBA:
            print("⚠️  Burst read optimized for SBA mode")
            return []
            
        results = []
        for i in range(count):
            try:
                value = self.read_word(address + i*4)
                results.append(value)
                print(f"   ✅ Read: addr=0x{address + i*4:08x} data=0x{value:08x}")
            except Exception as e:
                print(f"   ❌ Failed read: addr=0x{address + i*4:08x} error: {e}")
                results.append(0xDEADBEEF)
        return results
    
    def test_connection(self):
        """Test if connection is working"""
        try:
            if self.mode == OperationMode.SBA:
                test_addr = 0x00000000
                value = self.read_word(test_addr)
                print(f"✅ SBA Connection test PASSED - Read 0x{value:08x} from 0x{test_addr:08x}")
                return True
            else:
                # Test legacy connection by getting PC
                pkt = self._send_gdb_packet("?")
                if pkt:
                    print("✅ Legacy Connection test PASSED")
                    return True
                else:
                    return False
        except Exception as e:
            print(f"❌ Connection test FAILED: {e}")
            return False
    
    def generate_instruction_traffic(self, base_addr=0x20010000, count=3):
        """Generate instruction fetch-like traffic (SBA mode only)"""
        if self.mode != OperationMode.SBA:
            print("⚠️  Instruction traffic generation optimized for SBA mode")
            return []
            
        print("📖 Generating instruction traffic...")
        transactions = []
        for i in range(count):
            addr = base_addr + i * 4
            try:
                data = self.read_word(addr)
                transactions.append({
                    'src': BusSource.CORE_INSTR,
                    'addr': addr,
                    'data': data,
                    'we': 0,
                    'be': 0xF
                })
                print(f"   ✅ Instruction read: addr=0x{addr:08x} data=0x{data:08x}")
            except Exception as e:
                print(f"   ❌ Failed instruction read: addr=0x{addr:08x} error: {e}")
        return transactions
    
    def generate_read_only_traffic(self, addresses):
        """Generate traffic using only reads"""
        print("🔍 Generating read-only traffic...")
        for i, addr in enumerate(addresses):
            try:
                if self.mode == OperationMode.SBA:
                    data = self.read_word(addr)
                else:
                    # For legacy mode, use GDB to examine memory
                    response = self._send_gdb_command(f"x/x 0x{addr:08x}")
                    # Parse response to get data
                    data = 0  # You'd need to implement parsing
                print(f"   ✅ Read {i+1}: addr=0x{addr:08x} data=0x{data:08x}")
                time.sleep(0.05)
            except Exception as e:
                print(f"   ❌ Failed read {i+1}: addr=0x{addr:08x} error: {e}")
    
        #
    # --- GDB Remote Serial Protocol (RSP) helpers (for LEGACY mode) ---
    #
    def _gdb_checksum(self, payload: bytes) -> bytes:
        s = sum(payload) % 256
        return f"{s:02x}".encode()

    def _gdb_send_packet(self, payload_str: str, expect_reply=True, timeout=5.0) -> str:
        """
        Send RSP packet of form $payload#cs, wait for '+' ack, then read reply packet ($...#xx).
        Returns reply payload (str) without surrounding $/#checksum.
        """
        payload = payload_str.encode()
        packet = b"$" + payload + b"#" + self._gdb_checksum(payload)
        # send packet
        self.socket.settimeout(1.0)
        self.socket.sendall(packet)
        # wait for ACK '+'
        try:
            ack = self.socket.recv(1)
            if not ack:
                raise Exception("No ACK after sending packet")
            if ack not in (b'+', b'\x00', b'\x01'):  # b'+' is standard
                # sometimes server doesn't send explicit '+'; continue anyway
                pass
        except socket.timeout:
            # proceed — some servers won't ack, but still send reply
            pass

        if not expect_reply:
            return ""

        # read reply packet starting with $
        self.socket.settimeout(timeout)
        start = time.time()
        reply = b""
        while time.time() - start < timeout:
            try:
                chunk = self.socket.recv(4096)
                if not chunk:
                    continue
                reply += chunk
                # look for full packet $...#cs
                dollar = reply.find(b"$")
                hashpos = reply.find(b"#", dollar if dollar >= 0 else 0)
                if dollar >= 0 and hashpos > dollar and len(reply) >= hashpos + 3:
                    # full packet available
                    payload_bytes = reply[dollar+1:hashpos]
                    cs = reply[hashpos+1:hashpos+3]
                    # verify checksum
                    calc = self._gdb_checksum(payload_bytes)
                    if calc.lower() != cs.lower():
                        # bad checksum — request resend by NAK (rare). send '-' to request retry.
                        try:
                            self.socket.sendall(b'-')
                        except:
                            pass
                        # strip this packet and continue waiting
                        reply = reply[hashpos+3:]
                        continue
                    else:
                        # send ACK to server
                        try:
                            self.socket.sendall(b'+')
                        except:
                            pass
                        return payload_bytes.decode('utf-8', errors='ignore')
            except socket.timeout:
                continue
        raise Exception("Timeout waiting for RSP reply")

    def _gdb_write_memory(self, address: int, data_bytes: bytes):
        """
        Use 'M' packet to write binary memory.
        data_bytes must be the raw binary (not hex) - we'll hex-encode payload as required by RSP.
        RSP M packet format: M{addr},{length}:{hex-data}
        """
        hexdata = data_bytes.hex()
        payload = f"M{address:x},{len(data_bytes)}:{hexdata}"
        resp = self._gdb_send_packet(payload)
        # successful write typically replies "OK"
        return resp

    def _gdb_write_word(self, address: int, value: int):
        """
        Helper to write a 32-bit little-endian word to address using M packet.
        """
        # Pack as little-endian 4 bytes (change endianness if your target expects BE)
        b = struct.pack("<I", value)
        resp = self._gdb_write_memory(address, b)
        return resp

    def _gdb_write_burst(self, start_addr: int, data_list):
        """
        Write multiple 32-bit words via GDB (legacy mode), using consecutive M-packets.
        Equivalent to SBA write_burst().
        """
        success = 0

        for i, value in enumerate(data_list):
            addr = start_addr + i * 4
            try:
                # Reuse your existing word write helper
                self._gdb_write_word(addr, value)
                success += 1
                print(f"   📝 GDB Write: addr=0x{addr:08X} data=0x{value:08X}")
            except Exception as e:
                print(f"   ❌ GDB burst write failed at addr=0x{addr:08X}: {e}")
                break

        return success

    def _gdb_read_word(self, address: int) -> int:
        """
        Read a 32-bit word using GDB RSP 'm' packet.
        Returns the integer value (little-endian).
        """
        # RSP "m" packet: m{addr},{len}
        payload = f"m{address:x},4"
        resp = self._gdb_send_packet(payload)

        # GDB returns raw hex data (e.g. "78563412")
        if resp.startswith("E"):
            raise RuntimeError(f"GDB read failed at 0x{address:08x}: {resp}")

        # Convert hex -> bytes -> little-endian integer
        data_bytes = bytes.fromhex(resp)
        value = struct.unpack("<I", data_bytes)[0]
        print(f"   📝 GDB Read: addr=0x{address:08X} data=0x{value:08X}")
        return value
    
    def _gdb_read_burst(self, address: int, num_words: int):
        """
        Read multiple 32-bit words starting at address using consecutive RSP 'm' reads.
        Returns a list of integers.
        """
        values = []
        for i in range(num_words):
            addr_i = address + i*4
            v = self._gdb_read_word(addr_i)
            values.append(v)
        return values

    def _gdb_read_registers(self):
        """
        Send 'g' packet to read all registers. Returns hex string of registers.
        """
        resp = self._gdb_send_packet("g")
        return resp

    def _gdb_continue(self):
        """Send continue packet 'c' (resumes execution)."""
        resp = self._gdb_send_packet("c", expect_reply=False)
        # for continue, we don't expect immediate reply; target will later send stop reply when it stops
        return resp

    def _gdb_wait_for_stop(self, timeout=10.0):
        """
        Block until a stop reply is received from the target or timeout.
        Stop reply forms:
            $Sxx#cs  (stop with signal)
            $Txx...#cs (detailed stop with registers)
        Returns a tuple (payload, signal_int_or_None)
        """
        # Keep reading raw replies until one starts with S or T
        self.socket.settimeout(timeout)
        start = time.time()
        buf = b""
        while time.time() - start < timeout:
            try:
                chunk = self.socket.recv(4096)
                if not chunk:
                    continue
                buf += chunk
                # find first $...#..
                dollar = buf.find(b"$")
                hashpos = buf.find(b"#", dollar if dollar >=0 else 0)
                if dollar >= 0 and hashpos > dollar and len(buf) >= hashpos + 3:
                    payload = buf[dollar+1:hashpos].decode('utf-8', errors='ignore')
                    cs = buf[hashpos+1:hashpos+3]
                    # send ACK
                    try:
                        self.socket.sendall(b'+')
                    except:
                        pass
                    # parse stop
                    if payload.startswith('S') and len(payload) >= 3:
                        sig = int(payload[1:3], 16)
                        return (payload, sig)
                    if payload.startswith('T') and len(payload) >= 3:
                        sig = int(payload[1:3], 16)
                        return (payload, sig)
                    # else keep waiting (might be console output)
                    buf = buf[hashpos+3:]
            except socket.timeout:
                continue
        raise Exception("Timeout waiting for target stop reply")

    def _gdb_continue_and_wait_for_signal(self, timeout=15.0):
        """
        Combine continue + wait for stop and return signal number
        """
        # Send continue
        try:
            self._gdb_continue()
        except Exception as e:
            # if continue had an immediate error, still try to wait for stop
            print(f"⚠️  _gdb_continue error: {e}")
        payload, sig = self._gdb_wait_for_stop(timeout=timeout)
        return sig, payload

# driver.write_burst(0x00000000, burst_data)      
# driver.write_burst(0x2002FFF8, burst_data)
# driver.write_burst(0x00000ED4, burst_data)
    def run_fifo_test_via_gdb(self):
        """Write reset=2 then enable=1 and wait for SIGTRAP (SIGTRAP -> 5)"""
        if self.mode != OperationMode.LEGACY:
            print("⚠️  This test uses raw GDB RSP on port 3333 (Legacy mode).")
            return

        burst_data = [0xA5A5A5A5, 0x5A5A5A5A, 0xF0F0F0F0, 0x0F0F0F0F]
        print(f"Generating Write Word traffic (Legacy Mode test)")
        self._gdb_write_burst(0x00000000, burst_data)
        time.sleep(0.05)
        self._gdb_write_burst(0x2002FFF8, burst_data)
        time.sleep(0.05)
        self._gdb_write_burst(0x2002FFF8, burst_data)
        time.sleep(0.05)

        print(f"Generating Read Word traffic (Legacy Mode test)")
        self._gdb_read_burst(0x00000000, len(burst_data))
        time.sleep(0.05)
        self._gdb_read_burst(0x2002FFF8, len(burst_data))
        time.sleep(0.05)
        self._gdb_read_burst(0x2002FFF8, len(burst_data))
        time.sleep(0.05)
        

        fifo_ctl_addr = 0x30080000
        # Reset FIFO (0x2)
        print(f"Writing 0x2 to 0x{fifo_ctl_addr:08x} (reset FIFO)")
        self._gdb_write_word(fifo_ctl_addr, 0x2)
        time.sleep(0.05)

        print(f"Writing 0x11 to 0x{fifo_ctl_addr:08x} (disable DPI)")
        self._gdb_write_word(fifo_ctl_addr, 0x10)
        time.sleep(0.05)

        # Enable filling (0x1)
        print(f"Writing 0x1 to 0x{fifo_ctl_addr:08x} (enable filling)")
        self._gdb_write_word(fifo_ctl_addr, 0x1)
        time.sleep(0.05)

        # Continue and wait for stop (SIGTRAP expected)
        print("Continuing CPU and waiting for stop (SIGTRAP)...")
        try:
            sig, payload = self._gdb_continue_and_wait_for_signal(timeout=30.0)
            if sig == 5:
                print(f"✅ Received SIGTRAP (signal {sig}). Payload: {payload}")
            elif sig == 2:
                print(f"✅ Received SIGINT (signal {sig}). Payload: {payload}")
            else:
                print(f"⚠️  Target stopped with signal {sig}. Payload: {payload}")
        except Exception as e:
            print(f"❌ Did not get stop reply: {e}")

    def run_legacy_sequence(self):
        """Run the specific legacy mode command sequence"""
        if self.mode != OperationMode.LEGACY:
            print("⚠️  This sequence is for Legacy mode only")
            return
            
        print("🚀 Running legacy command sequence...")
        
        # Your specified command sequence
        commands = [
            # "$MADDR,LEN:DATA#CS", #"load"
            "$c#",
            "$M03080000,4:00000002#CS", # "set *(int*)0x30080000 = 0x2"
            "$M03080000,4:00000001#CS", # "set *(int*)0x30080000 = 0x2"
            "$c#"                       
            # "break main",
            # "set *(int*)0x30080000 = 0x2",
             # "set *(int*)0x30080000 = 0x2",
            # "c",
            # "info reg pc",
            # "set *(int*)0x30080000 = 0x2", 
            # "set *(int*)0x30080000 = rrr0x1",
            # "c"
        ]
        
        for cmd in commands:
            print(f"Executing: {cmd}")
            response = self._send_gdb_packet(cmd)
            if response:
                print(f"Response: {response[:100]}...")  # Print first 100 chars
            time.sleep(0.5)
    
    def close(self):
        """Close connection"""
        try:
            if self.mode == OperationMode.SBA:
                self._send_command("resume")
        except:
            pass
        self.socket.close()
        print("🔌 Connection closed")

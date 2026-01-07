#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "uart_regs.h"
#include "mmio.h"
#include "bitfield.h"

#define RESULT_ADDR       ((volatile uint32_t *)0x00008000u)
#define UART0_BASE_ADDR   0x200A0000u

#define SRAM0_BASE        0x00000000u
#define TEST_OFFSET       0x1000u
#define TEST_ADDR         (SRAM0_BASE + TEST_OFFSET)

static inline void write_uart_byte(uint8_t byte) {
  mmio_region_t uart_region = mmio_region_from_addr(UART0_BASE_ADDR);
  uint32_t reg = 0;
  reg = bitfield_field32_write(reg, UART_WDATA_WDATA_FIELD, byte);
  mmio_region_write32(uart_region, UART_WDATA_REG_OFFSET, reg);
}

static inline void write_sram_byte(uint32_t addr, uint8_t byte) {
  volatile uint8_t *ptr = (volatile uint8_t *)addr;
  *ptr = byte;
}

int main(void) {
  // Signal: test is running
  *RESULT_ADDR = 0x11111111u;

  // Test data: "HELLO01234"
  const uint8_t test_data[] = { 
    0x48, 0x45, 0x4C, 0x4C, 0x4F,  // "HELLO"
    0x30, 0x31, 0x32, 0x33, 0x34   // "01234"
  };
  const int test_len = 10;

  // Step 1: Write test data to SRAM0 @ offset 0x1000
  // (Simulating UART data being stored in memory)
  for (int i = 0; i < test_len; ++i) {
    uint32_t addr = TEST_ADDR + i;
    write_sram_byte(addr, test_data[i]);
  }

  // Step 2: Also output via UART for verification (optional)
  // This helps us see the data in the sniffer's UART transactions
  write_uart_byte(0x00);  // Dummy warm-up
  for (int i = 0; i < test_len; ++i) {
    write_uart_byte(test_data[i]);
  }

  // All steps completed successfully
  *RESULT_ADDR = 0xAAAAAAAAu;  // PASS

  // Loop forever
  while (1) {
    __asm volatile ("wfi");
  }

  return 0;
}
#include <stdio.h>
#include <stdlib.h>
#include "constants.h"
#include "x-heep.h"
#include "mmio.h"
#include "bus_sniffer_regs.h"


/* By default, printfs are activated for FPGA and disabled for simulation. */
#define PRINTF_IN_FPGA  1
#define PRINTF_IN_SIM   0

#if TARGET_SIM && PRINTF_IN_SIM
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#elif PRINTF_IN_FPGA
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#else
    #define PRINTF(...)
#endif

extern int add_asm_function(int a, int b);
extern int mul_by_const_asm_function( int a);

int main() {
    // unsigned int BUS_SNIFFER_START_ADDRESS = 0x30080000;
    // mmio_region_t bus_sniffer_base_addr = mmio_region_from_addr((uintptr_t)BUS_SNIFFER_START_ADDRESS);

    // mmio_region_write32(bus_sniffer_base_addr, BUS_SNIFFER_SNI_CTRL_REG_OFFSET, 2);
    // printf("Reset fifo");

    // mmio_region_write32(bus_sniffer_base_addr, BUS_SNIFFER_SNI_CTRL_REG_OFFSET, 1);
    // printf("Reset fifo");


      /* some dummy C values to store */
//   const uint32_t v0 = 39;
//   const uint32_t v1 = 42;
//   const uint32_t v2 = 123;

//   /* Inline-asm block that does six stores and loads in a row */
//   asm volatile (
//     /* store v0 at [FIFO_BASE + 0] */
//     "sw   %1, 0(%0)      \n"
//     /* load it back into t0 */
//     "lw   t0, 0(%0)      \n"

//     /* store v1 at [FIFO_BASE + 4] */
//     "sw   %2, 4(%0)      \n"
//     /* load it back into t1 */
//     "lw   t1, 4(%0)      \n"

//     /* store v2 at [FIFO_BASE + 8] */
//     "sw   %3, 8(%0)      \n"
//     /* load it back into t2 */
//     "lw   t2, 8(%0)      \n"
//     :
//     : "r"(BUS_SNIFFER_START_ADDRESS),  /* %0 = base address */
//       "r"(v0),         /* %1 = first value */
//       "r"(v1),         /* %2 = second value */
//       "r"(v2)          /* %3 = third value */
//     : "t0", "t1", "t2", "memory"
//   );

    // Define the base address of the bus_sniffer
    uintptr_t BUS_SNIFFER_START_ADDRESS = 0x30080000;
    mmio_region_t bus_sniffer = mmio_region_from_addr(BUS_SNIFFER_START_ADDRESS);

    // Reset FIFO (optional, ensure FIFO is empty at start)
    mmio_region_write32(bus_sniffer, BUS_SNIFFER_SNI_CTRL_REG_OFFSET, 0x2); // Reset
    mmio_region_write32(bus_sniffer, BUS_SNIFFER_SNI_CTRL_REG_OFFSET, 0x1); // Enable


    int num1 = 10;
    int num2 = 20;
    int sum = add_asm_function(num1, num2);
    int mul = mul_by_const_asm_function(num2);

    PRINTF("%d+%d=%d\n", num1, num2, sum);
    PRINTF("%d*%d=%d\n", num2, MULTIPLY_CONSTANT, mul );


    PRINTF("%d+%d=%d\n", num1, num2, sum);
    PRINTF("%d*%d=%d\n", num2, MULTIPLY_CONSTANT, mul );

    
    PRINTF("%d+%d=%d\n", num1, num2, sum);
    PRINTF("%d*%d=%d\n", num2, MULTIPLY_CONSTANT, mul );
    PRINTF("%d+%d=%d\n", num1, num2, sum);
    PRINTF("%d*%d=%d\n", num2, MULTIPLY_CONSTANT, mul );

    

    // Check FIFO full status in a loop (or wherever appropriate)
    // while (1) {
    //     uint32_t status = mmio_region_read32(bus_sniffer, BUS_SNIFFER_SNI_STATUS_REG_OFFSET);
    //     if (status & (1 << BUS_SNIFFER_SNI_STATUS_FULL_BIT)) {  // Assuming bit 0 = FIFO full flag (adjust as needed)
    //         PRINTF("FIFO full! Triggering SIGTRAP.\n");
    //         raise(5);  // Pause in GDB (SIGTRAP)
    //         // break;           // Exit loop after trapping (optional)
    //     }
    // }
    PRINTF("Sum is %d.\n", sum);
    return (sum == num1+num2) && (mul == num2*MULTIPLY_CONSTANT) ? EXIT_SUCCESS : EXIT_FAILURE;   
}

/**
 * @file main.c
 * @brief Bus Sniffer Validation Test - Concurrent CPU + DMA Operations
 *
 * This test validates the bus sniffer's ability to capture concurrent
 * transactions from multiple bus masters (CPU and DMA controller).
 *
 * @section test_objectives Test Objectives
 * - Verify transaction capture from CPU data accesses
 * - Verify transaction capture from DMA read/write channels
 * - Validate concurrent (interleaved) transaction capture
 * - Test timer peripheral accesses (polled mode)
 * - Verify data integrity through pattern matching
 *
 * @section memory_map Memory Map (from core_v_mini_mcu_pkg.sv)
 * - RAM0: 0x00000000 - 0x00007FFF (32KB)
 * - RAM1: 0x00008000 - 0x0000FFFF (32KB)
 *
 * @section test_buffers Test Buffer Layout
 * RAM0:
 *   - 0x00002000: Buffer A (64 bytes) - DMA #1 source
 *   - 0x00002100: Buffer B (64 bytes) - CPU computation source
 *   - 0x00002200: Workspace (256 bytes) - CPU results
 *   - 0x00002300: Buffer D (64 bytes) - DMA #2 source
 *
 * RAM1:
 *   - 0x00008000: Buffer C (64 bytes) - DMA #1 destination
 *   - 0x00008100: Final result (4 bytes)
 *   - 0x00008200: Buffer E (64 bytes) - DMA #2 destination
 *   - 0x00008300: Timer interrupt counter (4 bytes)
 *
 * @section test_flow Test Flow
 * - PHASE 0: Initialize all buffers with deterministic patterns
 * - PHASE 1: DMA #1 (A->C) concurrent with CPU computation on B
 * - PHASE 2: Wait for DMA #1, verify transfer
 * - PHASE 3: DMA #2 (D->E) concurrent with CPU computation on C
 * - PHASE 4: Wait for DMA #2, verify transfer, write final result
 *
 * @section expected_sniffer Expected Sniffer Observations
 * - CORE_DATA: CPU reads from B, writes to workspace, verification reads
 * - DMA_READ: 64 reads from A, 64 reads from D
 * - DMA_WRITE: 64 writes to C, 64 writes to E
 * - AO_PERIPH: Timer register accesses, UART writes
 * - RAM0/RAM1: Slave-side view of all memory transactions
 *
 * @author Bus Sniffer Validation Team
 * @date 2025
 */

#include <stdint.h>
#include <stddef.h>
#include "core_v_mini_mcu.h"
#include "dma.h"
#include "rv_timer.h"
#include "rv_timer_structs.h"

/* ============================================================================
 * PERIPHERAL BASE ADDRESSES
 * ============================================================================ */

#define RV_TIMER_AO_BASE        0x20050000u     /**< Always-on timer base */
#define UART_BASE               0x200A0000u     /**< UART peripheral base */
#define UART_WDATA              (UART_BASE + 0x18u)  /**< UART write data register */

/* ============================================================================
 * MEMORY MAP DEFINITIONS
 * ============================================================================ */

/* RAM0 region (0x00000000 - 0x00007FFF) */
#define RAM0_BUFFER_A           0x00002000u     /**< DMA #1 source buffer */
#define RAM0_BUFFER_B           0x00002100u     /**< CPU computation source */
#define RAM0_WORKSPACE          0x00002200u     /**< CPU workspace for results */
#define RAM0_BUFFER_D           0x00002300u     /**< DMA #2 source buffer */

/* RAM1 region (0x00008000 - 0x0000FFFF) */
#define RAM1_BUFFER_C           0x00008000u     /**< DMA #1 destination buffer */
#define RAM1_FINAL_RESULT       0x00008100u     /**< Test result location */
#define RAM1_BUFFER_E           0x00008200u     /**< DMA #2 destination buffer */
#define RAM1_TIMER_IRQ_COUNT    0x00008300u     /**< Timer event counter */

/* Buffer sizes */
#define BUFFER_SIZE             64u             /**< Size of each data buffer */
#define WORKSPACE_SIZE          256u            /**< Size of CPU workspace */

/* ============================================================================
 * TEST PATTERNS
 * ============================================================================
 * Each buffer uses a distinct base pattern for easy identification:
 * - Pattern A: 0xA0 | (index & 0x0F) -> 0xA0, 0xA1, ..., 0xAF, 0xA0, ...
 * - Pattern B: 0xB0 | (index & 0x0F) -> 0xB0, 0xB1, ..., 0xBF, 0xB0, ...
 * - Pattern D: 0xD0 | (index & 0x0F) -> 0xD0, 0xD1, ..., 0xDF, 0xD0, ...
 */

#define PATTERN_A_BASE          0xA0u
#define PATTERN_B_BASE          0xB0u
#define PATTERN_D_BASE          0xD0u

/* ============================================================================
 * RESULT CODES
 * ============================================================================
 * Distinct codes allow identification of failure point from Python test.
 */

#define RESULT_RUNNING          0x11111111u     /**< Test in progress */
#define RESULT_PASS             0xAAAAAAAAu     /**< All tests passed */
#define RESULT_FAIL_DMA1        0xDEAD0001u     /**< DMA #1 launch failed */
#define RESULT_FAIL_DMA2        0xDEAD0002u     /**< DMA #2 launch failed */
#define RESULT_FAIL_VERIFY1     0xDEAD0011u     /**< DMA #1 data verification failed */
#define RESULT_FAIL_VERIFY2     0xDEAD0012u     /**< DMA #2 data verification failed */

/* ============================================================================
 * TIMER CONFIGURATION (Polled Mode)
 * ============================================================================ */

#define TIMER_PERIOD_TICKS      100u            /**< Timer polling interval */

static rv_timer_t g_timer;
static uint64_t g_next_deadline = 0;

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Generate pattern A value for given index
 * @param i Byte index (0-63)
 * @return Pattern byte value
 */
static inline uint8_t pattern_a(int i) {
    return PATTERN_A_BASE | (i & 0x0F);
}

/**
 * @brief Generate pattern B value for given index
 * @param i Byte index (0-63)
 * @return Pattern byte value
 */
static inline uint8_t pattern_b(int i) {
    return PATTERN_B_BASE | (i & 0x0F);
}

/**
 * @brief Generate pattern D value for given index
 * @param i Byte index (0-63)
 * @return Pattern byte value
 */
static inline uint8_t pattern_d(int i) {
    return PATTERN_D_BASE | (i & 0x0F);
}

/**
 * @brief Write single character to UART (non-blocking)
 * @param c Character to transmit
 *
 * Generates AO_PERIPH write transaction visible in sniffer.
 */
static inline void uart_putc(uint8_t c) {
    *(volatile uint32_t *)UART_WDATA = (uint32_t)c;
}

/* ============================================================================
 * TIMER FUNCTIONS (Polled Mode)
 * ============================================================================
 * Timer is used in polled mode to generate periodic AO_PERIPH transactions
 * and RAM1 counter updates, creating mixed peripheral/memory traffic.
 */

/**
 * @brief Initialize RV_TIMER_AO in polled mode
 *
 * Configures timer for periodic events without using interrupts.
 */
static void timer_init_polled(void) {
    rv_timer_config_t cfg = {
        .hart_count = 1,
        .comparator_count = 1,
    };

    (void)rv_timer_init(mmio_region_from_addr(RV_TIMER_AO_BASE), cfg, &g_timer);

    rv_timer_tick_params_t tick = {
        .prescale = 0,
        .tick_step = 1,
    };
    (void)rv_timer_set_tick_params(&g_timer, 0, tick);

    uint64_t now = 0;
    (void)rv_timer_counter_read(&g_timer, 0, &now);
    g_next_deadline = now + (uint64_t)TIMER_PERIOD_TICKS;

    (void)rv_timer_arm(&g_timer, 0, 0, g_next_deadline);
}

/**
 * @brief Service timer in polling mode
 *
 * Called periodically to generate timer register traffic (AO_PERIPH),
 * update RAM1 counter, and output UART heartbeat.
 *
 * Uses software divider to control event rate independently of
 * actual timer counter advancement.
 */
static inline void timer_service_if_due(void) {
    static uint32_t sw_div = 0;
    sw_div++;
    if ((sw_div & 0x7u) != 0u) {
        return;  /* Service 1 in 8 calls */
    }

    volatile rv_timer *regs = (volatile rv_timer *)RV_TIMER_AO_BASE;

    /* Read and clear interrupt status (generates AO_PERIPH traffic) */
    volatile uint32_t intr = regs->INTR_STATE00;
    (void)intr;
    regs->INTR_STATE00 = 1u;

    /* Read timer counter registers (additional AO_PERIPH traffic) */
    volatile uint32_t lo = regs->TIMER_V_LOWER0;
    volatile uint32_t hi = regs->TIMER_V_UPPER0;
    (void)lo;
    (void)hi;

    /* Increment RAM1 counter (generates CORE_DATA write to RAM1) */
    (*(volatile uint32_t *)RAM1_TIMER_IRQ_COUNT)++;

    /* UART heartbeat (generates AO_PERIPH write) */
    uart_putc('.');

    /* Re-arm timer for next event */
    g_next_deadline += TIMER_PERIOD_TICKS;
    (void)rv_timer_arm(&g_timer, 0, 0, g_next_deadline);
}

/* ============================================================================
 * BUFFER INITIALIZATION (PHASE 0)
 * ============================================================================ */

/**
 * @brief Initialize all test buffers with deterministic patterns
 *
 * Generates the following sniffer-visible transactions:
 * - 64 CORE_DATA writes to Buffer A (pattern 0xA0|i)
 * - 64 CORE_DATA writes to Buffer B (pattern 0xB0|i)
 * - 64 CORE_DATA writes to Buffer C (clear to 0x00)
 * - 64 CORE_DATA writes to Buffer D (pattern 0xD0|i)
 * - 64 CORE_DATA writes to Buffer E (clear to 0x00)
 * - 256 CORE_DATA writes to Workspace (clear to 0x00)
 */
static void init_buffers(void) {
    volatile uint8_t *buf_a = (volatile uint8_t *)RAM0_BUFFER_A;
    volatile uint8_t *buf_b = (volatile uint8_t *)RAM0_BUFFER_B;
    volatile uint8_t *buf_c = (volatile uint8_t *)RAM1_BUFFER_C;
    volatile uint8_t *buf_d = (volatile uint8_t *)RAM0_BUFFER_D;
    volatile uint8_t *buf_e = (volatile uint8_t *)RAM1_BUFFER_E;
    volatile uint8_t *workspace = (volatile uint8_t *)RAM0_WORKSPACE;

    /* Buffer A: DMA #1 source with pattern A */
    for (int i = 0; i < BUFFER_SIZE; i++) {
        buf_a[i] = pattern_a(i);
    }

    /* Buffer B: CPU computation source with pattern B */
    for (int i = 0; i < BUFFER_SIZE; i++) {
        buf_b[i] = pattern_b(i);
    }

    /* Buffer C: DMA #1 destination (clear) */
    for (int i = 0; i < BUFFER_SIZE; i++) {
        buf_c[i] = 0x00;
    }

    /* Buffer D: DMA #2 source with pattern D */
    for (int i = 0; i < BUFFER_SIZE; i++) {
        buf_d[i] = pattern_d(i);
    }

    /* Buffer E: DMA #2 destination (clear) */
    for (int i = 0; i < BUFFER_SIZE; i++) {
        buf_e[i] = 0x00;
    }

    /* Workspace: CPU computation results (clear) */
    for (int i = 0; i < WORKSPACE_SIZE; i++) {
        workspace[i] = 0x00;
    }
}

/* ============================================================================
 * DMA TRANSFER FUNCTIONS
 * ============================================================================ */

/* Static DMA structures (must persist across function calls) */
static dma_target_t dma_src;
static dma_target_t dma_dst;
static dma_trans_t  dma_trans;

/**
 * @brief Configure and launch a 1D DMA transfer
 *
 * @param src_addr Source address in memory
 * @param dst_addr Destination address in memory
 * @param size     Number of bytes to transfer
 * @return 0 on success, negative error code on failure
 *
 * Generates sniffer-visible transactions:
 * - DMA_READ: 'size' byte reads from src_addr
 * - DMA_WRITE: 'size' byte writes to dst_addr
 */
static int start_dma_transfer(uint32_t src_addr, uint32_t dst_addr, uint32_t size) {
    dma_init(NULL);

    /* Configure source */
    dma_src.ptr        = (uint8_t *)src_addr;
    dma_src.inc_d1_du  = 1;
    dma_src.inc_d2_du  = 0;
    dma_src.type       = DMA_DATA_TYPE_BYTE;
    dma_src.trig       = DMA_TRIG_MEMORY;
    dma_src.env        = NULL;

    /* Configure destination */
    dma_dst.ptr        = (uint8_t *)dst_addr;
    dma_dst.inc_d1_du  = 1;
    dma_dst.inc_d2_du  = 0;
    dma_dst.type       = DMA_DATA_TYPE_BYTE;
    dma_dst.trig       = DMA_TRIG_MEMORY;
    dma_dst.env        = NULL;

    /* Configure transaction */
    dma_trans.src           = &dma_src;
    dma_trans.dst           = &dma_dst;
    dma_trans.src_addr      = NULL;
    dma_trans.size_d1_du    = size;
    dma_trans.size_d2_du    = 0;
    dma_trans.dim           = DMA_DIM_CONF_1D;
    dma_trans.pad_top_du    = 0;
    dma_trans.pad_bottom_du = 0;
    dma_trans.pad_left_du   = 0;
    dma_trans.pad_right_du  = 0;
    dma_trans.src_type      = DMA_DATA_TYPE_BYTE;
    dma_trans.dst_type      = DMA_DATA_TYPE_BYTE;
    dma_trans.sign_ext      = 0;
    dma_trans.mode          = DMA_TRANS_MODE_SINGLE;
    dma_trans.dim_inv       = 0;
    dma_trans.win_du        = 0;
    dma_trans.end           = DMA_TRANS_END_POLLING;
    dma_trans.channel       = 0;
    dma_trans.flags         = DMA_CONFIG_OK;

    /* Validate transaction */
    dma_config_flags_t flags = dma_validate_transaction(
        &dma_trans,
        DMA_DO_NOT_ENABLE_REALIGN,
        DMA_PERFORM_CHECKS_INTEGRITY
    );
    if (flags & DMA_CONFIG_CRITICAL_ERROR) {
        return -1;
    }

    /* Load and launch */
    flags = dma_load_transaction(&dma_trans);
    if (flags & DMA_CONFIG_CRITICAL_ERROR) {
        return -2;
    }

    flags = dma_launch(&dma_trans);
    if (flags & DMA_CONFIG_CRITICAL_ERROR) {
        return -3;
    }

    return 0;
}

/**
 * @brief Wait for DMA transfer completion (polling)
 * @return 0 when DMA is ready
 *
 * Polls DMA status while calling timer service to generate
 * continuous peripheral traffic during wait.
 */
static int wait_for_dma(void) {
    while (!dma_is_ready(0)) {
        timer_service_if_due();
        asm volatile("nop");
    }
    return 0;
}

/* ============================================================================
 * CPU COMPUTATION FUNCTIONS
 * ============================================================================ */

/**
 * @brief CPU computation concurrent with DMA #1
 *
 * Reads Buffer B, computes partial sums, writes to workspace.
 * Generates CORE_DATA read/write traffic while DMA is active.
 *
 * @return Computed checksum value
 */
static uint32_t cpu_computation(void) {
    volatile uint8_t *buf_b = (volatile uint8_t *)RAM0_BUFFER_B;
    volatile uint32_t *workspace = (volatile uint32_t *)RAM0_WORKSPACE;

    uint32_t checksum = 0;

    /* Process buffer B in 16-byte blocks */
    for (int block = 0; block < BUFFER_SIZE / 16; block++) {
        timer_service_if_due();
        uint32_t partial_sum = 0;

        /* Read 16 bytes (generates 16 CORE_DATA reads) */
        for (int i = 0; i < 16; i++) {
            int idx = block * 16 + i;
            partial_sum += buf_b[idx];
        }

        /* Write partial sum to workspace (generates CORE_DATA write) */
        workspace[block] = partial_sum;

        /* Accumulate checksum */
        checksum ^= partial_sum;
    }

    /* Store final checksum */
    workspace[BUFFER_SIZE / 16] = checksum;

    return checksum;
}

/**
 * @brief CPU computation concurrent with DMA #2
 *
 * Reads Buffer C (DMA #1 result), computes partial sums,
 * writes to different workspace region.
 *
 * @return Computed checksum value
 */
static uint32_t cpu_computation_phase2(void) {
    volatile uint8_t *buf_c = (volatile uint8_t *)RAM1_BUFFER_C;
    volatile uint32_t *workspace = (volatile uint32_t *)RAM0_WORKSPACE;

    uint32_t checksum = 0;

    /* Process buffer C in 16-byte blocks */
    for (int block = 0; block < BUFFER_SIZE / 16; block++) {
        timer_service_if_due();
        uint32_t partial_sum = 0;

        for (int i = 0; i < 16; i++) {
            int idx = block * 16 + i;
            partial_sum += buf_c[idx];
        }

        /* Write to separate workspace region */
        workspace[16 + block] = partial_sum;
        checksum ^= partial_sum;
    }

    workspace[20] = checksum;
    return checksum;
}

/* ============================================================================
 * VERIFICATION FUNCTIONS
 * ============================================================================ */

/**
 * @brief Verify DMA #1 transfer (A -> C)
 * @return Number of byte mismatches (0 = success)
 */
static uint32_t verify_dma_transfer_1(void) {
    volatile uint8_t *dst = (volatile uint8_t *)RAM1_BUFFER_C;
    uint32_t errors = 0;

    for (int i = 0; i < BUFFER_SIZE; i++) {
        uint8_t expected = pattern_a(i);
        uint8_t actual = dst[i];
        if (actual != expected) {
            errors++;
        }
    }

    return errors;
}

/**
 * @brief Verify DMA #2 transfer (D -> E)
 * @return Number of byte mismatches (0 = success)
 */
static uint32_t verify_dma_transfer_2(void) {
    volatile uint8_t *dst = (volatile uint8_t *)RAM1_BUFFER_E;
    uint32_t errors = 0;

    for (int i = 0; i < BUFFER_SIZE; i++) {
        uint8_t expected = pattern_d(i);
        uint8_t actual = dst[i];
        if (actual != expected) {
            errors++;
        }
    }

    return errors;
}

/* ============================================================================
 * MAIN FUNCTION
 * ============================================================================ */

/**
 * @brief Main test entry point
 *
 * Executes the complete bus sniffer validation test sequence.
 */
int main(void) {
    volatile uint32_t *result = (volatile uint32_t *)RAM1_FINAL_RESULT;

    /* Signal test start */
    *result = RESULT_RUNNING;

    /* Initialize timer for polled service */
    timer_init_polled();

    /* Generate initial timer/UART burst for sniffer warmup */
    for (int i = 0; i < 64; ++i) {
        timer_service_if_due();
    }

    /* ========================================
     * PHASE 0: Initialize all buffers
     * ======================================== */
    init_buffers();

    /* ========================================
     * PHASE 1: DMA #1 (A->C) + CPU on B
     * ======================================== */
    int dma_status = start_dma_transfer(RAM0_BUFFER_A, RAM1_BUFFER_C, BUFFER_SIZE);
    if (dma_status != 0) {
        *result = RESULT_FAIL_DMA1;
        while (1) { asm volatile("wfi"); }
    }

    /* CPU computes while DMA #1 transfers */
    uint32_t cpu_checksum1 = cpu_computation();
    (void)cpu_checksum1;

    /* ========================================
     * PHASE 2: Wait for DMA #1, verify
     * ======================================== */
    wait_for_dma();

    uint32_t dma1_errors = verify_dma_transfer_1();
    if (dma1_errors != 0) {
        *result = RESULT_FAIL_VERIFY1;
        while (1) { asm volatile("wfi"); }
    }

    /* ========================================
     * PHASE 3: DMA #2 (D->E) + CPU on C
     * ======================================== */
    dma_status = start_dma_transfer(RAM0_BUFFER_D, RAM1_BUFFER_E, BUFFER_SIZE);
    if (dma_status != 0) {
        *result = RESULT_FAIL_DMA2;
        while (1) { asm volatile("wfi"); }
    }

    /* CPU processes DMA #1 result while DMA #2 transfers */
    uint32_t cpu_checksum2 = cpu_computation_phase2();
    (void)cpu_checksum2;

    /* ========================================
     * PHASE 4: Wait for DMA #2, final verify
     * ======================================== */
    wait_for_dma();

    uint32_t dma2_errors = verify_dma_transfer_2();

    /* Write final result */
    if (dma2_errors == 0) {
        *result = RESULT_PASS;
    } else {
        *result = RESULT_FAIL_VERIFY2;
    }

    /* Halt CPU */
    while (1) {
        asm volatile("wfi");
    }

    return 0;
}
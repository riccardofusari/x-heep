// Generated register defines for bus_sniffer

#ifndef _BUS_SNIFFER_REG_DEFS_
#define _BUS_SNIFFER_REG_DEFS_

#ifdef __cplusplus
extern "C" {
#endif
// Register width
#define BUS_SNIFFER_PARAM_REG_WIDTH 32

// Control register: Bit0 enables the bus sniffer; Bit1 resets the FIFO; Bit2
// if the frame is stored in sw
#define BUS_SNIFFER_SNI_CTRL_REG_OFFSET 0x0
#define BUS_SNIFFER_SNI_CTRL_EN_BIT 0
#define BUS_SNIFFER_SNI_CTRL_RST_FIFO_BIT 1
#define BUS_SNIFFER_SNI_CTRL_FRAME_READ_BIT 2
#define BUS_SNIFFER_SNI_CTRL_ENABLE_GATING_BIT 3

// Status register: Bit0 = EMPTY, Bit1 = FULL, Bit2 = FRAME_AVAIL., Bit3 =
// FRAME_READ
#define BUS_SNIFFER_SNI_STATUS_REG_OFFSET 0x4
#define BUS_SNIFFER_SNI_STATUS_EMPTY_BIT 0
#define BUS_SNIFFER_SNI_STATUS_FULL_BIT 1
#define BUS_SNIFFER_SNI_STATUS_FRAME_AVAIL_BIT 2

// Data register 0: Upper 32 bits of captured frame.
#define BUS_SNIFFER_SNI_DATA0_REG_OFFSET 0x8

// Data register 1: Next 32 bits of captured frame.
#define BUS_SNIFFER_SNI_DATA1_REG_OFFSET 0xc

// Data register 2: Next 32 bits of captured frame.
#define BUS_SNIFFER_SNI_DATA2_REG_OFFSET 0x10

// Data register 3: Lower 32 bits of captured frame.
#define BUS_SNIFFER_SNI_DATA3_REG_OFFSET 0x14

#ifdef __cplusplus
}  // extern "C"
#endif
#endif  // _BUS_SNIFFER_REG_DEFS_
// End generated register defines for bus_sniffer
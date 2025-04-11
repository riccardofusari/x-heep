package bus_sniffer_pkg;

  import core_v_mini_mcu_pkg::*;  // NUM_BANKS
  import obi_pkg::*;  // For obi_req_t, obi_resp_t definitions

  // -----------------------------------------------------------------------------
  // This structure bundles all bus signals from the core_v_mini_mcu
  // -----------------------------------------------------------------------------

  typedef struct packed {
    // Core-related signals
    obi_req_t  core_instr_req;
    obi_resp_t core_instr_resp;
    obi_req_t  core_data_req;
    obi_resp_t core_data_resp;

    // DMA-related signals (arrays size [0:0] are effectively single element)
    obi_req_t  dma_read_req;
    obi_resp_t dma_read_resp;
    obi_req_t  dma_write_req;
    obi_resp_t dma_write_resp;
    obi_req_t  dma_addr_req;
    obi_resp_t dma_addr_resp;

    // Peripherals
    obi_req_t  ao_peripheral_slave_req;
    obi_resp_t ao_peripheral_slave_resp;
    obi_req_t  peripheral_slave_req;
    obi_resp_t peripheral_slave_resp;

    // RAM signals
    obi_req_t [core_v_mini_mcu_pkg::NUM_BANKS-1:0]  ram_slave_req;
    obi_resp_t [core_v_mini_mcu_pkg::NUM_BANKS-1:0] ram_slave_resp;

    // Memory Map SPI Region
    obi_req_t  flash_mem_slave_req;
    obi_resp_t flash_mem_slave_resp;
  } bus_sniffer_bundle_t;

  //--------------------------------------------------------------------------
  //    128 bits
  //--------------------------------------------------------------------------

  typedef struct packed {
    logic [3:0]  source_id;       // [127:124]
    logic [31:0] req_timestamp;   // [123:108]
    logic [15:0] resp_timestamp;  // [107:92]
    logic [31:0] address;         // [91:60]
    logic [31:0] data;            // [59:28]
    logic [3:0]  byte_enable;     // [27:24]
    logic        we;              // [23]
    logic        valid;           // [22]
    logic        gnt;             // [21]
    logic [4:0]  reserved;        // [20:0]
  } bus_sniffer_frame_t;



  // The partial transaction table
  typedef struct packed {
    logic               free_slot;     // entry is in use
    logic               waiting_resp;  // 1 if read & waiting for rvalid
    logic [3:0]         channel_id;    // e.g. CORE_DATA, DMA_READ, etc.
    bus_sniffer_frame_t frame;
  } partial_entry_t;

  typedef enum int {
    CH_CORE_INSTR,
    CH_CORE_DATA,
    CH_AO_PERIPH,
    CH_PERIPH,
    CH_RAM0,
    CH_RAM1,
    CH_FLASH,
    CH_DMA_READ,
    CH_DMA_WRITE,
    CH_DMA_ADDR
  } channel_e;

  //--------------------------------------------------------------------------
  // Source IDs for each set of signals
  //--------------------------------------------------------------------------
  parameter logic [3:0] CORE_INSTR = 4'h01;
  parameter logic [3:0] CORE_DATA = 4'h02;
  parameter logic [3:0] AO_PERIPH = 4'h03;
  parameter logic [3:0] PERIPH = 4'h04;
  parameter logic [3:0] RAM0 = 4'h05;
  parameter logic [3:0] RAM1 = 4'h06;
  parameter logic [3:0] FLASH = 4'h07;
  parameter logic [3:0] DMA_READ = 4'h08;
  parameter logic [3:0] DMA_WRITE = 4'h09;
  parameter logic [3:0] DMA_ADDR = 4'h0A;


endpackage : bus_sniffer_pkg

module bus_sniffer
  import bus_sniffer_pkg::*;
  import bus_sniffer_reg_pkg::*;
#(
    parameter type reg_req_t = logic,
    parameter type reg_rsp_t = logic,
    parameter FRAME_WIDTH   = 128,
    parameter FIFO_DEPTH    = 1024,
    parameter TABLE_DEPTH   = 10,
    parameter NUM_CHANNELS  = 10  // how many bus channels we can monitor
) (
    input logic clk_i,
    input logic rst_ni,

    // Register interface
    input  reg_req_t reg_req_i,
    output reg_rsp_t reg_rsp_o,


    output logic                halt_state_o,
    output logic                clk_gate_o,
    input  bus_sniffer_bundle_t bus_sniffer_bundle_i,
    input  logic                debug_mode_i
);


  // Memory mapped registers interface signals
    /* verilator lint_off UNUSED */
  bus_sniffer_reg2hw_t reg2hw;
  bus_sniffer_hw2reg_t hw2reg;


  logic [31:0] sni_data0;
  logic [31:0] sni_data1;
  logic [31:0] sni_data2;
  logic [31:0] sni_data3;

  logic rst_fifo_reg;
  logic rst_fifo_reg_ff;
  logic rst_fifo;

  logic frame_read_q_d;           // 1-cycle delayed copy of .q
  wire  frame_read_rise;          // rising-edge detect of SW write
  logic frame_pending;            // stays high until next SW ack
  // logic frame_read_sw;
  // logic frame_read_sw_q;
  // logic frame_read_ack;

  logic enable_gating_reg;
  logic capture_en = ~debug_mode_i;  // cattura solo fuori dal debug

  // Status + data -> HW drives them continuously
  assign hw2reg.sni_status.empty.de       = 1'b1;
  assign hw2reg.sni_status.full.de        = 1'b1;
  assign hw2reg.sni_status.frame_avail.de = 1'b1;
  assign hw2reg.sni_status.empty.d        = empty;
  assign hw2reg.sni_status.full.d         = full;
  // CHANGED: drive sticky 'frame_pending', not a 1-cycle pulse
  // assign hw2reg.sni_status.frame_avail.d  = pop_fifo;
  assign hw2reg.sni_status.frame_avail.d  = frame_pending;

  // auto-clear pulse for FRAME_READ (drive hw2reg only via continuous assigns)
  logic frame_read_autoclr_pulse;
  assign hw2reg.sni_ctrl.frame_read.de = frame_read_autoclr_pulse; // 1-cycle when we want to clear
  assign hw2reg.sni_ctrl.frame_read.d  = 1'b0;                     // clear value

  assign hw2reg.sni_data0.de              = 1'b1;
  assign hw2reg.sni_data1.de              = 1'b1;
  assign hw2reg.sni_data2.de              = 1'b1;
  assign hw2reg.sni_data3.de              = 1'b1;
  assign hw2reg.sni_data0.d               = sni_data0;
  assign hw2reg.sni_data1.d               = sni_data1;
  assign hw2reg.sni_data2.d               = sni_data2;
  assign hw2reg.sni_data3.d               = sni_data3;

  // CHANGED: no continuous assign to frame_read.de here anymore
  // (drive it procedurally when we need to auto-clear)
  // assign hw2reg.sni_ctrl.frame_read.de    = frame_read_ack;
  // assign frame_read_sw                    = reg2hw.sni_ctrl.frame_read;
  // assign frame_read_sw_q                  = reg2hw.sni_ctrl.frame_read.q;


  assign rst_fifo_reg                     = reg2hw.sni_ctrl.rst_fifo;
  assign enable_gating_reg                = reg2hw.sni_ctrl.enable_gating;

  // SW ack rising-edge detection
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      frame_read_q_d <= 1'b0;
    end else begin
      frame_read_q_d <= reg2hw.sni_ctrl.frame_read.q;
    end
  end
  assign frame_read_rise = reg2hw.sni_ctrl.frame_read.q & ~frame_read_q_d;


  // // Auto-clear del campo SNI_CTRL.FRAME_READ
  // assign hw2reg.sni_ctrl.frame_read.de = frame_read_sw_rise;
  // assign hw2reg.sni_ctrl.frame_read.d  = 1'b0;

  // sticky "frame_available" flag
  // always_ff @(posedge clk_i or negedge rst_ni) begin
  //   if (!rst_ni) begin
  //     frame_pending <= 1'b0;
  //   end else begin
  //     // set when we pop a frame
  //     if (pop_fifo) frame_pending <= 1'b1;
  //     // clear when SW acks (rising edge of FRAME_READ)
  //     if (frame_read_rise) frame_pending <= 1'b0;
  //   end
  // end

  // sticky "frame_available" flag
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)                    frame_pending <= 1'b0;
    else if (rst_fifo)              frame_pending <= 1'b0;       // pulisci anche al reset FIFO
    else if (pop_fifo)              frame_pending <= 1'b1;       // nuovo frame esposto a sni_data*
    else if (frame_read_rise)    frame_pending <= 1'b0;       // SW ha letto/ackato
  end
  //--------------------------------------------------------------------------
  // timestamp; only use the lower 16 bits
  //--------------------------------------------------------------------------
  logic [31:0] timestamp_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) timestamp_q <= 32'd0;
    else timestamp_q <= timestamp_q + 32'd1;
  end

  //--------------------------------------------------------------------------
  // FIFO Instantiation
  //--------------------------------------------------------------------------
  // FIFO control signals:
  logic push_fifo;
  logic pop_fifo;
  logic [FRAME_WIDTH-1:0] fifo_data_in;
  logic [FRAME_WIDTH-1:0] fifo_data_out;
  logic full, empty;

  //Whenever the rst_fifo register is written by the user or the system exits from debug mode, change the value of rst_fifo_reg_ff
  //rst_fifo_reg_ff is used for sampling the debug mode together with the reset.
  //This prevent the fifo to fill during debug mode, that will prevent the sigtrap to happen.

  always_ff @(posedge rst_fifo_reg or negedge debug_mode_i) begin
    if (rst_fifo_reg && debug_mode_i) rst_fifo_reg_ff <= rst_fifo_reg;
    else if (!debug_mode_i) rst_fifo_reg_ff <= 0;

  end
  // Reset logic
  assign rst_fifo = (debug_mode_i) ? (rst_fifo_reg_ff || !rst_ni) : rst_fifo_reg || !rst_ni;



  fifo_bus_sniffer #(
      .DATA_WIDTH(FRAME_WIDTH),
      .DEPTH(FIFO_DEPTH)
  ) fifo_bus_sniffer_inst (
      .clk(clk_i),
      .rst_ni(~rst_fifo),
      .wr_en(push_fifo),
      .rd_en(pop_fifo),
      .data_in(fifo_data_in),
      .data_out(fifo_data_out),
      .full(full),
      .empty(empty),
      .count()
  );

  // ---------------------------------------------------------------------------
  // find_free_slot()
  // Returns the index of the first entry that is not free_slot, or -1 if none.
  // ---------------------------------------------------------------------------
  function automatic int find_free_slot(input partial_entry_t table_tmp[TABLE_DEPTH]);
    for (int i = 0; i < TABLE_DEPTH; i++) begin
      if (!table_tmp[i].free_slot) begin
        return i;
      end
    end
    return -1;  // No free slot
  endfunction

  // Partial entry table
  partial_entry_t transaction_table[TABLE_DEPTH];

  //  On each clock, capture new requests from each channel
  //     if req && gnt are high, store them in a free table entry.
  //     If it's a write, mark the frame as "complete" immediately.
  //     If it's a read, mark waiting_resp=1.

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      // init table by freeing all the elements
      for (int i = 0; i < TABLE_DEPTH; i++) begin
        transaction_table[i].free_slot <= 1'b0;
      end

    end else begin
      // Temp copy of the table to compute next state of all the channels simultaneously
      partial_entry_t table_tmp[TABLE_DEPTH];

      // Copy the elements occupied
      for (int i = 0; i < TABLE_DEPTH; i++) begin
        table_tmp[i] = transaction_table[i];
      end

      //---------------------------------------------------------------
      // Check all channels in priority order
      //---------------------------------------------------------------

      // Iterate through channels
      for (int ch = 0; ch < NUM_CHANNELS; ch++) begin
        logic req, gnt, we;
        logic [31:0] addr, wdata;
        logic [3:0] be;
        logic [3:0] channel_id;

        // Get signals for current channel
        case (ch)
          CH_CORE_INSTR: begin
            req = bus_sniffer_bundle_i.core_instr_req.req;
            gnt = bus_sniffer_bundle_i.core_instr_resp.gnt;
            we = bus_sniffer_bundle_i.core_instr_req.we;
            addr = bus_sniffer_bundle_i.core_instr_req.addr;
            wdata = bus_sniffer_bundle_i.core_instr_req.wdata;
            be = bus_sniffer_bundle_i.core_instr_req.be;
            channel_id = CORE_INSTR;
          end
          CH_CORE_DATA: begin
            req = bus_sniffer_bundle_i.core_data_req.req;
            gnt = bus_sniffer_bundle_i.core_data_resp.gnt;
            we = bus_sniffer_bundle_i.core_data_req.we;
            addr = bus_sniffer_bundle_i.core_data_req.addr;
            wdata = bus_sniffer_bundle_i.core_data_req.wdata;
            be = bus_sniffer_bundle_i.core_data_req.be;
            channel_id = CORE_DATA;
          end
          CH_AO_PERIPH: begin
            req = bus_sniffer_bundle_i.ao_peripheral_slave_req.req;
            gnt = bus_sniffer_bundle_i.ao_peripheral_slave_resp.gnt;
            we = bus_sniffer_bundle_i.ao_peripheral_slave_req.we;
            addr = bus_sniffer_bundle_i.ao_peripheral_slave_req.addr;
            wdata = bus_sniffer_bundle_i.ao_peripheral_slave_req.wdata;
            be = bus_sniffer_bundle_i.ao_peripheral_slave_req.be;
            channel_id = AO_PERIPH;
          end
          CH_PERIPH: begin
            req = bus_sniffer_bundle_i.peripheral_slave_req.req;
            gnt = bus_sniffer_bundle_i.peripheral_slave_resp.gnt;
            we = bus_sniffer_bundle_i.peripheral_slave_req.we;
            addr = bus_sniffer_bundle_i.peripheral_slave_req.addr;
            wdata = bus_sniffer_bundle_i.peripheral_slave_req.wdata;
            be = bus_sniffer_bundle_i.peripheral_slave_req.be;
            channel_id = PERIPH;
          end
          CH_RAM0: begin
            req = bus_sniffer_bundle_i.ram_slave_req[0].req;
            gnt = bus_sniffer_bundle_i.ram_slave_resp[0].gnt;
            we = bus_sniffer_bundle_i.ram_slave_req[0].we;
            addr = bus_sniffer_bundle_i.ram_slave_req[0].addr;
            wdata = bus_sniffer_bundle_i.ram_slave_req[0].wdata;
            be = bus_sniffer_bundle_i.ram_slave_req[0].be;
            channel_id = RAM0;
          end
          CH_RAM1: begin
            req = bus_sniffer_bundle_i.ram_slave_req[1].req;
            gnt = bus_sniffer_bundle_i.ram_slave_resp[1].gnt;
            we = bus_sniffer_bundle_i.ram_slave_req[1].we;
            addr = bus_sniffer_bundle_i.ram_slave_req[1].addr;
            wdata = bus_sniffer_bundle_i.ram_slave_req[1].wdata;
            be = bus_sniffer_bundle_i.ram_slave_req[1].be;
            channel_id = RAM1;
          end
          CH_FLASH: begin
            req = bus_sniffer_bundle_i.flash_mem_slave_req.req;
            gnt = bus_sniffer_bundle_i.flash_mem_slave_resp.gnt;
            we = bus_sniffer_bundle_i.flash_mem_slave_req.we;
            addr = bus_sniffer_bundle_i.flash_mem_slave_req.addr;
            wdata = bus_sniffer_bundle_i.flash_mem_slave_req.wdata;
            be = bus_sniffer_bundle_i.flash_mem_slave_req.be;
            channel_id = FLASH;
          end
          CH_DMA_READ: begin
            req = bus_sniffer_bundle_i.dma_read_req.req;
            gnt = bus_sniffer_bundle_i.dma_read_resp.gnt;
            we = bus_sniffer_bundle_i.dma_read_req.we;
            addr = bus_sniffer_bundle_i.dma_read_req.addr;
            wdata = bus_sniffer_bundle_i.dma_read_req.wdata;
            be = bus_sniffer_bundle_i.dma_read_req.be;
            channel_id = DMA_READ;
          end
          CH_DMA_WRITE: begin
            req = bus_sniffer_bundle_i.dma_write_req.req;
            gnt = bus_sniffer_bundle_i.dma_write_resp.gnt;
            we = bus_sniffer_bundle_i.dma_write_req.we;
            addr = bus_sniffer_bundle_i.dma_write_req.addr;
            wdata = bus_sniffer_bundle_i.dma_write_req.wdata;
            be = bus_sniffer_bundle_i.dma_write_req.be;
            channel_id = DMA_WRITE;
          end
          CH_DMA_ADDR: begin
            req = bus_sniffer_bundle_i.dma_addr_req.req;
            gnt = bus_sniffer_bundle_i.dma_addr_resp.gnt;
            we = bus_sniffer_bundle_i.dma_addr_req.we;
            addr = bus_sniffer_bundle_i.dma_addr_req.addr;
            wdata = bus_sniffer_bundle_i.dma_addr_req.wdata;
            be = bus_sniffer_bundle_i.dma_addr_req.be;
            channel_id = DMA_ADDR;
          end
        endcase

        // If channel request is active and granted, allocate a slot in the table
        if (req && gnt) begin
          int idx = find_free_slot(table_tmp);  // Use temp table
          if (idx >= 0) begin
            // Update temp table (not actual table yet)
            table_tmp[idx].free_slot           = 1'b1;
            table_tmp[idx].waiting_resp        = ~we;
            table_tmp[idx].channel_id          = channel_id;
            table_tmp[idx].frame.source_id     = channel_id;
            table_tmp[idx].frame.req_timestamp = timestamp_q;
            table_tmp[idx].frame.address       = addr;
            table_tmp[idx].frame.byte_enable   = be;
            table_tmp[idx].frame.we            = we;
            table_tmp[idx].frame.valid         = 1'b1;
            table_tmp[idx].frame.gnt           = 1'b1;  // store handshake

            if (we) begin
              // For writes, the data is known right now
              table_tmp[idx].frame.data           = wdata;
              table_tmp[idx].frame.resp_timestamp = 16'h0001;
            end else begin
              // For reads, data & resp_timestamp come later
              table_tmp[idx].frame.data           = '0;
              table_tmp[idx].frame.resp_timestamp = '0;
            end
          end
        end
      end


      for (int i = 0; i < TABLE_DEPTH; i++) begin
        transaction_table[i] <= table_tmp[i];
      end
    end
  end

  // On each clock, capture any responses. If we see rvalid for a channel,
  // we find the matching table entry that is waiting_resp=1 for that channel,
  // fill in the read data, set resp_timestamp, and mark it "complete".
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      // no operation
    end else begin
      //CORE DATA
      logic [31:0] diff;

      if (bus_sniffer_bundle_i.core_data_resp.rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == CORE_DATA) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.core_data_resp.rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;
          end
        end
      end
      // CORE_INSTR
      if (bus_sniffer_bundle_i.core_instr_resp.rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == CORE_INSTR) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.core_instr_resp.rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;
          end
        end
      end
      // AO_PERIPH
      if (bus_sniffer_bundle_i.ao_peripheral_slave_resp.rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == AO_PERIPH) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.ao_peripheral_slave_resp.rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;  // no longer waiting
          end
        end
      end
      // PERIPH
      if (bus_sniffer_bundle_i.peripheral_slave_resp.rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == PERIPH) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.peripheral_slave_resp.rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;  // no longer waiting
          end
        end
      end
      // RAM0
      if (bus_sniffer_bundle_i.ram_slave_resp[0].rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == RAM0) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.ram_slave_resp[0].rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;  // no longer waiting
          end
        end
      end
      // RAM1
      if (bus_sniffer_bundle_i.ram_slave_resp[1].rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == RAM1) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.ram_slave_resp[1].rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;  // no longer waiting
          end
        end
      end
      // FLASH
      if (bus_sniffer_bundle_i.flash_mem_slave_resp.rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == FLASH) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.flash_mem_slave_resp.rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;  // no longer waiting
          end
        end
      end
      // DMA
      if (bus_sniffer_bundle_i.dma_read_resp.rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == DMA_READ) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.dma_read_resp.rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;  // no longer waiting
          end
        end
      end
      // DMA
      if (bus_sniffer_bundle_i.dma_write_resp.rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == DMA_WRITE) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.dma_write_resp.rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;  // no longer waiting
          end
        end
      end
      // DMA
      if (bus_sniffer_bundle_i.dma_addr_resp.rvalid) begin
        for (int i = 0; i < TABLE_DEPTH; i++) begin
          if (transaction_table[i].free_slot && 
                        transaction_table[i].waiting_resp && 
                        transaction_table[i].channel_id == DMA_ADDR) begin

            diff <= timestamp_q - transaction_table[i].frame.req_timestamp;
            transaction_table[i].frame.data <= bus_sniffer_bundle_i.dma_addr_resp.rdata;
            transaction_table[i].frame.resp_timestamp <= diff[15:0];
            transaction_table[i].waiting_resp <= 1'b0;  // no longer waiting
          end
        end
      end
    end
  end


  logic [31:0] push_idx;  // we’ll store -1 if none found
  always_comb begin
    push_idx = -1;  // default: no entry found
    // Simple priority: pick the first valid & !waiting entry
    for (int i = 0; i < TABLE_DEPTH; i++) begin
      if (transaction_table[i].free_slot && !transaction_table[i].waiting_resp) begin
        push_idx = i;
        break;
      end
    end
  end

  //------------------------------------------------------------------------ 
  // FIFO Push logic
  //------------------------------------------------------------------------ 

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      push_fifo    <= 1'b0;
      fifo_data_in <= '0;
    end else begin
      // By default, no push this cycle
      push_fifo <= 1'b0;

      // If we found a complete entry AND FIFO not full AND not in debug, push it
      if (push_idx != -1 && !full  && capture_en) begin
        push_fifo    <= 1'b1;  // 1-cycle pulse
        fifo_data_in <= transaction_table[push_idx].frame;
        // Mark that entry as free
        transaction_table[push_idx].free_slot <= 1'b0;
      end
    end
  end


  // ---------------------------------------------------------------------------
  // FIFO pop logic
  // ---------------------------------------------------------------------------

  // Sample the negedge of the run_enable of the clock gating, to do the first pop
  logic run_enable_q;
  wire initial_pop = run_enable_q & ~run_enable;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      run_enable_q <= 1'b1;
    end else begin
      run_enable_q <= run_enable;  // sample last cycle’s run_enable
    end
  end


  // always_ff @(posedge clk_i or negedge rst_ni) begin
  //   if (!rst_ni) begin
  //     pop_fifo <= 1'b0;
  //   end else begin
  //     pop_fifo <= 1'b0;  // default: no pop

  //     // 1) FIFO just became full → generate first pop
  //     // if (/*halt_pulse*/!run_enable) begin
  //     //   pop_fifo <= 1'b1;

  //     if (  /*halt_pulse*/ initial_pop  /**/) begin
  //       pop_fifo <= 1'b1;

  //       // 2) SW read-ack while frames remain
  //     end else if (frame_read_sw && !empty) begin
  //       pop_fifo <= 1'b1;
  //     end
  //   end
  // end


always_ff @(posedge clk_i or negedge rst_ni) begin
  if (!rst_ni) begin
    pop_fifo                 <= 1'b0;
    frame_read_autoclr_pulse <= 1'b0;
  end else begin
    pop_fifo                 <= 1'b0;  // default
    frame_read_autoclr_pulse <= 1'b0;  // default

    // first pop when clock gets gated
    if (initial_pop && !empty) begin
      pop_fifo <= 1'b1;

    // SW ack rising edge -> pop exactly one and auto-clear FRAME_READ
    end else if (frame_read_rise && !empty) begin
      pop_fifo                 <= 1'b1;
      frame_read_autoclr_pulse <= 1'b1; // drives hw2reg via continuous assign
    end
  end
end

  // ---------------------------------------------------------------------------
  // FIFO-full edge detector
  // ---------------------------------------------------------------------------
  logic full_q;  // full flag one cycle ago
  logic halt_pulse;  // 1-clk pulse that replaces former halt_state

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      full_q <= 1'b0;
    end else begin
      full_q <= full;  // remember previous level
    end
  end

  assign halt_pulse = (full & ~full_q) && run_enable;  // rising edge of "full"
  // assign halt_state_o = halt_pulse;    // this now goes to debug_req


  // ---------------------------------------------------------------------------
  // Debug req
  // ---------------------------------------------------------------------------
  logic debug_req_trigger;
  logic [2:0] debug_req_counter;  // Counter per mantenere il segnale


  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      debug_req_counter <= 3'b0;
      debug_req_trigger <= 1'b0;
    end else begin
      if ((full && halt_pulse) && debug_req_counter == 3'b0) begin
        // Inizia la sequenza di debug request
        debug_req_counter <= 3'b1;
        debug_req_trigger <= 1'b1;
      end else if (debug_req_counter > 3'b0 && debug_req_counter < 3'b110) begin
        // Mantieni alto per alcuni cicli (almeno 1, meglio 3-4)
        debug_req_counter <= debug_req_counter + 1'b1;
        debug_req_trigger <= 1'b1;
      end else begin
        debug_req_trigger <= 1'b0;
        if (debug_req_counter == 3'b110) debug_req_counter <= 3'b0;  // Reset per prossimo trigger
      end
    end
  end

  // Collegamento al segnale debug_req del core
  assign halt_state_o = debug_req_trigger;


  // ------------------------------------------------------------------
  // Software‐enable bit from control register
  // ------------------------------------------------------------------

  logic gated_active;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) gated_active <= 1'b0;
    else if (enable_gating_reg) gated_active <= 1'b1;
    else gated_active <= 1'b0;
  end


  // ------------------------------------------------------------------
  // Countdown then gate core clock
  // ------------------------------------------------------------------
  parameter int HALT_REQ_CYCLES = 15;
  logic [$clog2(HALT_REQ_CYCLES+1)-1:0] halt_req_cnt;
  logic run_enable;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      halt_req_cnt <= 0;
      run_enable   <= 1;
    end else if (!gated_active) begin
      // before gating is enabled, always leave CPU running
      halt_req_cnt <= 0;
      run_enable   <= 1;
    end else if (halt_pulse) begin
      // we detected FIFO-full → give the CPU N more cycles, then stop
      /* verilator lint_off WIDTH */
      halt_req_cnt <= HALT_REQ_CYCLES;
      run_enable   <= 1;
    end else if (halt_req_cnt != 0) begin
      // counting down
      halt_req_cnt <= halt_req_cnt - 1;
      if (halt_req_cnt == 1) run_enable <= 0;
    end else if (empty) begin
      // once FIFO is empty, immediately re-open clock
      run_enable <= 1;
    end
    // otherwise hold the previous run_enable
  end

  assign clk_gate_o = run_enable;


  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      sni_data0 <= 32'd0;
      sni_data1 <= 32'd0;
      sni_data2 <= 32'd0;
      sni_data3 <= 32'd0;
    end else if (rst_fifo) begin
      sni_data0 <= 32'd0;
      sni_data1 <= 32'd0;
      sni_data2 <= 32'd0;
      sni_data3 <= 32'd0;
    end else if (pop_fifo) begin
      sni_data0 <= fifo_data_out[127:96];
      sni_data1 <= fifo_data_out[95:64];
      sni_data2 <= fifo_data_out[63:32];
      sni_data3 <= fifo_data_out[31:0];
    end
  end



  bus_sniffer_reg_top #(
      .reg_req_t(reg_req_t),
      .reg_rsp_t(reg_rsp_t),
  ) bus_sniffer_reg_top_i (
      .clk_i,
      .rst_ni,
      .reg_req_i,
      .reg_rsp_o,
      .reg2hw,
      .hw2reg,
      .devmode_i(1'b1)
  );

endmodule

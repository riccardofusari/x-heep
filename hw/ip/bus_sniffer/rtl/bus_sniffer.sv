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


    output logic                sniffer_tdo_o,
    input  bus_sniffer_bundle_t bus_sniffer_bundle_i
);


  // Interface signals
  // logic [1:0] sni_ctrl;
  logic [31:0] sni_data0;
  logic [31:0] sni_data1;
  logic [31:0] sni_data2;
  logic [31:0] sni_data3;

  /* verilator lint_off UNUSED */
  bus_sniffer_reg2hw_t reg2hw;
  bus_sniffer_hw2reg_t hw2reg;


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
  // logic [$clog2(FIFO_DEPTH):0] fifo_count;

  fifo_bus_sniffer #(
      .DATA_WIDTH(FRAME_WIDTH),
      .DEPTH(FIFO_DEPTH)
  ) fifo_bus_sniffer_inst (
      .clk(clk_i),
      .rst_ni(rst_ni),
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
      // List of channels to check (priority order)


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
  // Push logic
  //------------------------------------------------------------------------ 

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      push_fifo    <= 1'b0;
      fifo_data_in <= '0;
    end else begin
      // By default, no push this cycle
      push_fifo <= 1'b0;

      // If we found a complete entry AND FIFO not full, push it
      if (push_idx != -1 && !full) begin
        push_fifo    <= 1'b1;  // 1-cycle pulse
        fifo_data_in <= transaction_table[push_idx].frame;
        // Mark that entry as free
        transaction_table[push_idx].free_slot <= 1'b0;
      end
    end
  end

  //------------------------------------------------------------------------ 
  // Pop logic
  //------------------------------------------------------------------------ 

  logic pop_condition, pop_condition_d;
  assign pop_condition = (!shifting && !empty);

  // Delay the condition by one cycle.
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) pop_condition_d <= 1'b0;
    else pop_condition_d <= pop_condition;
  end

  // pop_fifo is high only when pop_condition rises (i.e., it is true now and was false in the previous cycle)
  assign pop_fifo = pop_condition && !pop_condition_d;


  //--------------------------------------------------------------------------
  // Shift-Out Logic:
  // Load a frame from the FIFO into a shift register and shift it out LSB-first.
  //--------------------------------------------------------------------------

  int   shift_count;
  logic shifting;
  typedef logic [FRAME_WIDTH-1:0] shift_reg_t;
  shift_reg_t shift_reg;

  function automatic shift_reg_t frame_to_bits(bus_sniffer_frame_t f);
    shift_reg_t bits;
    bits = {
      f.source_id,  // [127:124]
      f.req_timestamp,  // [123:108]
      f.resp_timestamp,  // [107:92]
      f.address,  // [91:60]
      f.data,  // [59:28]
      f.byte_enable,  // [27:24]
      f.we,  // [23]
      f.valid,  // [22]
      f.gnt,  // [21]
      f.reserved  // [20:0]
    };
    return bits;
  endfunction


  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      shift_reg   <= '0;
      shift_count <= 0;
      shifting    <= 1'b0;
    end else begin
      if (pop_fifo) begin
        shift_reg   <= frame_to_bits(fifo_data_out);
        shift_count <= 0;
        shifting    <= 1'b1;
      end else if (shifting) begin
        if (shift_count == (FRAME_WIDTH - 1)) shifting <= 1'b0;
        shift_count <= shift_count + 1;
      end
    end
  end

  wire shift_bit = shift_reg[shift_count];
  assign sniffer_tdo_o = shifting ? shift_bit : 1'b0;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
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

  assign hw2reg.sni_status.empty.de       = 1'b1;
  assign hw2reg.sni_status.full.de        = 1'b1;
  assign hw2reg.sni_status.frame_avail.de = 1'b1;
  assign hw2reg.sni_status.empty.d        = empty;
  assign hw2reg.sni_status.full.d         = full;
  assign hw2reg.sni_status.frame_avail.d  = pop_fifo;

  assign hw2reg.sni_data0.de              = 1'b1;
  assign hw2reg.sni_data1.de              = 1'b1;
  assign hw2reg.sni_data2.de              = 1'b1;
  assign hw2reg.sni_data3.de              = 1'b1;
  assign hw2reg.sni_data0.d               = sni_data0;
  assign hw2reg.sni_data1.d               = sni_data1;
  assign hw2reg.sni_data2.d               = sni_data2;
  assign hw2reg.sni_data3.d               = sni_data3;




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

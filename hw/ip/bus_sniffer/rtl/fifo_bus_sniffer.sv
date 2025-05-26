//------------------------------------------------------------------------------
// Module: optimized_fifo
// Description: Parameterized synchronous FIFO for 128-bit words optimized for 
//              Vivado on a Pynq-Z2 FPGA. This design uses inferred block RAM.
//------------------------------------------------------------------------------
module fifo_bus_sniffer #(
    parameter int DATA_WIDTH = 128,           // Width of each FIFO word
    parameter int DEPTH      = 32,            // FIFO depth (number of entries)
    parameter int ADDR_WIDTH = $clog2(DEPTH)
) (
    input  logic                  clk,
    input  logic                  rst_ni,    // synchronous reset
    input  logic                  wr_en,     // push enable
    input  logic                  rd_en,     // pop enable
    input  logic [DATA_WIDTH-1:0] data_in,   // write data
    output logic [DATA_WIDTH-1:0] data_out,  // read data
    output logic                  full,      // FIFO full flag
    output logic                  empty,     // FIFO empty flag
    output logic [  ADDR_WIDTH:0] count      // Number of words stored
);

  //-------------------------------------------------------------------------
  // Memory Array:
  // Use the ram_style attribute for synthesis to force block RAM inference.
  // For simulation with Verilator you can disable it if needed.
  //-------------------------------------------------------------------------
  // `ifdef SYNTHESIS

  // `endif
  (* ram_style = "block" *) logic [DATA_WIDTH-1:0] mem[0:DEPTH-1];

  //-------------------------------------------------------------------------
  // FIFO pointers and counter.
  //-------------------------------------------------------------------------
  logic [ADDR_WIDTH-1:0] wr_ptr, rd_ptr;
  // We use an extra bit for the counter to represent the range [0, DEPTH].
  logic [ADDR_WIDTH:0] count_reg;
  int count_int;
  int i;

  //-------------------------------------------------------------------------
  // Status signals.
  //-------------------------------------------------------------------------
  assign full = (count_int == DEPTH);
  assign empty = (count_int == 0);
  assign count = count_reg;
  assign data_out = mem[rd_ptr];
  //-------------------------------------------------------------------------
  // FIFO operation:
  // Synchronous FIFO that handles push, pop, and simultaneous operations.
  //-------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_ni) begin
    if (!rst_ni) begin
      wr_ptr    <= '0;
      rd_ptr    <= '0;
      count_reg <= '0;
      count_int <= 0;
      /* verilator lint_off BLKSEQ */
      for (i = 0; i < DEPTH; i++) begin
        mem[i] = '0;
      end

    end else begin
      case ({
        (wr_en && !full), (rd_en && !empty)
      })
        2'b10: begin
          // Write only.
          mem[wr_ptr] <= data_in;
          wr_ptr      <= wr_ptr + 1;
          count_reg   <= count_reg + 1;
          count_int   <= count_int + 1;

        end
        2'b01: begin
          // Read only.
          rd_ptr    <= rd_ptr + 1;
          count_reg <= count_reg - 1;
          count_int <= count_int - 1;

        end
        2'b11: begin
          // Simultaneous push and pop.
          mem[wr_ptr] <= data_in;
          wr_ptr      <= wr_ptr + 1;
          rd_ptr      <= rd_ptr + 1;
          // count_reg remains unchanged.
        end
        default: begin
          // No operation.
        end
      endcase
    end
  end

endmodule

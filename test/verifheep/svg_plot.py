#!/usr/bin/env python3
import svgwrite

# ----- Configuration Parameters -----
TOTAL_CYCLES = 30         # Total clock cycles in the diagram
SCALE = 20                # Pixels per clock cycle horizontally
LABEL_X_OFFSET = 80       # Horizontal offset for the waveform area (to leave space for labels)
SVG_WIDTH = LABEL_X_OFFSET + TOTAL_CYCLES * SCALE + 20
SVG_HEIGHT = 350          # Increased height to accommodate extra signals

# Signal timing for digital signals (in clock cycles)
clock_y = 30         # Top of the clock waveform
req_y   = 70         # Top of REQ waveform
gnt_y   = 110        # Top of GNT waveform
we_y    = 150        # Top of WE/BE waveform
signal_height = 20   # Digital signal height (difference between high and low levels)

# ----- Multi-bit Signal Data (example segments) -----
# Each segment is defined as (start_cycle, end_cycle, value_label)
# These values you would extract from your transaction.
core_instr_segments = [
    (0, 5, "NOP"),
    (5, 10, "ADD")
]

address_segments = [
    (0, 3, "0x00000000"),
    (3, 8, "0x12345678"),
    (8, 10, "0x12345678")
]

data_segments = [
    (0, 6, "0x00000000"),
    (6, 10, "0xDEADBEEF")
]

# Vertical positions (y offsets) for multi-bit signals
core_instr_y = 190
address_y    = 230
data_y       = 270
multibit_height = 25  # Height for multi-bit signal boxes

# ----- Helper Functions for Drawing Digital Waveforms -----
def y_for_level(y_offset, level, height):
    """Returns the y coordinate for the given level (1=high, 0=low)."""
    return y_offset if level == 1 else y_offset + height

def draw_waveform(dwg, signal_name, transitions, x_offset, y_offset, scale, height):
    """
    Draw a stepped digital waveform.
    
    transitions: list of (cycle, level) tuples.
    """
    group = dwg.g()
    # Label the signal on the left.
    group.add(dwg.text(signal_name, insert=(0, y_offset + height/2 + 5),
                        font_size="12px", fill="black"))
    points = []
    # Start at first transition:
    t0, l0 = transitions[0]
    points.append((x_offset + t0 * scale, y_for_level(y_offset, l0, height)))
    for (t, level) in transitions[1:]:
        points.append((x_offset + t * scale, y_for_level(y_offset, l0, height)))
        points.append((x_offset + t * scale, y_for_level(y_offset, level, height)))
        l0 = level
    polyline = dwg.polyline(points=points, stroke="blue", stroke_width=2, fill="none")
    group.add(polyline)
    dwg.add(group)

def draw_time_axis(dwg, total_cycles, x_offset, y_position, scale):
    """Draws vertical grid lines and cycle numbers."""
    for cycle in range(total_cycles + 1):
        x = x_offset + cycle * scale
        dwg.add(dwg.line(start=(x, y_position), end=(x, y_position + 10), stroke="gray", stroke_width=1))
        dwg.add(dwg.text(str(cycle), insert=(x - 3, y_position + 25), font_size="10px", fill="gray"))

# ----- Helper Functions for Drawing Multi-Bit Signals -----
def draw_multibit_signal(dwg, signal_name, segments, x_offset, y_offset, scale, height):
    """
    Draw a multi-bit signal as a series of rectangular segments.
    
    segments: list of tuples (start_cycle, end_cycle, value_label).
    Each segment is drawn as a rectangle spanning from start_cycle to end_cycle (converted to pixels)
    and labeled with value_label.
    """
    group = dwg.g()
    # Draw signal name on the left.
    group.add(dwg.text(signal_name, insert=(0, y_offset + height/2 + 5),
                        font_size="12px", fill="black"))
    for (start, end, label) in segments:
        x_start = x_offset + start * scale
        width = (end - start) * scale
        rect = dwg.rect(insert=(x_start, y_offset), size=(width, height),
                        fill="white", stroke="black", stroke_width=1)
        group.add(rect)
        # Center the label in the rectangle.
        mid_x = x_start + width/2
        mid_y = y_offset + height/2 + 4
        group.add(dwg.text(label, insert=(mid_x, mid_y),
                           text_anchor="middle", font_size="12px", fill="black"))
    dwg.add(group)

# ----- Main Function to Create the SVG -----
def main():
    dwg = svgwrite.Drawing("timing_diagram.svg", size=(SVG_WIDTH, SVG_HEIGHT))

    # Draw vertical cycle grid lines
    draw_time_axis(dwg, TOTAL_CYCLES, LABEL_X_OFFSET, 20, SCALE)

    # Draw digital signals:
    # Clock: Simple square wave (even cycles high, odd cycles low)
    clock_transitions = [(cycle, 1 if cycle % 2 == 0 else 0) for cycle in range(TOTAL_CYCLES + 1)]
    draw_waveform(dwg, "CLK", clock_transitions, LABEL_X_OFFSET, clock_y, SCALE, signal_height)
    
    # REQ signal: low until cycle 10, high from 10 to 15, then low.
    req_transitions = [
        (0, 0),
        (10, 0),
        (10, 1),
        (15, 1),
        (15, 0),
        (TOTAL_CYCLES, 0)
    ]
    draw_waveform(dwg, "REQ", req_transitions, LABEL_X_OFFSET, req_y, SCALE, signal_height)
    
    # GNT signal: low until cycle 15, high for one cycle, then low.
    gnt_transitions = [
        (0, 0),
        (15, 0),
        (15, 1),
        (16, 1),
        (16, 0),
        (TOTAL_CYCLES, 0)
    ]
    draw_waveform(dwg, "GNT", gnt_transitions, LABEL_X_OFFSET, gnt_y, SCALE, signal_height)
    
    # WE/BE signal: Same as REQ for simplicity.
    draw_waveform(dwg, "WE/BE", req_transitions, LABEL_X_OFFSET, we_y, SCALE, signal_height)
    
    # Draw multi-bit signals as additional rows.
    # CORE_INSTR signal
    draw_multibit_signal(dwg, "CORE_INSTR", core_instr_segments, LABEL_X_OFFSET, core_instr_y, SCALE, multibit_height)
    
    # ADDRESS signal
    draw_multibit_signal(dwg, "ADDRESS", address_segments, LABEL_X_OFFSET, address_y, SCALE, multibit_height)
    
    # DATA signal
    draw_multibit_signal(dwg, "DATA", data_segments, LABEL_X_OFFSET, data_y, SCALE, multibit_height)
    
    # Save the SVG output.
    dwg.save()
    print("SVG timing diagram saved as timing_diagram.svg")

if __name__ == "__main__":
    main()

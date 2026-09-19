"""RackTicker 2U bezel, revision 10 - master parameter table.  RELEASE.

V10 over V9:
  * minimum wall 3.0 mm (~7 lines) on every layer of every part
  * Pi dovetail rails 16-22 x 7 mm (were 12-16 x 5), 14 mm long, 4 mm roof
  * jigsaw key centred on the full node: socket walls 4.85 mm (were 3.15)
  * cradle stands clear of the seam fin along its whole slide-in path
  * validation simulates assembly paths and checks hex-key access to every screw

--- V9 notes follow ---

V9 over V8 (review at printed scale, 2026-09-18):
  * the V8 jigsaw socket left a 1.1 mm wall - about two extrusion lines - and
    the end-part key was fed through a 6.5 mm strip beside a window.  The joint
    is now a NODE: both plates thicken from 5 to 12 mm with 45 deg ramps, the
    plate extends down to the connector keep-out line, the key is 12 mm deep
    with >= 3.4 mm walls and bevelled corners, and a cap holds the faces flush.
  * automatic minimum-wall scan of every layer of every printed part.

--- V8 notes follow ---

V8 over V7 (release polish, 2026-09-18):
  * LEDs 1.30 mm deeper than the printed V5 (chosen from the real print)
  * rack mounting on the four corners only, like commercial 2U gear
  * 0.6 mm chamfer round the outer front perimeter - hides elephant's foot and
    reads as a finished product
  * the two part splits get a 0.5 mm V-groove so they read as designed lines
  * two cable-tie slots through the seam fin for the panel power leads
  * 20 mm of room in front of the Pi for anything that plugs into that edge

--- V7 notes follow ---

V7 over V6 (review before printing, 2026-09-18):
  * jigsaw dovetail keys lock the three bezel parts into one rigid chain, so the
    butt joints cannot open whatever the prints shrink; every part keeps slots
    and the whole bezel floats as one while the panel seam is closed
  * panel 0.75 mm deeper than the printed V5, aiming for FLUSH LEDs (they should
    not be recessed).  On V5 at 1.50 inset they stood proud "maybe 1 mm
    or less", so 0.75 centres on flush with at most ~0.25 either way
  * cradle tie tabs 3 mm with root fillets, full-length beams, taller beams
  * matte PLA

--- V6 notes follow ---

V6 changes, from the first full V5 build (2026-09-18):
  * one end part, printed twice - it is symmetric top/bottom, so the left side
    is the same print turned upside down.  No engraving.
  * round panel holes on the end part; the 1 mm butt gap came from slot slop.
  * Pi cradle hangs on a printed dovetail - no screw into plastic.  Open on all
    four edges for power, HDMI, micro-SD and USB.  Tie slots on opposite sides.
  * rear gusset ribs, rounded windows, a seam fin that bears on the panels,
    flared junctions - stiffness from geometry, not mass.
  * panel 1.00 mm deeper so the LEDs sit just behind the bezel face.
  * 8 x M3x10 total.

--- V5 notes follow ---

V5 goals over V4, from the build review:
  * ONE fastener type in the whole kit: M3 x 10.  No washers, no nuts, no
    inserts, no second screw length.
  * Three face parts instead of four plus a splice: the centre part spans the
    panel seam, so the seam joiner and the seam screws disappear.
  * No screws between printed parts at all.  The two panels tie the three face
    parts together; the rack rails tie the ends.
  * Roughly a third less plastic.
  * Pi 3A+ with its HAT mounted in the centre, USB facing the open rear.

Coordinate system (millimetres):
    X   0 at the left outside edge of the 19-inch face, +X right
    Y   0 at the bottom outside edge of the 2U face, +Y up
    Z   0 at the room-facing bezel front plane, +Z rearward into the rack
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Rack interface (MEASURED / proven by the mounted white prototype)
# ---------------------------------------------------------------------------
RACK_W = 482.60
RACK_H = 88.90
RACK_HOLE_DX = 8.75
RACK_HOLE_Y = (6.35, 82.55)      # four corners only
RACK_SLOT_W = 7.00          # capsule width  (X)
RACK_SLOT_H = 9.00          # capsule height (Y)
RAIL_PAD_T = 5.00           # proven thickness at the rail
RAIL_ZONE_X = 24.00         # nothing deeper than RAIL_PAD_T inboard of this

# ---------------------------------------------------------------------------
# LED panels (MEASURED, Waveshare RGB-Matrix-P2.5-64x32)
# ---------------------------------------------------------------------------
PANEL_W = 159.70
PANEL_H = 79.70
PANEL_D = 12.00
PAIR_W = 2 * PANEL_W
SEAM_X = RACK_W / 2                  # 241.30

PANEL_X0 = SEAM_X - PANEL_W          #  81.60
PANEL_X1 = SEAM_X + PANEL_W          # 401.00
PANEL_Y0 = (RACK_H - PANEL_H) / 2    #   4.60
PANEL_Y1 = (RACK_H + PANEL_H) / 2    #  84.30

SCREW_X = (98.95, 223.95, 258.65, 383.65)
SCREW_Y = (11.95, 76.95)

# Observed on the panel rear (photograph, 2026-09-17): the two HUB75 headers
# and the +5V/GND terminal all sit in the MIDDLE band of the panel rear, and
# the large frame recesses are on the Y centreline.  Nothing printed may sit
# flat on the panel rear between these limits.
PANEL_REAR_CLEAR_Y = (18.00, 70.00)   # keep this band of the panel rear free

# ---------------------------------------------------------------------------
# Screw stack - the only fastener in the kit
# ---------------------------------------------------------------------------
SCREW = "M3x10"
PANEL_INSERT_DEPTH = 5.00
BOSS_T = 5.00               # every screw-bearing boss is exactly 5.00

# ---------------------------------------------------------------------------
# Cosmetic front
# ---------------------------------------------------------------------------
AP_CLR = 0.40

# MEASURED on the printed coupon, 2026-09-17: the bezel front plane lands on the
# BASE of the LED packages, and the LEDs themselves stand proud of the panel's
# moulded frame face by about this much.  PANEL_D (12.00) is the frame only.
# Inferred from two prints: at 0.50 inset the bezel face landed on the LED
# base; at 1.50 the LED tips were at or just past the face.  So the LED tips
# stand ~1.50 proud of the moulded frame face.
LED_PROUD = 2.80          # 1.30 mm deeper than the V5 print at 1.50
LED_INSET = 0.00
PANEL_INSET = LED_PROUD + LED_INSET      # 2.80

AP_X0 = PANEL_X0 - AP_CLR            #  81.20
AP_X1 = PANEL_X1 + AP_CLR            # 401.40
AP_Y0 = PANEL_Y0 - AP_CLR            #   4.20
AP_Y1 = PANEL_Y1 + AP_CLR            #  84.70

PANEL_Z0 = PANEL_INSET               #  0.50
PANEL_Z1 = PANEL_Z0 + PANEL_D        # 12.50
BOSS_Z0 = PANEL_Z1                   # 12.50
BOSS_Z1 = BOSS_Z0 + BOSS_T           # 17.50

SKIN_T = 2.00               # cosmetic skin over the side wings
BAND_D = PANEL_INSET + PANEL_D + 1.00   # band depth, 1.00 past the panel rear
WING_RIB_D = 10.00          # stiffening rib depth behind the skin

# 5.00 mm structural rear plates
PLATE_TOP_Y0 = 72.00
PLATE_BOT_Y1 = 16.90
PLATE_X_INSET = 2.40        # plate starts this far inboard of the panel edge

# Panel screw clearance: one continuous horizontal capsule, sized so an M3
# socket-cap head (5.50 across) bears directly - no washer needed.
PANEL_SLOT_L = 5.00
PANEL_SLOT_W = 3.30

# Lightening windows through the rear plates
WINDOW_TOP_Y = (75.50, 84.00)
WINDOW_BOT_Y = (4.90, 13.40)

# ---------------------------------------------------------------------------
# Part split - three face parts, seams at the two panel midlines
# ---------------------------------------------------------------------------
SPLIT_L = PANEL_X0 + PANEL_W / 2     # 161.45, left panel midline
SPLIT_R = RACK_W - SPLIT_L           # 321.15, right panel midline
LAP_L = 8.00                         # alignment lap on each end part
LAP_Z0 = BOSS_Z1                     # 17.50, sits flat on the centre plate rear
LAP_ROOT_Z0 = BOSS_Z1 - 0.50         # 17.00, overlap into this part's own plate
LAP_Z1 = BOSS_Z1 + 4.00

# Centre part connectivity fin, behind the panel seam, clear of the panel rear
FIN_X = (238.30, 244.30)
FIN_Y = (12.50, RACK_H - 12.50)
FIN_Z = (BOSS_Z0, BOSS_Z1 + 8.00)     # bears on the panel rims at the seam

# ---------------------------------------------------------------------------
# Pi 3A+ / HUB75 HAT cradle
# ---------------------------------------------------------------------------
PI_W = 56.50                # across the rack, X
PI_L = 65.00                # front to back, Z - USB on the rear edge
PI_CLR = 0.60
PI_STACK_H = 26.00          # Pi + header + HAT + components, for clearance

# The cradle hangs on a printed peg at one end and takes ONE screw at the other.
# Total kit fastener count is therefore 9 x M3x10 - the eight panel screws plus
# this one.  Nothing else in the build is threaded.
CRADLE_BOSS_X = (176.00, 250.00)
CRADLE_SCREW_X = 176.00
CRADLE_PEG_X = 250.00
CRADLE_PEG_D = 5.00
CRADLE_PEG_CLR = 0.30
CRADLE_PEG_Z = (26.00, 28.80)
CRADLE_BOSS_Y = 7.00
CRADLE_BOSS_D = 9.00
CRADLE_BOSS_Z = (BOSS_Z1 - 0.50, 26.00)
CRADLE_PILOT_D = 2.50
CRADLE_PILOT_DEPTH = 7.50
CRADLE_CLEAR_D = 3.30
CRADLE_T = 3.00
CRADLE_FLANGE_Y = (2.00, 11.50)
CRADLE_FLOOR_T = 2.00

# ---------------------------------------------------------------------------
# Print / process
# ---------------------------------------------------------------------------
BED_X = 180.0
BED_Y = 180.0
BED_Z = 180.0
MATERIAL = "black PETG"
LAYER_H = 0.20
WALLS = 5

# ---------------------------------------------------------------------------
# V6 additions
# ---------------------------------------------------------------------------
END_HOLE_D = 3.50           # round clearance hole on the end part
WINDOW_R = 3.00             # corner radius on every lightening window
RIB_T = 3.20                # rear gusset rib thickness
RIB_H = 7.00                # rib height behind the plate rear face
FILLET_S = 1.50             # 45 deg fillet strips at rib / skin junctions
FIN_FLARE_W = 16.00         # fin widens to this where it meets each plate
FIN_FLARE_L = 10.00

# Pi dovetail: rail on face-centre's bottom plate, slot in the cradle flange
RAIL_X = 200.00
RAIL_W0 = 16.00             # at the plate rear face
RAIL_W1 = 22.00             # at the rail top
RAIL_H = 7.00
RAIL_Y = (1.50, 15.50)
DOVETAIL_CLR = 0.25

# jigsaw dovetail key, end plate -> centre plate, in the plate plane
KEY_NECK = 5.40
KEY_HEAD = 8.00
KEY_LEN = 6.00
KEY_BEVEL = 0.80          # 45 deg bevel on the head corners (no sharp notch)
KEY_CLR = 0.25            # PLA: pegs print fat, sockets print tight
NODE_Y0 = 70.90           # mirror image lands at Y 18.00: both halves clear the keep-out band
KEY_YC = (NODE_Y0 + RACK_H) / 2         # centred on the full 18.00 mm node, 79.90
NODE_T = 7.00             # extra thickness behind the plate: 5 + 7 = 12 mm
NODE_FLAT = 12.00         # full-thickness length each side of the split (>= 5.8 mm behind the socket)
NODE_RAMP = 7.00          # 45 deg ramp back down to the plate
CAP_T = 3.00              # end-part cap over the centre node, keeps faces flush
CAP_L = 9.00
MIN_WALL = 3.00           # printed-scale floor: ~7 lines of a 0.42 mm line width

MATERIAL = "matte PLA"
EDGE_CHAMFER = 0.60
SPLIT_GROOVE = 0.50
FIN_TIE_Y = (30.00, 58.90)            # symmetric about the 2U centreline
FIN_TIE_SLOT = (5.20, 3.00)           # Y x Z

AP_WALL_T = 3.20          # side wall of the screen opening
RIB_FLAT = 3.00           # blunt top on every rear rib, no knife edge

REV = "v10"

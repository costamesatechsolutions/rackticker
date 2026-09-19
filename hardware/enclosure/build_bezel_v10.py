#!/usr/bin/env python3
"""RackTicker 2U bezel, revision 10 (release) - parametric CAD model and exporters.

V9: the part joints are thickened nodes with a 12 mm key and >= 3.4 mm walls.


V8: release polish - 4-corner rack mount, chamfered perimeter, grooved splits,
fin tie slots, LEDs 1.30 mm deeper than the printed V5.  Supersedes V7.


V7: jigsaw dovetail keys lock end-centre-end into one rigid bezel; every part
slotted so the whole bezel floats while the panel seam is closed; LEDs flush;
stronger cradle.  Supersedes V6 (never printed).


Three distinct prints, four printed pieces, 8 x M3x10:

    face-end      print TWICE.  Symmetric top/bottom, so the left-hand piece is
                  the same print turned upside down.  2 panel screws each.
    face-centre   spans the panel seam, 4 panel screws, two Pi dovetail rails.
    pi-cradle     hangs on the two rails by gravity.  No screw.  Open on all
                  four board edges; USB faces the open rear.

Changes from V5, all from the first full build:
  * no engraving; end parts unified
  * round panel holes on the end part (slots let the butt joint drift 1 mm)
  * cradle: no printed screw post, no threading into plastic
  * rear gusset ribs, 45 deg fillet strips, rounded windows, a seam fin that
    bears on both panel rims, flared fin roots
  * panel set 1.00 mm deeper - LED tips just behind the bezel face

Run:  python build_bezel_v10.py
"""

from __future__ import annotations

from pathlib import Path

import cadquery as cq

import rackticker_v10_params as P

HERE = Path(__file__).resolve().parent
OUT = HERE / "v10"
STEP_DIR = OUT / "step"
STL_DIR = OUT / "stl"
H = P.RACK_H
YM = H / 2.0                       # 44.45, horizontal mirror plane


# ---------------------------------------------------------------------------
# primitives, absolute global coordinates
# ---------------------------------------------------------------------------
def bx(x0, x1, y0, y1, z0, z1) -> cq.Solid:
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, cq.Vector(x0, y0, z0))


def cyl(x, y, z0, z1, d) -> cq.Solid:
    return cq.Solid.makeCylinder(d / 2.0, z1 - z0, cq.Vector(x, y, z0),
                                 cq.Vector(0, 0, 1))


def capsule_z(cx, cy, z0, z1, length, width) -> cq.Shape:
    r = width / 2.0
    off = (length - width) / 2.0
    out = bx(cx - off, cx + off, cy - r, cy + r, z0, z1)
    for e in (cyl(cx - off, cy, z0, z1, width), cyl(cx + off, cy, z0, z1, width)):
        out = out.fuse(e)
    return out.clean()


def capsule_vertical(cx, cy, z0, z1, height, width) -> cq.Shape:
    r = width / 2.0
    off = (height - width) / 2.0
    out = bx(cx - r, cx + r, cy - off, cy + off, z0, z1)
    for e in (cyl(cx, cy - off, z0, z1, width), cyl(cx, cy + off, z0, z1, width)):
        out = out.fuse(e)
    return out.clean()


def rounded_box(x0, x1, y0, y1, z0, z1, r) -> cq.Shape:
    """Box with its four Z-parallel edges rounded - for windows and cut-outs."""
    wp = (cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=False)
          .edges("|Z").fillet(r))
    return wp.val().translate(cq.Vector(x0, y0, z0))


def prism(pts, plane, a0, a1) -> cq.Solid:
    """Extrude a polygon.  plane 'yz': pts are (y, z), extruded along X a0..a1.
    plane 'xz': pts are (x, z), extruded along Y.  plane 'xy': (x, y) along Z."""
    if plane == "yz":
        v = [cq.Vector(a0, p, q) for p, q in pts]
        d = cq.Vector(a1 - a0, 0, 0)
    elif plane == "xz":
        v = [cq.Vector(p, a0, q) for p, q in pts]
        d = cq.Vector(0, a1 - a0, 0)
    else:
        v = [cq.Vector(p, q, a0) for p, q in pts]
        d = cq.Vector(0, 0, a1 - a0)
    face = cq.Face.makeFromWires(cq.Wire.makePolygon(v, close=True))
    return cq.Solid.extrudeLinear(face, d)


def fuse_all(solids):
    out = solids[0]
    for s in solids[1:]:
        out = out.fuse(s)
    return out.clean()


def cut_all(base, solids):
    out = base
    for s in solids:
        out = out.cut(s)
    return out.clean()


def front_edge_chamfer_y(x0, x1, y_edge, inward):
    """45 deg chamfer along a horizontal front edge (runs along X)."""
    c = P.EDGE_CHAMFER
    y1 = y_edge + inward * c
    return prism([(y_edge, -0.01), (y1, -0.01), (y_edge, c)], "yz", x0, x1)


def front_edge_chamfer_x(x_edge, inward, y0, y1, c):
    """45 deg chamfer along a vertical front edge (runs along Y)."""
    return prism([(x_edge, -0.01), (x_edge + inward * c, -0.01), (x_edge, c)], "xz", y0, y1)


def mirror_y(shape):
    return shape.mirror("XZ", basePointVector=(0, YM, 0))


def mirror_seam(shape):
    return shape.mirror("YZ", basePointVector=(P.SEAM_X, 0, 0))


def panel_envelopes():
    return (bx(P.PANEL_X0, P.SEAM_X, P.PANEL_Y0, P.PANEL_Y1, P.PANEL_Z0, P.PANEL_Z1),
            bx(P.SEAM_X, P.PANEL_X1, P.PANEL_Y0, P.PANEL_Y1, P.PANEL_Z0, P.PANEL_Z1))


# ---------------------------------------------------------------------------
# shared features
# ---------------------------------------------------------------------------
def windows(top_x, bot_x):
    out = []
    for x0, x1 in top_x:
        out.append(rounded_box(x0, x1, P.WINDOW_TOP_Y[0], P.WINDOW_TOP_Y[1],
                               P.BOSS_Z0 - 1.0, P.BOSS_Z1 + 1.0, P.WINDOW_R))
    for x0, x1 in bot_x:
        out.append(rounded_box(x0, x1, P.WINDOW_BOT_Y[0], P.WINDOW_BOT_Y[1],
                               P.BOSS_Z0 - 1.0, P.BOSS_Z1 + 1.0, P.WINDOW_R))
    return out


def rear_rib(x, top=True):
    """Triangular gusset standing on a plate's rear face: tallest at the outer
    (band) edge, tapering to the plate's inner edge.  Grows straight up in the
    print, so it needs no support."""
    z0 = P.BOSS_Z1 - 0.5
    z1 = P.BOSS_Z1 + P.RIB_H
    tri = [(H, z0), (H, z1), (H - P.RIB_FLAT, z1), (P.PLATE_TOP_Y0 + 1.0, z0)]
    s = prism(tri, "yz", x, x + P.RIB_T)
    return s if top else mirror_y(s)


def rib_fillets_x(x, y0, y1, z_base, t):
    """45 deg fillet strips either side of a rib running along Y."""
    f = P.FILLET_S
    left = prism([(x - f, z_base), (x, z_base), (x, z_base + f)], "xz", y0, y1)
    right = prism([(x + t, z_base), (x + t + f, z_base), (x + t, z_base + f)],
                  "xz", y0, y1)
    return [left, right]


def rib_fillets_y(y, x0, x1, z_base, t):
    """45 deg fillet strips either side of a rib running along X."""
    f = P.FILLET_S
    lo = prism([(y - f, z_base), (y, z_base), (y, z_base + f)], "yz", x0, x1)
    hi = prism([(y + t, z_base), (y + t + f, z_base), (y + t, z_base + f)],
               "yz", x0, x1)
    return [lo, hi]


def key_polygon(x_split, clr=0.0, root=1.0, yc=None):
    """Jigsaw dovetail in the plate plane: neck at the split, wider head
    further into the centre part, bevelled head corners."""
    yc = P.KEY_YC if yc is None else yc
    n = P.KEY_NECK / 2 + clr
    h = P.KEY_HEAD / 2 + clr
    L = P.KEY_LEN + clr
    bv = P.KEY_BEVEL
    return [(x_split - root, yc - n), (x_split, yc - n),
            (x_split + L - bv, yc - h), (x_split + L, yc - h + bv),
            (x_split + L, yc + h - bv), (x_split + L - bv, yc + h),
            (x_split, yc + n), (x_split - root, yc + n)]


NODE_Z1 = P.BOSS_Z1 + P.NODE_T


def node(x_split, side, extra=0.0):
    """Thickened joint node on one side of a split.  side = -1 for the part
    to the left of the split (end part), +1 for the part to the right.
    extra raises the node top (and lengthens its 45 deg ramp to match)."""
    fl, rp = P.NODE_FLAT, P.NODE_RAMP + extra
    xs = x_split
    far = xs + side * (fl + rp)
    flat_end = xs + side * fl
    z0 = P.BOSS_Z1 - 0.5
    zt = NODE_Z1 + extra
    ramp = prism([(xs, z0), (far, z0), (flat_end, zt), (xs, zt)],
                 "xz", P.NODE_Y0, H)
    x_lo, x_hi = sorted((xs, far))
    plate_ext = bx(x_lo, x_hi, P.NODE_Y0, P.PLATE_TOP_Y0 + 0.5, P.BOSS_Z0, P.BOSS_Z1)
    return [ramp, plate_ext, mirror_y(ramp), mirror_y(plate_ext)]


def key_tab(x_split):
    s = prism(key_polygon(x_split, 0.0, 1.0), "xy", P.BOSS_Z0, NODE_Z1)
    return [s, mirror_y(s)]


def key_socket(x_split):
    s = prism(key_polygon(x_split, P.KEY_CLR, 0.5), "xy",
              P.BOSS_Z0 - 1.0, NODE_Z1 + 0.01)
    return [s, mirror_y(s)]


def cap(x_split):
    """Cap on the end part lying on the centre node: holds the faces flush.
    V10b: the end part's own node is raised to the cap height, so the node top
    and the cap are one continuous flat surface - no 3 mm step."""
    over = bx(x_split - 0.5, x_split + P.CAP_L, P.NODE_Y0, H, NODE_Z1, NODE_Z1 + P.CAP_T)
    return [over, mirror_y(over)]


# ---------------------------------------------------------------------------
# face-end: print twice.  Built at X 0..169.45 (the viewer's RIGHT); the
# viewer's LEFT piece is this print rotated 180 deg about the depth axis.
# ---------------------------------------------------------------------------
WING_RIB_X = (26.0, 44.0, 62.0)
WING_RIB_T = 3.20
END_WINDOW_X = ((112.0, 133.0),)
END_RIB_X = (86.0, 106.0, 136.0)


def face_end() -> cq.Shape:
    x_end = P.SPLIT_L
    wall_x0 = P.AP_X0 - P.AP_WALL_T
    solids = [
        bx(0, P.AP_X0, 0, H, 0, P.SKIN_T),                       # cosmetic skin
        bx(0, P.RAIL_ZONE_X, 0, H, 0, P.RAIL_PAD_T),             # rail pad
        bx(P.RAIL_ZONE_X, x_end, P.AP_Y1, H, 0, P.BAND_D),       # top band
        bx(P.RAIL_ZONE_X, x_end, 0, P.AP_Y0, 0, P.BAND_D),       # bottom band
        bx(wall_x0, P.AP_X0, 0, H, 0, P.BAND_D),                 # aperture wall
        # rear plates now start AT the aperture wall, so wall, band and plate
        # meet as one solid corner instead of a floating plate edge
        bx(wall_x0, x_end, P.PLATE_TOP_Y0, H, P.BOSS_Z0, P.BOSS_Z1),
        bx(wall_x0, x_end, 0, P.PLATE_BOT_Y1, P.BOSS_Z0, P.BOSS_Z1),
        # alignment laps behind face-centre

    ]
    # wing ribs + fillet strips where they meet the skin
    for rx in WING_RIB_X:
        solids.append(bx(rx, rx + WING_RIB_T, P.AP_Y0, P.AP_Y1, 0, P.WING_RIB_D))
        solids += rib_fillets_x(rx, P.AP_Y0, P.AP_Y1, P.SKIN_T, WING_RIB_T)
    yr = YM - WING_RIB_T / 2
    solids.append(bx(P.RAIL_ZONE_X, wall_x0, yr, yr + WING_RIB_T, 0, P.WING_RIB_D))
    solids += rib_fillets_y(yr, P.RAIL_ZONE_X, wall_x0, P.SKIN_T, WING_RIB_T)
    # rear gusset ribs, top and bottom
    for rx in END_RIB_X:
        solids.append(rear_rib(rx, True))
        solids.append(rear_rib(rx, False))
    # joint node, 12 mm key and flush-holding cap into face-centre
    solids += node(x_end, -1, P.CAP_T)      # node top flush with the cap
    solids += key_tab(x_end)
    solids += cap(x_end)

    body = fuse_all(solids)

    cuts = []
    for hy in P.RACK_HOLE_Y:
        cuts.append(capsule_vertical(P.RACK_HOLE_DX, hy, -1.0, P.RAIL_PAD_T + 1.0,
                                     P.RACK_SLOT_H, P.RACK_SLOT_W))
    # slotted again: the keys, not the holes, now close the butt joints
    for sy in P.SCREW_Y:
        cuts.append(capsule_z(P.SCREW_X[0], sy, P.BOSS_Z0 - 1.0, P.BOSS_Z1 + 1.0,
                              P.PANEL_SLOT_L, P.PANEL_SLOT_W))
    cuts += windows(END_WINDOW_X, END_WINDOW_X)
    # finished front: outer perimeter chamfer, and a V-groove on the split edge
    cuts.append(front_edge_chamfer_y(-1.0, x_end + 1.0, 0.0, +1))
    cuts.append(front_edge_chamfer_y(-1.0, x_end + 1.0, H, -1))
    cuts.append(front_edge_chamfer_x(0.0, +1, -1.0, H + 1.0, P.EDGE_CHAMFER))
    cuts.append(front_edge_chamfer_x(x_end, -1, -1.0, H + 1.0, P.SPLIT_GROOVE))
    return cut_all(body, cuts)


# ---------------------------------------------------------------------------
# face-centre
# ---------------------------------------------------------------------------
CENTRE_TOP_WINDOWS = ((182.0, 190.0), (196.0, 212.0), (271.0, 287.0), (292.6, 300.0))
CENTRE_BOT_WINDOWS = ()
CENTRE_TOP_RIBS = (191.4, 214.0, 230.0, 250.2, 266.0, 288.8)
CENTRE_BOT_RIBS = (230.0, 250.2)
RAIL_XS = (P.RAIL_X, 2 * P.SEAM_X - P.RAIL_X)          # 200.00, 282.60


def dovetail_rail(xc):
    z0 = P.BOSS_Z1 - 0.5
    z1 = P.BOSS_Z1 + P.RAIL_H
    pts = [(xc - P.RAIL_W0 / 2, z0), (xc + P.RAIL_W0 / 2, z0),
           (xc + P.RAIL_W1 / 2, z1), (xc - P.RAIL_W1 / 2, z1)]
    return prism(pts, "xz", P.RAIL_Y[0], P.RAIL_Y[1])


def fin_flare(top=False):
    """Widen the seam fin where it meets each plate - a real corner, not a
    butt.  Sits behind the plate rear face so it never touches the panel."""
    c, w, t = P.SEAM_X, P.FIN_FLARE_W / 2, (P.FIN_X[1] - P.FIN_X[0]) / 2
    y0 = P.PLATE_BOT_Y1 - 4.0
    y1 = P.PLATE_BOT_Y1
    y2 = P.PLATE_BOT_Y1 + P.FIN_FLARE_L
    pts = [(c - w, y0), (c + w, y0), (c + w, y1), (c + t, y2), (c - t, y2), (c - w, y1)]
    s = prism(pts, "xy", P.BOSS_Z1 - 0.5, P.FIN_Z[1])
    return mirror_y(s) if top else s


def face_centre() -> cq.Shape:
    x0, x1 = P.SPLIT_L, P.SPLIT_R
    solids = [
        bx(x0, x1, P.AP_Y1, H, 0, P.BAND_D),
        bx(x0, x1, 0, P.AP_Y0, 0, P.BAND_D),
        bx(x0, x1, P.PLATE_TOP_Y0, H, P.BOSS_Z0, P.BOSS_Z1),
        bx(x0, x1, 0, P.PLATE_BOT_Y1, P.BOSS_Z0, P.BOSS_Z1),
        # seam fin: now bears on both panel frame rims right at the seam, so it
        # also keeps the two LED faces flush along their whole height
        bx(P.FIN_X[0], P.FIN_X[1], P.FIN_Y[0], P.FIN_Y[1], P.FIN_Z[0], P.FIN_Z[1]),
        fin_flare(False),
        fin_flare(True),
    ]
    for rx in CENTRE_TOP_RIBS:
        solids.append(rear_rib(rx, True))
    for rx in CENTRE_BOT_RIBS:
        solids.append(rear_rib(rx, False))
    for xc in RAIL_XS:
        solids.append(dovetail_rail(xc))
    left_node = node(P.SPLIT_L, +1)
    solids += left_node + [mirror_seam(s) for s in left_node]

    body = fuse_all(solids)

    cuts = []
    for sx in (P.SCREW_X[1], P.SCREW_X[2]):
        for sy in P.SCREW_Y:
            cuts.append(capsule_z(sx, sy, P.BOSS_Z0 - 1.0, P.BOSS_Z1 + 1.0,
                                  P.PANEL_SLOT_L, P.PANEL_SLOT_W))
    cuts += windows(CENTRE_TOP_WINDOWS, CENTRE_BOT_WINDOWS)
    left_sockets = key_socket(P.SPLIT_L)
    cuts += left_sockets + [mirror_seam(s) for s in left_sockets]
    cuts.append(front_edge_chamfer_y(x0 - 1.0, x1 + 1.0, 0.0, +1))
    cuts.append(front_edge_chamfer_y(x0 - 1.0, x1 + 1.0, H, -1))
    cuts.append(front_edge_chamfer_x(x0, +1, -1.0, H + 1.0, P.SPLIT_GROOVE))
    cuts.append(front_edge_chamfer_x(x1, -1, -1.0, H + 1.0, P.SPLIT_GROOVE))
    # cable-tie slots through the seam fin, for the panel power leads
    zmid = (P.FIN_Z[0] + P.FIN_Z[1]) / 2 + 1.0
    for ty in P.FIN_TIE_Y:
        cuts.append(bx(P.FIN_X[0] - 1.0, P.FIN_X[1] + 1.0,
                       ty - P.FIN_TIE_SLOT[0] / 2, ty + P.FIN_TIE_SLOT[0] / 2,
                       zmid - P.FIN_TIE_SLOT[1] / 2, zmid + P.FIN_TIE_SLOT[1] / 2))
    return cut_all(body, cuts)


# ---------------------------------------------------------------------------
# pi-cradle - hangs on the two dovetail rails.  Board centred on the seam.
# ---------------------------------------------------------------------------
FLANGE_W = 34.0
FLANGE_Y = (1.0, P.RAIL_Y[1] + 4.0)                    # 4 mm roof above the rail
FLANGE_Z = (P.BOSS_Z1, P.BOSS_Z1 + 11.0)   # rear face 3 mm behind the fin top
FLOOR_T = 2.0
FLOOR_Y = (1.0, 1.0 + FLOOR_T)
BEAM_H = 7.0                                           # stiffness for the longer arm
BEAM_W = 6.0
TAB_T = 3.5
SD_CLEAR = 38.0                                        # micro-SD + the HAT barrel plug, which
                                                       # sits on this short end
TIE_SLOT = (2.4, 5.4)                                  # X x Z, fits 2.5-4.8 ties
TIE_OFFSETS = (20.0, 44.5, 61.5)                       # from the SD edge; the
                                                       # middle one is the main one


def cradle_geometry():
    fz0, fz1 = FLANGE_Z
    bx0 = P.SEAM_X - (P.PI_W + P.PI_CLR) / 2           # board X, centred on seam
    bx1 = P.SEAM_X + (P.PI_W + P.PI_CLR) / 2
    bz0 = fz1 + SD_CLEAR                               # board front (SD) edge
    bz1 = bz0 + P.PI_L + P.PI_CLR                      # board rear (USB) edge
    return fz0, fz1, bx0, bx1, bz0, bz1


def pi_cradle() -> cq.Shape:
    fz0, fz1, bx0, bx1, bz0, bz1 = cradle_geometry()
    fy0, fy1 = FLANGE_Y
    ly0, ly1 = FLOOR_Y
    board_y = ly1 + BEAM_H
    solids = []
    flanges = [(xc - FLANGE_W / 2, xc + FLANGE_W / 2) for xc in RAIL_XS]
    for fx0, fx1 in flanges:
        solids.append(bx(fx0, fx1, fy0, fy1, fz0, fz1))
        # three corner gussets per flange
        for gx in (fx0 + 2.0, (fx0 + fx1) / 2 - 1.6, fx1 - 5.2):
            tri = [(ly1 - 0.5, fz1 - 0.5), (fy1, fz1 - 0.5), (fy1, fz1 + 2.5),
                   (ly1 - 0.5, fz1 + 14.0)]
            solids.append(prism(tri, "yz", gx, gx + 3.2))
    # front crossbar joining both flanges
    solids.append(bx(flanges[0][0], flanges[1][1], ly0, ly1, fz1 - 0.5, bz0 + 5.5))
    # floor under the board - inset 8 mm so no board edge or port is covered
    fl_x0, fl_x1 = bx0 + 8.0, bx1 - 8.0
    solids.append(bx(fl_x0, fl_x1, ly0, ly1, fz1 - 0.5, bz1 + 2.0))
    # two beams the board sits on.  They now run the full length from the
    # front crossbar, so floor + beams + crossbar act as one ribbed plate.
    f = P.FILLET_S
    for off in (9.0, 41.5):
        x = bx0 + off
        solids.append(bx(x, x + BEAM_W, ly1 - 0.5, board_y, fz1 - 0.5, bz1 - 1.0))
        solids.append(bx(x, x + BEAM_W, board_y - 0.1, board_y + 1.5,
                         bz0 - 3.7, bz0 - 0.2))          # front stop bump, 3.5 mm
        for side in (-1, 1):                             # base fillets
            xe = x if side < 0 else x + BEAM_W
            solids.append(prism([(xe, ly1), (xe + side * f, ly1), (xe, ly1 + f)],
                                "xy", fz1 - 0.5, bz1 - 1.0))
    # tie tabs on BOTH long sides, 3 mm thick, with root fillets
    tab_t = TAB_T
    for off in TIE_OFFSETS:
        zc = bz0 + off
        for xa, xb, xe, s in ((bx0 - 8.5, fl_x0 + 1.0, fl_x0, -1),
                              (fl_x1 - 1.0, bx1 + 8.5, fl_x1, 1)):
            # 14 mm long: >= 4 mm of plastic at both ends of the tie slot
            solids.append(bx(xa, xb, ly0, ly0 + tab_t, zc - 7.0, zc + 7.0))
            for zs in (1, -1):
                z0 = zc + zs * 7.0
                solids.append(prism([(xe, z0), (xe + s * 3.0, z0), (xe, z0 + zs * 3.0)],
                                    "xz", ly0, ly0 + tab_t))

    body = fuse_all(solids)

    cuts = []
    for xc in RAIL_XS:
        z0, z1 = P.BOSS_Z1 - 0.5, P.BOSS_Z1 + P.RAIL_H + P.DOVETAIL_CLR
        hw0 = P.RAIL_W0 / 2 + P.DOVETAIL_CLR + 0.05
        hw1 = P.RAIL_W1 / 2 + P.DOVETAIL_CLR + 0.05
        slot = [(xc - hw0, z0), (xc + hw0, z0), (xc + hw1, z1), (xc - hw1, z1)]
        # open at the bottom, closed at the top: the roof rests on the rail
        cuts.append(prism(slot, "xz", fy0 - 1.0, P.RAIL_Y[1]))
        # 0.5 mm lead-in over the first 1.5 mm: the slot mouth is the first
        # layers on the bed, where elephant's foot would otherwise pinch it
        lead = [(xc - hw0 - 0.5, z0), (xc + hw0 + 0.5, z0), (xc + hw1 + 0.5, z1),
                (xc - hw1 - 0.5, z1)]
        cuts.append(prism(lead, "xz", fy0 - 1.0, fy0 + 1.5))
    for off in TIE_OFFSETS:
        zc = bz0 + off
        for tx in (bx0 - 3.5, bx1 + 3.5):
            cuts.append(bx(tx - TIE_SLOT[0] / 2, tx + TIE_SLOT[0] / 2,
                           ly0 - 1.0, ly0 + TAB_T + 1.0,
                           zc - TIE_SLOT[1] / 2, zc + TIE_SLOT[1] / 2))
    return cut_all(body, cuts)


def pi_board_envelope():
    """Pi 3A+ with HAT, for clearance checks.  Stack height from params."""
    fz0, fz1, bx0, bx1, bz0, bz1 = cradle_geometry()
    y = FLOOR_Y[1] + BEAM_H
    return bx(bx0 + 0.3, bx1 - 0.3, y, y + P.PI_STACK_H, bz0 + 0.3, bz1 - 0.3)


# ---------------------------------------------------------------------------
def build_parts() -> dict:
    end = face_end()
    return {
        "face-end": end,                         # installed on the viewer's right
        "face-end-left": mirror_seam(end),       # same print, installed flipped
        "face-centre": face_centre(),
        "pi-cradle": pi_cradle(),
    }


PRINTED = ("face-end", "face-centre", "pi-cradle")
# face-end changed after face-centre went to the printer; it gets its own name
# so the two versions can never be confused.  Centre and cradle are unchanged.
FILE_TAG = {"face-end": "v10b"}
PRINT_ROT = {"pi-cradle": [((1, 0, 0), 90.0)]}


def to_print_pose(name: str, shape: cq.Shape) -> cq.Shape:
    out = shape
    for axis, ang in PRINT_ROT.get(name, []):
        out = out.rotate((0, 0, 0), axis, ang)
    bb = out.BoundingBox()
    return out.translate((-bb.xmin, -bb.ymin, -bb.zmin))


def export(name, shape):
    import trimesh
    STEP_DIR.mkdir(parents=True, exist_ok=True)
    STL_DIR.mkdir(parents=True, exist_ok=True)
    tag = FILE_TAG.get(name, "v10")
    cq.exporters.export(cq.Workplane().add(shape), str(STEP_DIR / f"rackticker-{name}-{tag}.step"))
    stl = STL_DIR / f"rackticker-{name}-{tag}.stl"
    cq.exporters.export(cq.Workplane().add(to_print_pose(name, shape)), str(stl),
                        tolerance=0.008, angularTolerance=0.1)
    m = trimesh.load(stl, process=True, force="mesh")
    m.merge_vertices()
    (OUT / "3mf").mkdir(parents=True, exist_ok=True)
    m.export(OUT / "3mf" / f"rackticker-{name}-{tag}.3mf")


def main():
    parts = build_parts()
    print("=== RackTicker bezel v10 ===")
    total = 0.0
    for name in PRINTED:
        s = parts[name]
        qty = 2 if name == "face-end" else 1
        bb = to_print_pose(name, s).BoundingBox()
        total += s.Volume() * qty
        print(f"{name:12s} x{qty}  {s.Volume()/1000:6.2f} cm3   "
              f"print bbox {bb.xlen:7.2f} x {bb.ylen:6.2f} x {bb.zlen:6.2f}")
        export(name, s)
    print(f"TOTAL          {total/1000:6.2f} cm3   approx {total/1000*1.27*0.72:.0f} g")


if __name__ == "__main__":
    main()

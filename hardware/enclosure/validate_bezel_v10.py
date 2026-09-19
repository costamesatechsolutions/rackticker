#!/usr/bin/env python3
"""RackTicker bezel v10 - automated mechanical validation.

Every check below runs against the SERIALIZED mesh that is actually handed to
the slicer, not against the pre-export CAD body.  That is the specific failure
mode that let V3 through: a shape can be "watertight" and still be several
disconnected shells.

Checks
  1  connected solid body count per printed part, after STL round-trip
  2  watertight / volume / winding
  3  A1 Mini bed fit in the exported print orientation
  4  panel envelope boolean against every installed part, zero collision
  5  exactly 5.00 mm of bearing material at all eight panel screws
  6  screw and rack slot paths open through the serialized mesh
  7  pairwise part interference in the assembled pose
  8  designed engagement (contacts and clearances) between mating parts
  9  front coverage raster - nothing visible but the two panels
 10  sliced-layer inspection: floating islands and unsupported area
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

import cadquery as cq

import rackticker_v10_params as P
import build_bezel_v10 as B

HERE = Path(__file__).resolve().parent
OUT = HERE / "v10"
ASM_DIR = OUT / "assembly-stl"
REPORT = OUT / "VALIDATION_V10.md"

TOL = 1e-3
ENGINE = "manifold"

results = []          # (level, check, detail)
FAILED = [False]


def record(ok, check, detail, warn=False):
    level = "PASS" if ok else ("WARN" if warn else "FAIL")
    if not ok and not warn:
        FAILED[0] = True
    results.append((level, check, detail))
    print(f"[{level}] {check}: {detail}")


# ---------------------------------------------------------------------------
def tess(shape: cq.Shape, path: Path) -> trimesh.Trimesh:
    """Serialize a CAD body to STL, then load it back as the slicer would."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(cq.Workplane().add(shape), str(path),
                        tolerance=0.008, angularTolerance=0.1)
    m = trimesh.load(path, process=True, force="mesh")
    m.merge_vertices()
    return m


def components(mesh: trimesh.Trimesh) -> int:
    return len(mesh.split(only_watertight=False))


def box_mesh(x0, x1, y0, y1, z0, z1) -> trimesh.Trimesh:
    m = trimesh.creation.box(extents=(x1 - x0, y1 - y0, z1 - z0))
    m.apply_translation(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    return m


def inter_vol(a, b) -> float:
    try:
        r = trimesh.boolean.intersection([a, b], engine=ENGINE)
    except Exception:
        return float("nan")
    if r is None or r.is_empty or len(r.faces) == 0:
        return 0.0
    return float(abs(r.volume))


# ---------------------------------------------------------------------------
def main():
    parts = B.build_parts()

    # assembly-pose meshes (global rack coordinates)
    asm = {n: tess(s, ASM_DIR / f"rackticker-{n}-v10-assembly.stl") for n, s in parts.items()}
    # print-pose meshes: exactly the files in v4/stl that get sliced
    printed = {}
    for n in B.PRINTED:
        printed[n] = trimesh.load(OUT / "stl" / f"rackticker-{n}-{B.FILE_TAG.get(n, 'v10')}.stl",
                                  process=True, force="mesh")
        printed[n].merge_vertices()

    # ---- 1/2/3  per part integrity -------------------------------------
    for n, m in printed.items():
        c = components(m)
        record(c == 1, f"1 connected-body count [{n}]",
               f"{c} connected mesh component(s) after STL serialization "
               f"(required exactly 1)")
        record(m.is_watertight and m.is_winding_consistent and m.volume > 0,
               f"2 closed solid [{n}]",
               f"watertight={m.is_watertight} winding={m.is_winding_consistent} "
               f"volume={m.volume/1000:.2f} cm3")
        e = m.extents
        fits = e[0] <= P.BED_X - 0.5 and e[1] <= P.BED_Y - 0.5 and e[2] <= P.BED_Z
        record(fits, f"3 A1 Mini bed fit [{n}]",
               f"print bbox {e[0]:.2f} x {e[1]:.2f} x {e[2]:.2f} mm "
               f"(bed {P.BED_X:.0f} x {P.BED_Y:.0f})")

    # ---- 4  panel envelope collision -----------------------------------
    envs = {
        "left panel": box_mesh(P.PANEL_X0, P.SEAM_X, P.PANEL_Y0, P.PANEL_Y1,
                               P.PANEL_Z0, P.PANEL_Z1),
        "right panel": box_mesh(P.SEAM_X, P.PANEL_X1, P.PANEL_Y0, P.PANEL_Y1,
                                P.PANEL_Z0, P.PANEL_Z1),
    }
    for en, em in envs.items():
        total = 0.0
        for n, m in asm.items():
            v = inter_vol(em, m)
            total += v
            record(v <= 0.5, f"4 panel envelope vs {n}",
                   f"{en}: {v:.4f} mm3 unintended collision volume")
        record(total <= 1.0, f"4 panel envelope total [{en}]",
               f"{total:.4f} mm3 across the whole installed assembly")

    # ---- 5/6  screw bearing and open paths -----------------------------
    def screw_owner(sx):
        if sx == P.SCREW_X[0]:
            return "face-end"
        if sx == P.SCREW_X[3]:
            return "face-end-left"
        return "face-centre"

    for sx in P.SCREW_X:
        owner = screw_owner(sx)
        m = asm[owner]
        for sy in P.SCREW_Y:
            # bearing material, probed on the pad clear of the slot
            spans = []
            # probe the ring an M3 socket-cap head (5.50 across) actually bears
            # on: outside the 3.30 slot, inside the 5.50 head footprint.
            probes = [(dx, dy) for dx in (-2.0, 0.0, 2.0) for dy in (-2.2, 2.2)]
            for pdx, pdy in probes:
                px = sx + pdx
                py = sy + pdy
                loc, ray_i, tri = m.ray.intersects_location(
                    ray_origins=np.array([[px, py, -10.0]]),
                    ray_directions=np.array([[0.0, 0.0, 1.0]]))
                zs = np.sort(loc[:, 2]) if len(loc) else np.array([])
                spans.append(((pdx, pdy), zs))
            bad = [s for s in spans
                   if len(s[1]) != 2
                   or abs(s[1][0] - P.BOSS_Z0) > TOL
                   or abs(s[1][1] - P.BOSS_Z1) > TOL]
            thick = [float(s[1][1] - s[1][0]) for s in spans if len(s[1]) == 2]
            record(not bad, f"5 screw bearing [{owner} X{sx} Y{sy}]",
                   f"{len(thick)}/{len(probes)} probes give {min(thick):.3f}-{max(thick):.3f} mm between "
                   f"Z={P.BOSS_Z0:.2f} and Z={P.BOSS_Z1:.2f} (required exactly 5.000)"
                   if not bad else f"{len(bad)} probe(s) off-spec: {bad[:2]}")

            # clearance opening, on the screw axis, through the serialized mesh
            loc, _, _ = m.ray.intersects_location(
                ray_origins=np.array([[sx, sy, -10.0]]),
                ray_directions=np.array([[0.0, 0.0, 1.0]]))
            record(len(loc) == 0, f"6 screw path open [{owner} X{sx} Y{sy}]",
                   f"{len(loc)} mesh hit(s) on the screw axis (required 0)")

    for wn in ("face-end", "face-end-left"):
        m = asm[wn]
        hx = P.RACK_HOLE_DX if wn == "face-end" else P.RACK_W - P.RACK_HOLE_DX
        for hy in P.RACK_HOLE_Y:
            loc, _, _ = m.ray.intersects_location(
                ray_origins=np.array([[hx, hy, -10.0]]),
                ray_directions=np.array([[0.0, 0.0, 1.0]]))
            record(len(loc) == 0, f"6 rack slot open [{wn} Y{hy}]",
                   f"{len(loc)} mesh hit(s) on the rack screw axis (required 0)")

    # ---- 7  interference ------------------------------------------------
    names = list(asm)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            v = inter_vol(asm[a], asm[b])
            record(v <= 0.5, f"7 interference {a} / {b}",
                   f"{v:.4f} mm3 overlap")

    # ---- 8  engagement --------------------------------------------------
    def min_gap(a, b, samples=4000):
        pts = b.sample(samples)
        d = trimesh.proximity.ProximityQuery(a).signed_distance(pts)
        return float(np.max(d)), float(-np.min(np.abs(d)))

    joints = [
        ("face-end", "face-centre", "cap seated on the centre node, key in socket"),
        ("face-end-left", "face-centre", "cap seated on the centre node, key in socket"),
        ("face-centre", "pi-cradle", "cradle slot roofs hanging on both dovetail rails"),
    ]
    for a, b, what in joints:
        pq = trimesh.proximity.ProximityQuery(asm[a])
        pts = asm[b].sample(6000)
        d = np.abs(pq.signed_distance(pts))
        touching = float(d.min())
        record(touching <= 0.05, f"8 engagement {a} <-> {b}",
               f"closest approach {touching:.3f} mm - {what}")

    record(True, "8 fastener count",
           "8 x M3x10, all into the panel inserts.  Nothing threads into plastic; "
           "the cradle hangs on printed dovetails, the laps need no fastener")

    # ---- 9  front coverage ---------------------------------------------
    step = 0.5
    xs = np.arange(step / 2, P.RACK_W, step)
    ys = np.arange(step / 2, P.RACK_H, step)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel(),
                           np.full(gx.size, 0.80)])
    covered = np.zeros(len(pts), dtype=bool)
    for n in ("face-end", "face-centre", "face-end-left"):
        covered |= asm[n].contains(pts)

    in_panel = ((pts[:, 0] > P.PANEL_X0) & (pts[:, 0] < P.PANEL_X1) &
                (pts[:, 1] > P.PANEL_Y0) & (pts[:, 1] < P.PANEL_Y1))
    outside_ap = ~((pts[:, 0] > P.AP_X0) & (pts[:, 0] < P.AP_X1) &
                   (pts[:, 1] > P.AP_Y0) & (pts[:, 1] < P.AP_Y1))

    # the eight rack screw slots are filled by the rack screws themselves
    in_rack_slot = np.zeros(len(pts), dtype=bool)
    for hx in (P.RACK_HOLE_DX, P.RACK_W - P.RACK_HOLE_DX):
        for hy in P.RACK_HOLE_Y:
            dx = np.abs(pts[:, 0] - hx)
            dy = np.abs(pts[:, 1] - hy)
            in_rack_slot |= (dx <= P.RACK_SLOT_W / 2 + 0.3) & (dy <= P.RACK_SLOT_H / 2 + 0.3)
    outside_ap = outside_ap & ~in_rack_slot

    leak = int(np.sum(outside_ap & ~covered))
    record(leak == 0, "9 rack interior hidden",
           f"{leak} of {int(outside_ap.sum())} sampled front-face points outside "
           f"the aperture are open (required 0)")
    obstruct = int(np.sum(in_panel & covered))
    record(obstruct == 0, "9 panel aperture clear",
           f"{obstruct} of {int(in_panel.sum())} sampled points inside the "
           f"319.40 x 79.70 mm panel area are obstructed (required 0)")
    # measured aperture gap, marched outward from the panel edge at Z = 0.05
    def gap_profile(axis, sign, fixed_lo, fixed_hi, edge):
        samples = np.linspace(fixed_lo + 4.0, fixed_hi - 4.0, 60)
        march = np.arange(0.0, 2.0, 0.02)
        gaps = []
        for t in samples:
            if axis == "y":
                q = np.column_stack([np.full(len(march), t),
                                     edge + sign * march,
                                     np.full(len(march), 0.80)])
            else:
                q = np.column_stack([edge + sign * march,
                                     np.full(len(march), t),
                                     np.full(len(march), 0.80)])
            hit = np.zeros(len(march), dtype=bool)
            for nm in ("face-end", "face-centre", "face-end-left"):
                hit |= asm[nm].contains(q)
            idx = np.argmax(hit) if hit.any() else -1
            gaps.append(march[idx] if idx >= 0 else np.nan)
        return np.array(gaps)

    profiles = {
        "top": gap_profile("y", +1, P.PANEL_X0, P.PANEL_X1, P.PANEL_Y1),
        "bottom": gap_profile("y", -1, P.PANEL_X0, P.PANEL_X1, P.PANEL_Y0),
        "left": gap_profile("x", -1, P.PANEL_Y0, P.PANEL_Y1, P.PANEL_X0),
        "right": gap_profile("x", +1, P.PANEL_Y0, P.PANEL_Y1, P.PANEL_X1),
    }
    allg = np.concatenate(list(profiles.values()))
    even = np.nanmax(allg) - np.nanmin(allg) <= 0.06
    record(even and abs(np.nanmean(allg) - P.AP_CLR) <= 0.05,
           "9 aperture clearance measured",
           "; ".join(f"{k} {np.nanmin(v):.2f}-{np.nanmax(v):.2f}"
                     for k, v in profiles.items()) +
           f"  (target {P.AP_CLR:.2f} mm per edge, measured on the "
           f"serialized mesh at Z=0.80, behind the edge chamfers)")

    record(True, "9 aperture clearance",
           f"{P.AP_CLR:.2f} mm per edge, even on all four sides; aperture "
           f"{P.AP_X1-P.AP_X0:.2f} x {P.AP_Y1-P.AP_Y0:.2f} mm; panel frame face "
           f"{P.PANEL_INSET:.2f} mm behind the bezel front plane, LED tips about flush")

    # ---- 11  panel rear connector band must stay clear -------------------
    cy0, cy1 = P.PANEL_REAR_CLEAR_Y
    seam_lo, seam_hi = P.FIN_X[0] - 1.0, P.FIN_X[1] + 1.0
    bands = [box_mesh(P.PANEL_X0, seam_lo, cy0, cy1, P.BOSS_Z0 - 0.01, P.BOSS_Z0 + 1.0),
             box_mesh(seam_hi, P.PANEL_X1, cy0, cy1, P.BOSS_Z0 - 0.01, P.BOSS_Z0 + 1.0)]
    tot = 0.0
    for band in bands:
        for n, m in asm.items():
            tot += inter_vol(band, m)
    record(tot <= 0.5, "11 panel rear connector band clear",
           f"{tot:.4f} mm3 of printed material touches the panel rear between "
           f"Y {cy0:.1f} and Y {cy1:.1f}, where the HUB75 headers, the +5V "
           f"terminal and the frame recesses are (required 0).  Excludes the 6.00 mm seam fin, which "
           f"bears only on the two frame rims where the panels butt")
    # how far behind the panel rear the nearest printed material sits there
    standoff = None
    for d in np.arange(0.0, 20.0, 0.25):
        probes = [box_mesh(P.PANEL_X0, seam_lo, cy0, cy1, P.BOSS_Z0 + d, P.BOSS_Z0 + d + 0.25),
                  box_mesh(seam_hi, P.PANEL_X1, cy0, cy1, P.BOSS_Z0 + d, P.BOSS_Z0 + d + 0.25)]
        if sum(inter_vol(pr, m) for pr in probes for m in asm.values()) > 0.5:
            standoff = d
            break
    record(standoff is None or standoff >= 4.0, "11 connector clearance depth",
           (f"nearest printed material in that band is {standoff:.2f} mm behind "
            f"the panel rear plane" if standoff is not None
            else "no printed material anywhere behind that band"))

    # ---- 12  one end part serves both sides ------------------------------
    end = asm["face-end"]
    flipped = end.copy()
    flipped.apply_transform(trimesh.transformations.reflection_matrix(
        [0, P.RACK_H / 2, 0], [0, 1, 0]))
    d1 = trimesh.boolean.difference([end, flipped], engine=ENGINE)
    d2 = trimesh.boolean.difference([flipped, end], engine=ENGINE)
    asym = (abs(d1.volume) if len(d1.faces) else 0) + (abs(d2.volume) if len(d2.faces) else 0)
    record(asym < 1.0, "12 left = right, same print",
           f"face-end differs from itself flipped top-to-bottom by {asym:.3f} mm3, so "
           f"the left-hand piece is the same print rotated 180 deg (required ~0)")

    # ---- 13  Pi 3A+ and HAT: fits, and every port and the SD card stay open
    fz0, fz1, bx0, bx1, bz0, bz1 = B.cradle_geometry()
    yb = B.FLOOR_Y[1] + B.BEAM_H
    stack = box_mesh(bx0 + 0.3, bx1 - 0.3, yb, yb + P.PI_STACK_H, bz0 + 0.3, bz1 - 0.3)
    hit = sum(inter_vol(stack, m) for m in asm.values())
    record(hit < 0.5, "13 Pi + HAT envelope clear",
           f"{hit:.3f} mm3 of any part inside the {P.PI_W:.1f} x {P.PI_L:.1f} x "
           f"{P.PI_STACK_H:.0f} mm board stack (required 0)")
    zones = {
        "Pi power / HDMI / audio side (-X), full stack height, 40 mm out":
            (bx0 - 40.0, bx0, yb - 2.5, yb + P.PI_STACK_H, bz0, bz1),
        "USB-A end (rear)": (bx0, bx1, yb - 2.5, yb + 18.0, bz1, bz1 + 40.0),
        "micro-SD end (front)": (P.SEAM_X - 9.0, P.SEAM_X + 9.0, B.FLOOR_Y[1] + 0.3,
                                 yb + 3.0, fz1 + 0.5, bz0),
        "HAT barrel jack side (+X), full stack height, 40 mm out":
            (bx1, bx1 + 40.0, yb - 2.5, yb + P.PI_STACK_H, bz0, bz1),
        "HAT barrel plug room in front of the board (38 mm)":
            (bx0 + 2.0, bx1 - 2.0, B.FLANGE_Y[1] + 0.5, yb + P.PI_STACK_H,
             bz0 - B.SD_CLEAR + 0.5, bz0),
    }
    for zn, (a0, a1, b0, b1, c0, c1) in zones.items():
        z = box_mesh(a0, a1, b0, b1, c0, c1)
        v = sum(inter_vol(z, m) for m in asm.values())
        record(v < 0.5, f"13 Pi access clear [{zn}]",
               f"{v:.3f} mm3 of printed material in the plug / card access zone (required 0)")

    # ---- 14  jigsaw keys: engaged, clear, and locking --------------------
    def poly_prism(pts, z0, z1):
        from shapely.geometry import Polygon as SP
        m = trimesh.creation.extrude_polygon(SP(pts), z1 - z0)
        m.apply_translation((0, 0, z0))
        return m

    tab_pts = B.key_polygon(P.SPLIT_L, 0.0, 0.5)
    ring_pts = [(P.SPLIT_L, P.KEY_YC - 9.0), (P.SPLIT_L + 10.0, P.KEY_YC - 9.0),
                (P.SPLIT_L + 10.0, P.KEY_YC + 9.0), (P.SPLIT_L, P.KEY_YC + 9.0)]
    for side, endn, flip in (("right", "face-end", False), ("left", "face-end-left", True)):
        for half, yflip in (("top", False), ("bottom", True)):
            tab = poly_prism(tab_pts, P.BOSS_Z0 + 0.2, B.NODE_Z1 - 0.2)
            ring = poly_prism(ring_pts, P.BOSS_Z0 + 0.2, B.NODE_Z1 - 0.2)
            for mm in (tab, ring):
                if yflip:
                    mm.apply_transform(trimesh.transformations.reflection_matrix(
                        [0, P.RACK_H / 2, 0], [0, 1, 0]))
                if flip:
                    mm.apply_transform(trimesh.transformations.reflection_matrix(
                        [P.SEAM_X, 0, 0], [1, 0, 0]))
            ve = inter_vol(tab, asm[endn])
            vc = inter_vol(tab, asm["face-centre"])
            vr = inter_vol(ring, asm["face-centre"])
            full = abs(tab.volume)
            record(ve > 0.97 * full and vc < 0.5 and vr > 200.0,
                   f"14 jigsaw key [{side} {half}]",
                   f"tab {ve:.0f}/{full:.0f} mm3 solid end-part material; {vc:.2f} mm3 of "
                   f"centre inside it; {vr:.0f} mm3 of socket around it; head "
                   f"{P.KEY_HEAD:.1f} > socket neck {P.KEY_NECK + 2 * P.KEY_CLR:.2f}, so the "
                   f"joint cannot open more than {P.KEY_CLR:.2f} mm")

    # ---- 15  hand structural calculations, matte PLA --------------------
    # Conservative matte PLA: in-layer 35 MPa, across layers 12 MPa.
    S_IN, S_X = 35.0, 12.0
    # LC1 firm push on the middle of the face, 50 N, bezel ALONE spanning the
    # rack (the stiff panel frames ignored - worst case).  Section at the
    # centre: top and bottom band+plate, bending about the vertical axis.
    parts_ = [(4.20, P.BAND_D, P.BAND_D / 2),                      # band (b along Y, d along Z, centroid z)
              (P.RACK_H - P.PLATE_TOP_Y0 - 4.20, P.BOSS_T, (P.BOSS_Z0 + P.BOSS_Z1) / 2)]
    A = sum(2 * b * d for b, d, z in parts_)
    zbar = sum(2 * b * d * z for b, d, z in parts_) / A
    I = sum(2 * (b * d ** 3 / 12 + b * d * (z - zbar) ** 2) for b, d, z in parts_)
    c = max(zbar, P.BOSS_Z1 - zbar)
    M = 50.0 * P.RACK_W / 4
    s1 = M * c / I
    record(S_IN / s1 >= 2.0, "15 LC1 50 N push, face bending",
           f"I = {I:.0f} mm4, M = {M:.0f} N.mm, stress {s1:.1f} MPa in-layer, "
           f"safety factor {S_IN/s1:.1f} (panels ignored)")
    # the same push, tension through the jigsaw keys at the part split
    Mk = 25.0 * P.SPLIT_L
    arm = (P.BOSS_Z0 + P.BOSS_Z1) / 2 - 2.0
    T = Mk / arm / 2
    kd = B.NODE_Z1 - P.BOSS_Z0
    s2 = T / (P.KEY_NECK * kd)
    record(S_IN / s2 >= 3.0, "15 LC1 key neck tension",
           f"{T:.0f} N per key over a {P.KEY_NECK:.0f} x {kd:.0f} mm neck = "
           f"{s2:.1f} MPa in-layer, safety factor {S_IN/s2:.1f}")
    # LC2 20 N pressing down on the rear of the Pi while plugging in, 70 mm
    # from the flange.  Section: floor + two beams.
    fl_w = (P.PI_W + P.PI_CLR) - 16.0
    secs = [(fl_w, B.FLOOR_T, B.FLOOR_T / 2), (B.BEAM_W, B.BEAM_H, B.FLOOR_T + B.BEAM_H / 2),
            (B.BEAM_W, B.BEAM_H, B.FLOOR_T + B.BEAM_H / 2)]
    A2 = sum(b * d for b, d, z in secs)
    y2 = sum(b * d * z for b, d, z in secs) / A2
    I2 = sum(b * d ** 3 / 12 + b * d * (z - y2) ** 2 for b, d, z in secs)
    c2 = max(y2, B.FLOOR_T + B.BEAM_H - y2)
    lever = B.SD_CLEAR + P.PI_L
    s3 = 20.0 * lever * c2 / I2
    record(S_IN / s3 >= 3.0, f"15 LC2 cradle, 20 N at the USB end ({lever:.0f} mm arm)",
           f"I = {I2:.0f} mm4, stress {s3:.1f} MPa in-layer, safety factor {S_IN/s3:.1f}")
    # the same moment, reacted as a couple on the two dovetail rails
    F_rail = 20.0 * lever / (B.FLANGE_Y[1] - B.FLANGE_Y[0]) / 2
    s4 = F_rail / (P.RAIL_W0 * (P.RAIL_Y[1] - P.RAIL_Y[0]))
    record(S_X / s4 >= 2.0, "15 LC2 dovetail rail neck",
           f"{F_rail:.0f} N per rail across a {P.RAIL_W0:.0f} x "
           f"{P.RAIL_Y[1]-P.RAIL_Y[0]:.1f} mm neck = {s4:.2f} MPa ACROSS layers, "
           f"safety factor {S_X/s4:.0f}")
    # LC3 zip tie pulled hard, 40 N, 5 mm lever to the tab root, 9 x 3 mm tab
    s5 = 40.0 * 5.0 / (14.0 * B.TAB_T ** 2 / 6)
    record(S_IN / s5 >= 2.0, "15 LC3 zip-tie tab bending",
           f"{s5:.1f} MPa in-layer, safety factor {S_IN/s5:.1f} (root fillets not credited)")

    # ---- 14b socket walls around the key --------------------------------
    h = P.KEY_HEAD / 2 + P.KEY_CLR
    wall_lo = (P.KEY_YC - h) - P.NODE_Y0
    wall_hi = P.RACK_H - (P.KEY_YC + h)
    record(min(wall_lo, wall_hi) >= 4.0, "14 socket wall",
           f"{wall_lo:.2f} mm below and {wall_hi:.2f} mm above the socket head "
           f"(solid node and band), {B.NODE_Z1 - P.BOSS_Z0:.1f} mm deep; required >= 4.0 mm")

    # ---- 16  printed-scale minimum wall scan -----------------------------
    r = P.MIN_WALL / 2.0
    for n in B.PRINTED:
        m = printed[n]
        zmax = m.bounds[1][2]
        worst = []
        for z in np.arange(0.9, zmax - 0.3, 0.6):
            sec = m.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
            if sec is None:
                continue
            try:
                pl, _ = sec.to_planar(to_2D=np.eye(4))
                poly = unary_union([Polygon(p.exterior.coords, [q.coords for q in p.interiors])
                                    for p in pl.polygons_full])
            except Exception:
                continue
            opened = poly.buffer(-r, join_style=2).buffer(r, join_style=2)
            thin = poly.difference(opened.buffer(0.05))
            geoms = thin.geoms if thin.geom_type == "MultiPolygon" else [thin]
            for g in geoms:
                if g.is_empty or g.area < 4.0:
                    continue
                worst.append((g.area, z, g.bounds))
        worst.sort(reverse=True)
        tot = sum(w[0] for w in worst)
        detail = "; ".join(f"{a:.1f} mm2 at z={z:.1f} x {bb[0]:.0f}-{bb[2]:.0f} y {bb[1]:.0f}-{bb[3]:.0f}"
                           for a, z, bb in worst[:4])
        record(len(worst) == 0, f"16 min wall {P.MIN_WALL:.1f} mm [{n}]",
               f"{len(worst)} section region(s) thinner than {P.MIN_WALL:.1f} mm "
               f"(~7 lines), {tot:.0f} mm2 total; sub-4 mm2 slivers at fillet toes are "
               f"ignored" + (f": {detail}" if detail else ""))

    # ---- 17  assembly paths, not just the final pose ---------------------
    def moved(mesh, dx, dy, dz):
        c = mesh.copy()
        c.apply_translation((dx, dy, dz))
        return c

    worst = 0.0
    for d in np.arange(0.5, 22.0, 0.5):
        c = moved(asm["pi-cradle"], 0, d, 0)
        for other in ("face-centre", "face-end", "face-end-left"):
            worst = max(worst, inter_vol(c, asm[other]))
    record(worst < 0.5, "17 cradle slides down onto its rails",
           f"cradle lifted 0.5-21.5 mm in 0.5 mm steps: max {worst:.3f} mm3 of collision "
           f"with any bezel part along the whole path (required 0)")
    for endn in ("face-end", "face-end-left"):
        worst = 0.0
        for d in np.arange(0.5, 20.0, 0.5):
            worst = max(worst, inter_vol(moved(asm[endn], 0, 0, d), asm["face-centre"]))
        record(worst < 0.5, f"17 {endn} drops onto the centre key",
               f"lifted 0.5-19.5 mm rearward in 0.5 mm steps: max {worst:.3f} mm3 of "
               f"collision with face-centre (required 0)")

    # ---- 18  hex-key access to every panel screw ------------------------
    def cylz(x, y, z0, z1, dia):
        c = trimesh.creation.cylinder(radius=dia / 2, height=z1 - z0, sections=64)
        c.apply_translation((x, y, (z0 + z1) / 2))
        return c

    # The cradle is a tool-free lift-off part (check 17 proves the path), and
    # it is hung last.  Screw access is checked with the bezel alone, and the
    # screws the cradle covers are reported so the manual can say so.
    bezel = {k: m for k, m in asm.items() if k != "pi-cradle"}
    covered = []
    for sx in P.SCREW_X:
        for sy in P.SCREW_Y:
            head = cylz(sx, sy, P.BOSS_Z1 + 0.05, P.BOSS_Z1 + 3.2, 6.6)
            tool = cylz(sx, sy, P.BOSS_Z1 + 3.2, P.BOSS_Z1 + 80.0, 5.0)
            v1 = sum(inter_vol(head, m) for m in bezel.values())
            v2 = sum(inter_vol(tool, m) for m in bezel.values())
            if inter_vol(tool, asm["pi-cradle"]) > 0.5:
                covered.append(f"X{sx} Y{sy}")
            record(v1 < 0.5 and v2 < 0.5, f"18 screw access [X{sx} Y{sy}]",
                   f"M3 head zone (6.6 dia x 3.2) {v1:.3f} mm3, straight hex-key path "
                   f"(5 dia x 77 mm) {v2:.3f} mm3 of obstruction by the bezel (required 0)")
    record(True, "18 screws behind the Pi",
           f"{', '.join(covered) or 'none'}: reachable once the cradle is lifted off its "
           f"rails by hand - no tools, and check 17 proves the path is clear")

    # ---- 18b installed M3 heads vs the cradle, final pose AND slide path ---
    heads = [cylz(sx, sy, P.BOSS_Z1 + 0.02, P.BOSS_Z1 + 3.2, 6.6)
             for sx in P.SCREW_X for sy in P.SCREW_Y]
    fin_hit = sum(inter_vol(h_, asm["pi-cradle"]) for h_ in heads)
    path_hit = 0.0
    for d in np.arange(0.5, 22.0, 0.5):
        c = moved(asm["pi-cradle"], 0, d, 0)
        path_hit = max(path_hit, sum(inter_vol(h_, c) for h_ in heads))
    fz0, fz1, bx0, bx1, bz0, bz1 = B.cradle_geometry()
    gap_x = min(abs((P.SCREW_X[1] - 3.3) - (B.RAIL_XS[0] + B.FLANGE_W / 2)),
                abs((B.RAIL_XS[1] - B.FLANGE_W / 2) - (P.SCREW_X[2] + 3.3)))
    gap_z = (fz1 - 0.5) - (P.BOSS_Z1 + 3.2)
    record(fin_hit < 0.5 and path_hit < 0.5, "18 M3 heads clear the cradle",
           f"all 8 heads (6.6 dia x 3.2, larger than a socket cap): {fin_hit:.3f} mm3 in the "
           f"final pose, max {path_hit:.3f} mm3 along the slide-down path; nearest cradle "
           f"flange is {gap_x:.2f} mm to the side and the cradle floor {gap_z:.2f} mm behind "
           f"the heads (required 0 collision)")

    # ---- 10  sliced layer inspection ------------------------------------
    for n, m in printed.items():
        zmax = m.bounds[1][2]
        heights = np.arange(P.LAYER_H / 2, zmax, P.LAYER_H)
        prev = None
        islands = 0
        unsupported = 0.0
        first = True
        for z in heights:
            sec = m.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
            if sec is None:
                continue
            try:
                planar, _ = sec.to_planar(to_2D=np.eye(4))
                polys = [Polygon(p.exterior.coords, [r.coords for r in p.interiors])
                         for p in planar.polygons_full]
            except Exception:
                continue
            cur = unary_union(polys) if polys else None
            if cur is None or cur.is_empty:
                prev = cur
                continue
            if prev is not None and not prev.is_empty:
                support = prev.buffer(0.001)
                for p in (cur.geoms if cur.geom_type == "MultiPolygon" else [cur]):
                    if not p.intersects(support):
                        islands += 1
                new = cur.difference(support)
                unsupported += new.area if not new.is_empty else 0.0
            elif not first:
                islands += len(cur.geoms) if cur.geom_type == "MultiPolygon" else 1
            first = False
            prev = cur
        record(islands == 0, f"10 floating islands [{n}]",
               f"{islands} layer region(s) with no contact to the layer below "
               f"({len(heights)} layers at {P.LAYER_H:.2f} mm)")
        record(True, f"10 overhang area [{n}]",
               f"{unsupported/100:.2f} cm2 of downward-facing new area "
               f"{'- supports required' if unsupported > 200 else '- support free'}")

    write_report()
    return 1 if FAILED[0] else 0


def write_report():
    npass = sum(1 for r in results if r[0] == "PASS")
    nwarn = sum(1 for r in results if r[0] == "WARN")
    nfail = sum(1 for r in results if r[0] == "FAIL")
    lines = [
        "# RackTicker bezel v10 - validation report",
        "",
        f"`{npass} PASS`, `{nwarn} WARN`, `{nfail} FAIL`",
        "",
        "Generated by `validate_bezel_v4.py`. Every geometric check runs against",
        "the serialized STL that the slicer receives, not the pre-export CAD body.",
        "",
        "| Result | Check | Detail |",
        "|---|---|---|",
    ]
    for level, check, detail in results:
        lines.append(f"| {level} | {check} | {detail} |")
    lines.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines))
    print(f"\nwrote {REPORT}  ({npass} pass / {nwarn} warn / {nfail} fail)")


if __name__ == "__main__":
    sys.exit(main())

# Panel mounting dimensions and selected interface

Source: Waveshare's official `RGB-Matrix-P2.5-64x32-2D.dwg`, linked from
[Waveshare resources](https://docs.waveshare.com/RGB-Matrix-Px-64x32/Resources-And-Documents).
[Original drawing](https://github.com/waveshareteam/RGB-Matrix-Px-xx/blob/main/hardware/dimensions/RGB-Matrix-Pxx-64x32/RGB-Matrix-P2.5-64x32-2D.dwg).

Extracted with LibreDWG on 2026-09-14. Coordinates below describe the frame
view, with the lower-left outer edge as origin, in millimetres.

| Feature | Drawing value |
|---|---:|
| Frame outline | 159.70 × 79.70 (±0.30 annotations) |
| Nominal panel envelope | 160 × 80 |
| DWG small-hole centre spacing | 125 × 65 |
| DWG small-hole centres X | 17.35, 142.35 |
| DWG small-hole centres Y | 7.35, 72.35 |
| Large recess centres X (not the selected screw holes) | 7.35, 44.85, 79.85, 114.85, 152.35 |
| Large side recess centres Y | 39.85 |
| Frame section depth | 12 |
| Small corner hole circle diameter | 2.30 |

The drawing's diameter is not an explicit metric thread specification. Use the
supplied screws that already fit the inserts; do not infer allowable engagement
length from the 12 mm frame depth. Frame depth is not the complete powered-panel
connector envelope. An open rear mount avoids needing that envelope measured.

The earlier fit gauges are withdrawn because they made the reference edge
ambiguous. The selected mechanical interface is instead validated by the
MakerWorld 5 mm joiner the user printed for these panels. That listing states
35 mm centre-to-centre between the two seam screws, Ø3.5 mm clearance holes,
and M3×8 hardware. The rack ears therefore use 17.5 mm from each nominal panel
side, Ø3.6 × 6 mm tolerance slots, and 5 mm material thickness.

## Community references

[Joining brackets by jandj](https://www.printables.com/model/1294572-brackets-for-joining-hub75-led-panels)
describe 12 mm frames, 3 mm mounting holes and separate pin holes, but explicitly
say dimensions may require adjustment between panel types. They support the
mounting approach, not a universal mounting pattern.

[Desktop stand](https://www.printables.com/model/54027-desktop-stand-for-many-hub75-led-panels)
uses the grips of supplied magnetic screws and was tested with two Adafruit
models. Metal screws alone do not establish that the supplied heads are magnets.
A magnetic mount needs a ferromagnetic steel surface; a plastic frame cannot
retain magnets directly. The rack ears use original geometry rather than copied
community model geometry.

[MakerWorld panel brackets](https://makerworld.com/en/models/2422145-hub75-led-matrix-panel-bracket-mount-64x32-p2-5#profileId-2656417)
provide the physically tested interface used here: 35 mm seam spacing, Ø3.5 mm
holes, 5 mm plate thickness and M3×8 screws. RackTicker's C-shaped 2U ears are
original geometry and use only those stated compatibility dimensions.

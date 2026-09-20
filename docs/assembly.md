# Building a RackTicker

The whole build in order. The printing and mechanical fit are covered in detail in
[BEZEL_V10.md](../hardware/enclosure/BEZEL_V10.md); this page links each step to where it is
explained.

| The 2U face | Behind it |
| --- | --- |
| ![The printed 2U face](photos/front.jpg) | ![The Pi, adapter board and ADS-B receiver behind the face](photos/inside.jpg) |

## 1. Parts

The full list, with quantities, is in
[BEZEL_V10.md](../hardware/enclosure/BEZEL_V10.md#what-you-need): two Waveshare 64×32 P2.5
panels, a Raspberry Pi 3A+, an RGB matrix adapter board, a 5 V supply with a barrel plug
(sized for your panels; 4 A or more is a good start), a microSD card, eight M3 × 10 screws,
four rack screws, a zip tie and about 220 g of PLA. An RTL-SDR ADS-B stick is optional.

## 2. Print the face

Four parts: two identical face ends, a face centre and a Pi cradle. Use the ready-oriented
files in `hardware/enclosure/v10/3mf/`, and print the cradle with supports **off**. Settings
and the reasons are in [BEZEL_V10.md](../hardware/enclosure/BEZEL_V10.md#print).

## 3. Set up the Pi

Flash Raspberry Pi OS Lite (64-bit), then run the installer. Both steps are in
[Install on a Pi](../README.md#install-on-a-pi).

## 4. Assemble

Fit the panels into the face and the Pi into its cradle following
[Assemble in BEZEL_V10.md](../hardware/enclosure/BEZEL_V10.md#assemble). Connect the adapter
board and the panels as the markings on your boards and the photo above show.

## 5. First light

1. Power it on. The panels show the address to use, by name and by IP.
2. Open `http://<address>:8081/` (port 8081 on a Pi, not 8080) and set your home location under
   Settings.
3. Press **Settings → Developer tools → Colour test** and check each block matches its label.

If the colours are wrong or the panel flickers, see
[Panel tuning](../README.md#panel-tuning). The two usual fixes are `--led-rgb-sequence=RBG`
when green shows blue, and keeping the default `--led-limit-refresh=120`.

## 6. Handing it over

Before boxing a unit for someone else, use **Settings → Software → Reset → Ready to pass on**.
See [Passing one on](../README.md#passing-one-on-or-cloning-cards).

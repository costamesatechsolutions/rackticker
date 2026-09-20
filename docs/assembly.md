# Building a RackTicker

From a box of parts to a screen in a rack. About an afternoon, plus print time.
The printing and the mechanical fit are covered in detail in
[BEZEL_V10.md](../hardware/enclosure/BEZEL_V10.md); this page puts the whole build in order
and covers the electronics and first boot.

| The 2U face | Behind it |
| --- | --- |
| ![The printed 2U face](photos/front.jpg) | ![The Pi, adapter board and ADS-B receiver behind the face](photos/inside.jpg) |

## 1. Parts

The full list, with quantities, is in [BEZEL_V10.md](../hardware/enclosure/BEZEL_V10.md#what-you-need).
In short: two Waveshare 64×32 P2.5 panels, a Raspberry Pi 3A+, a WatangTech RGB Matrix
Adapter Board, a 5 V supply of 4 A or more with a barrel plug, a microSD card, eight
M3 × 10 screws, four rack screws, a zip tie and about 220 g of PLA. An RTL-SDR ADS-B stick
is optional.

## 2. Print the face

Four parts: two identical face ends, a face centre and a Pi cradle. Use the ready-oriented
files in `hardware/enclosure/v10/3mf/`. Do not rotate them, and print the cradle with
supports **off**. Settings and the reasons are in
[BEZEL_V10.md](../hardware/enclosure/BEZEL_V10.md#print).

Print while you do steps 3 and 4.

## 3. Prepare the card

1. Flash **Raspberry Pi OS Lite (64-bit)** with
   [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Under Edit Settings, set a
   hostname, a user and password, your Wi-Fi, and enable SSH.
2. Put the card in the Pi and fit the adapter board on its header.
3. Before it goes in the case, power it on the bench with the panels not yet attached, wait
   a minute, and confirm you can SSH in. This is far easier now than behind a rack face.
4. Run the installer from the [README](../README.md#install-on-a-pi). It builds the panel
   driver, reboots and installs the newest release (about ten minutes on a Pi 3A+).

## 4. Connect the panels

Wiring is the part most worth doing slowly, with the power off. Match connectors to the
labels printed on your boards.

- **Data:** a HUB75 ribbon runs from the adapter board to the first panel's input. A second
  ribbon runs from that panel's output to the second panel's input, so the two panels are
  chained side by side into one 128×32 screen. Ribbons are keyed; if one does not seat, it
  is the wrong way round.
- **Power:** each panel has its own 5 V power lead. The supply feeds the adapter board
  through its DC jack, which powers the Pi too. Check your adapter's documentation for how
  the panel power is taken from it, and do not power the panels from the Pi's USB port.
- **Supply size:** 4 A is enough for normal use; an 8 A brick leaves headroom at full
  brightness.

> Check the wiring against your adapter board's own documentation and photo before first
> power. This guide describes the arrangement the reference build uses ("regular" pin
> mapping on the WatangTech board); a different adapter needs different flags in
> `/etc/rackticker/matrix.conf` (see [Panel tuning](../README.md#panel-tuning)).

## 5. Assemble the face

Follow [Assemble in BEZEL_V10.md](../hardware/enclosure/BEZEL_V10.md#assemble). The order is:

1. Panels face down on a towel, butted at the seam.
2. Face centre on first, then the two face ends, screws loose.
3. Close the seam, check the gap around the screen is even, then tighten all eight screws by
   hand. Do not power-drive them: each has exactly 5 mm of thread in the panel.
4. Pi and adapter into the cradle with USB toward the open rear, one zip tie over the board.
5. Slide the cradle down its rails until it stops. It hangs by gravity; no screw.
6. Route the panel power leads through the two tie slots in the centre fin.

Connect the ribbons and power before step 5 if the cables are short, and again check them
after the cradle is on.

## 6. First light

1. Fit the face in the rack with the four corner screws and power it on.
2. The panels show the address to use, both the name and the IP address. Open
   `http://<address>:8081/` (port 8081 on a Pi, not 8080).
3. Set your home location under Settings. Weather, flights and Pixel Town's sea use it.
4. Press **Settings → Developer tools → Colour test** and check each block matches its label.

### If something looks wrong

| You see | Do this |
| --- | --- |
| GREEN shows blue, amber looks pink | The panels are wired RBG. Add `--led-rgb-sequence=RBG` to `/etc/rackticker/matrix.conf`, then `sudo systemctl restart rackticker-matrix` |
| Bursts of flicker | Keep the default `--led-limit-refresh=120`, or measure with `--led-show-refresh` and set it below your panel's lowest rate |
| Very dim colours drift pink | `--led-pwm-lsb-nanoseconds=160` is the default here; do not raise it |
| Only half the screen lights, or the halves are swapped | Check the chain: adapter to panel 1 input, panel 1 output to panel 2 input |
| Nothing on the panel, but the page works at the Pi's address | Power the panels from the supply, not from the Pi, and check the ribbon is seated |

## 7. Optional: planes overhead

Plug an RTL-SDR ADS-B stick into the Pi's USB port with its antenna where it can see the
sky. Without one, the Flights screen uses a free network feed.

## 8. Handing it over

Before boxing a unit for someone else, use **Settings → Software → Reset → Ready to pass
on**. See [Passing one on](../README.md#passing-one-on-or-cloning-cards).

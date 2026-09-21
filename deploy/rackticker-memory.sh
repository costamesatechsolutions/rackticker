#!/usr/bin/env bash
# Compressed swap in RAM for a small Pi.
#
#   rackticker-memory.sh start | stop
#
# A Pi 3A+ has 512 MB and the display, the web page, the plugins' process and the
# ADS-B feeders all live in it. When it runs short the kernel pushes memory out to the
# swap file on the SD card, and the next frame that needs a page of it waits for the
# card: a stall of a few hundred milliseconds to two seconds, in the middle of a crawl.
# zram is swap that is compressed memory: a page pushed out is microseconds away and
# takes a third of the room, and the SD card is only used if that fills too.
#
# Everything here is best effort: if the kernel has no zram, the Pi carries on as before.
set -u

device=/dev/zram0

start() {
  [[ -e /sys/block/zram0 ]] || modprobe zram 2>/dev/null || true
  [[ -e /sys/block/zram0 ]] || { echo "no zram in this kernel; leaving swap as it is"; return 0; }
  if grep -q "^$device " /proc/swaps; then
    return 0
  fi
  swapoff "$device" 2>/dev/null || true
  echo 1 > /sys/block/zram0/reset 2>/dev/null || true
  total_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  # Half the RAM's worth of swap: compressed about three to one, it costs about a sixth of it.
  echo "$(( total_kb * 1024 / 2 ))" > /sys/block/zram0/disksize || return 0
  mkswap -q "$device" >/dev/null 2>&1 || return 0
  # Ahead of the swap file on the card, which stays as the last resort.
  swapon --priority 100 "$device" || return 0
  # Pushing cold pages out is now cheap, so do it early and keep the working set in RAM;
  # and read one page at a time, since there is no seek to amortise.
  echo 100 > /proc/sys/vm/swappiness 2>/dev/null || true
  echo 0 > /proc/sys/vm/page-cluster 2>/dev/null || true
  echo "zram swap on: $(( total_kb / 2 / 1024 )) MB"
}

stop() {
  swapoff "$device" 2>/dev/null || true
  echo 1 > /sys/block/zram0/reset 2>/dev/null || true
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  *) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
exit 0

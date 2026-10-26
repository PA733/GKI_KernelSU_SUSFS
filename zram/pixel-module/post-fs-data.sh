#!/system/bin/sh
# KernelSU post-fs-data runs before mmd_setup (triggered at boot completion).
# Do not call setprop here: synchronous property handling can block init.
MODDIR=${0%/*}
STATUS="$MODDIR/last-status.txt"
ALGORITHMS=$(cat /sys/block/zram0/comp_algorithm 2>/dev/null)

case " $ALGORITHMS " in
  *" lz4kd "*|*" [lz4kd] "*) ;;
  *) echo "Stock ZRAM backend: no LZ4KD available; properties unchanged." > "$STATUS"; exit 0 ;;
esac

if [ "$(cat /sys/block/zram0/initstate 2>/dev/null)" != "0" ]; then
  echo "ZRAM already initialized; did not reset active swap." > "$STATUS"
  exit 0
fi

RESETPROP=$(command -v resetprop)
if [ -z "$RESETPROP" ] && [ -x /data/adb/ksu/bin/resetprop ]; then
  RESETPROP=/data/adb/ksu/bin/resetprop
fi
if [ -z "$RESETPROP" ]; then
  echo "resetprop unavailable; LZ4KD selection was not applied." > "$STATUS"
  exit 1
fi

"$RESETPROP" -n mmd.zram.comp_algorithm lz4kd || exit 1
echo lz4kd > /sys/block/zram0/comp_algorithm || exit 1
echo "LZ4KD selected before mmd_setup." > "$STATUS"

#!/usr/bin/env bash

set -euo pipefail

if (($# > 1)); then
  printf 'Usage: %s [timeout]\n' "$0" >&2
  exit 2
fi

if ! command -v stress-ng >/dev/null; then
  printf 'stress-ng is required. Install it with: sudo apt install stress-ng\n' >&2
  exit 1
fi

timeout=${1:-10m}
limit_c=70
temperature_path=/sys/class/thermal/thermal_zone0/temp

if [[ ! -r "$temperature_path" ]]; then
  printf 'Cannot read %s.\n' "$temperature_path" >&2
  exit 1
fi

stress-ng --cpu 0 --timeout "$timeout" &
load_pid=$!

cleanup() {
  kill -TERM "$load_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

while kill -0 "$load_pid" 2>/dev/null; do
  temperature_c=$(( $(<"$temperature_path") / 1000 ))
  printf '%s C\n' "$temperature_c"
  if ((temperature_c >= limit_c)); then
    printf 'Stopping at %s C.\n' "$temperature_c" >&2
    cleanup
    wait "$load_pid" || true
    exit 1
  fi
  sleep 1
done

wait "$load_pid"

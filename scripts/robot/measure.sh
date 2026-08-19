#!/usr/bin/env bash
# The compute-budget harness. apps/perception/README.md (refuted-claims row on
# the compute budget, and Stage 7).
#
#   measure.sh <label> [seconds]        e.g.  measure.sh live_perception 300
#
# WHY THIS EXISTS. docs/OPERATIONS.md §9 records that nobody has measured
# whether this stack fits, and risk 1 is that it does not — and that the failure takes the
# bridge with it, i.e. takes `stop_everything` with it. "It felt fine" is not a
# measurement, and neither is a number read after the fact against a threshold
# chosen after the fact. So this script PRINTS THE THRESHOLDS FIRST, then
# samples, then judges. The verdict is mechanical.
#
# It changes nothing: it starts no container, stops no container, claims no
# sensor. Run it alongside whatever stage is already up.
#
# The 16 GB on an Orin NX is UNIFIED CPU+GPU. The CUDA context, the TensorRT
# engine and the RealSense buffers all draw from the same pool that FAST-LIO's
# ikd-Tree grows into, so `RAM` from tegrastats is the whole budget, not the
# CPU's share of it.

set -euo pipefail
# `readlink -f` — see the note in run_c3po; this is invoked through a symlink
# on PATH, so BASH_SOURCE[0] is the link, not this file.
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=_common.sh
. "$here/_common.sh"

label="${1:-}"
duration="${2:-300}"
if [ -z "$label" ]; then
    err "usage: measure.sh <label> [seconds]"
    err "  e.g. measure.sh live_perception 300"
    exit 2
fi
case "$duration" in *[!0-9]*|'') err "seconds must be an integer"; exit 2 ;; esac

# --- the thresholds, stated before the run ---------------------------------
#
# Straight out of apps/perception/README.md Stage 7. Do not edit them to make a run pass; edit them
# only with the reason written down, because their whole value is being fixed
# in advance.

RAM_TOTAL_MB=15388          # tegrastats' own total on this part
RAM_FAIL_MB=11776           # 11.5 GB of 15.0, unified CPU+GPU
LFB_FAIL_MB=200             # free-block pool floor
LOAD_FAIL=8.0               # 1-min loadavg, sustained
CORE_HI_FAIL_PCT=50         # cores 5-7 must stay below this
TJ_FAIL_C=90                # sustained junction temperature
CLK_FAIL_MHZ=1400           # CPU clocks under load must not fall below this
EMC_WARN_PCT=60
EMC_FAIL_PCT=80
SWAP_FAIL_S=60              # vmstat si/so non-zero for this long
SUSTAIN_S=60                # what "sustained" means everywhere above

# Topic rates: FAIL below these (Hz).
RATE_MIN_LIVOX=9
RATE_MIN_ODOM=9
RATE_MIN_OBJECTS=10
RATE_MIN_SUMMARY=3.5
RATE_WINDOW_S="${C3PO_RATE_WINDOW_S:-12}"

sustain_n="$SUSTAIN_S"
if [ "$duration" -lt "$SUSTAIN_S" ]; then
    sustain_n="$duration"
fi

OUT_ROOT="${C3PO_MEASURE_DIR:-$LOG_DIR/measure}"
run_dir="$OUT_ROOT/${label}_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$run_dir"

say "Compute budget: $label"
info "duration: ${duration}s   samples: 1 Hz   output: $run_dir"
echo
say "Judging against these thresholds (fixed before the run):"
printf '  %-26s %s\n' "memory FAIL"    "peak RAM > ${RAM_FAIL_MB} MB of ${RAM_TOTAL_MB} MB (11.5 GB of 15.0, UNIFIED CPU+GPU)"
printf '  %-26s %s\n' ""              "or lfb free-block pool < ${LFB_FAIL_MB} MB"
printf '  %-26s %s\n' ""              "or vmstat si/so non-zero for ${SWAP_FAIL_S}s"
printf '  %-26s %s\n' "cpu FAIL"      "1-min loadavg > ${LOAD_FAIL} sustained"
printf '  %-26s %s\n' ""              "or any of cores 5-7 >= ${CORE_HI_FAIL_PCT}% sustained (bridge + OS live there)"
printf '  %-26s %s\n' "thermal FAIL"  "tj@ > ${TJ_FAIL_C} C sustained, or CPU clocks < ${CLK_FAIL_MHZ} MHz under load"
printf '  %-26s %s\n' "rates FAIL"    "/livox/lidar < ${RATE_MIN_LIVOX} Hz, /odom < ${RATE_MIN_ODOM} Hz,"
printf '  %-26s %s\n' ""              "/c3po/objects < ${RATE_MIN_OBJECTS} Hz, /c3po/world_summary < ${RATE_MIN_SUMMARY} Hz"
printf '  %-26s %s\n' "EMC"           "WARN > ${EMC_WARN_PCT}%, FAIL > ${EMC_FAIL_PCT}% sustained"
printf '  %-26s %s\n' "\"sustained\""   "${sustain_n} consecutive 1 Hz samples"
echo
info "two readings that need saying out loud, so they are not re-interpreted later:"
info "  * 'lfb' is judged on the POOL (block count x block size). The raw largest"
info "    block was 4 MB at the 2026-08-18 idle baseline, so a 200 MB threshold on"
info "    the block SIZE alone would fail an empty robot. Both are reported."
info "  * cores 5-7 is the stated threshold, but perception_up pins the vision"
info "    container to core 5. A core-5 breach may be that pin rather than a"
info "    budget breach — cores 6-7 are reported separately for that reason."
if [ "$duration" -lt "$SUSTAIN_S" ]; then
    warn "duration ${duration}s < ${SUSTAIN_S}s: 'sustained' degrades to ${sustain_n} samples"
    warn "a short run can only fail, never pass convincingly"
fi
echo

# --- what is up right now --------------------------------------------------

say "Context"
{
    echo "label: $label"
    echo "date: $(date -Is)"
    echo "duration_s: $duration"
    echo "uname: $(uname -a)"
    # `sudo -n`: this harness runs unattended for minutes; a password prompt
    # from a context line would hang the whole measurement.
    echo "nvpmodel: $(nvpmodel -q 2>/dev/null || sudo -n nvpmodel -q 2>/dev/null || echo unavailable)"
    echo "loadavg_at_start: $(cat /proc/loadavg)"
} > "$run_dir/context.txt"
sed 's/^/  /' "$run_dir/context.txt" || true

containers="$( { perception_running_containers; gemm_running; } | tr '\n' ' ' )"
info "containers: ${containers:-none}"
if bridge_running; then
    bridge_start_pid="$(bridge_pid)"
    ok "bridge running (pid $bridge_start_pid) — it must still be running at the end"
else
    bridge_start_pid=""
    warn "bridge NOT running — the OOM-direction check below is therefore vacuous"
fi
case "$(perception_stage)" in
    "") warn "no perception container: this measures the machine, not the stack" ;;
    *)  info "perception stage: $(perception_stage)" ;;
esac
echo

# --- collectors ------------------------------------------------------------

TEGRA_LOG="$run_dir/tegrastats.log"
LOAD_LOG="$run_dir/loadavg.log"
VMSTAT_LOG="$run_dir/vmstat.log"
DOCKER_LOG="$run_dir/docker_stats.log"

collector_pids=""
tegra_ok=0

cleanup() {
    local p
    for p in $collector_pids; do kill "$p" 2>/dev/null || true; done
    # tegrastats holds a singleton; killing the child is not always enough.
    # Only stop it if OURS is the instance that produced samples — an
    # unconditional --stop would silently stop somebody else's monitor.
    if [ "$tegra_ok" = "1" ]; then
        tegrastats --stop >/dev/null 2>&1 || sudo -n tegrastats --stop >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM

start_tegrastats() {
    tegrastats --interval 1000 > "$TEGRA_LOG" 2>"$run_dir/tegrastats.err" &
    collector_pids="$collector_pids $!"
    sleep 3
    [ -s "$TEGRA_LOG" ] && { tegra_ok=1; return 0; }
    warn "tegrastats produced nothing as \$USER — retrying with sudo"
    sudo -n tegrastats --interval 1000 >> "$TEGRA_LOG" 2>>"$run_dir/tegrastats.err" &
    collector_pids="$collector_pids $!"
    sleep 3
    [ -s "$TEGRA_LOG" ] && { tegra_ok=1; return 0; }
    warn "still nothing. tegrastats is a singleton — somebody else may hold it:"
    warn "  sudo tegrastats --stop"
    return 0
}

say "Sampling for ${duration}s"
start_tegrastats

( while :; do printf '%s %s\n' "$(date +%s)" "$(cat /proc/loadavg)"; sleep 1; done ) > "$LOAD_LOG" 2>/dev/null &
collector_pids="$collector_pids $!"

# vmstat's FIRST data row is averages since boot, not an instant — the analysis
# below drops it. Without that, a machine that swapped once a week ago reads as
# swapping now.
( vmstat 1 "$((duration + 2))" ) > "$VMSTAT_LOG" 2>/dev/null &
collector_pids="$collector_pids $!"

# `docker stats --no-stream` costs ~1 s per call, so it samples at 1/5 Hz. It is
# the only per-container number, and the per-container number is what says
# whether the OOM killer would take perception (correct) or the bridge (the
# failure this whole layout exists to prevent).
(
    while :; do
        _docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}' 2>/dev/null || true
        sleep 5
    done
) > "$DOCKER_LOG" 2>/dev/null &
collector_pids="$collector_pids $!"

# --- topic rates, measured inside the window -------------------------------
#
# Gated on the ACTUAL topics, in the nav container, on domain 42. A container
# that is up is not a stack that is publishing — that is the same mistake
# `docker start && ok` used to make in run_c3po.

RATES_FILE="$run_dir/rates.txt"
: > "$RATES_FILE"

measure_rate() {
    local topic="$1" hz
    if ! perception_running || ! _docker ps --format '{{.Names}}' 2>/dev/null | grep -qx c3po-perception-nav; then
        printf '%s unknown\n' "$topic" >> "$RATES_FILE"
        return 0
    fi
    # docker exec bypasses the entrypoint, so both overlays have to be sourced
    # by hand or ros2 cannot see fast_lio / c3po_perception at all.
    hz="$(_docker exec c3po-perception-nav bash -lc \
            "source /opt/ros/humble/setup.bash; source /opt/c3po/ws/install/setup.bash; \
             timeout ${RATE_WINDOW_S} ros2 topic hz '$topic' --window 20 2>/dev/null" \
          2>/dev/null | awk '/average rate/ {r=$NF} END {if (r != "") print r}' || true)"
    printf '%s %s\n' "$topic" "${hz:-unknown}" >> "$RATES_FILE"
}

sleep 5
for t in /livox/lidar /odom /c3po/objects /c3po/world_summary; do
    measure_rate "$t"
done

# Wait out the rest of the window.
elapsed=$(( 8 + 4 * RATE_WINDOW_S ))
remaining=$(( duration - elapsed ))
[ "$remaining" -lt 0 ] && remaining=0
info "topic rates sampled; ${remaining}s of load sampling left"
sleep "$remaining"

cleanup
collector_pids=""
sleep 1
ok "sampling done"
echo

# --- analysis --------------------------------------------------------------

fails=0
warns=0
unknowns=0

verdict() { # name status detail
    case "$2" in
        PASS)    ok   "$(printf '%-22s PASS    %s' "$1" "$3")" ;;
        WARN)    warn "$(printf '%-22s WARN    %s' "$1" "$3")"; warns=$((warns + 1)) ;;
        FAIL)    err  "$(printf '%-22s FAIL    %s' "$1" "$3")"; fails=$((fails + 1)) ;;
        UNKNOWN) warn "$(printf '%-22s UNKNOWN %s' "$1" "$3")"; unknowns=$((unknowns + 1)) ;;
    esac
}

# tegrastats: one pass, everything.
if [ "$tegra_ok" = "1" ] && [ -s "$TEGRA_LOG" ]; then
    eval "$(awk -v sustain="$sustain_n" -v corepct="$CORE_HI_FAIL_PCT" \
                -v tjfail="$TJ_FAIL_C" -v clkfail="$CLK_FAIL_MHZ" \
                -v emcwarn="$EMC_WARN_PCT" -v emcfail="$EMC_FAIL_PCT" '
    function runup(cond, cur, best) { return cond ? cur + 1 : 0 }
    {
        n++
        # RAM x/yMB
        if (match($0, /RAM [0-9]+\/[0-9]+MB/)) {
            s = substr($0, RSTART + 4, RLENGTH - 6); split(s, a, "/")
            if (a[1] + 0 > rampeak) rampeak = a[1] + 0
            ramtotal = a[2] + 0
        }
        # (lfb NxSMB)
        if (match($0, /lfb [0-9]+x[0-9]+MB/)) {
            s = substr($0, RSTART + 4, RLENGTH - 6); split(s, a, "x")
            pool = (a[1] + 0) * (a[2] + 0)
            if (lfbmin == "" || pool < lfbmin) lfbmin = pool
            if (blkmin == "" || a[2] + 0 < blkmin) blkmin = a[2] + 0
        }
        # SWAP x/yMB
        if (match($0, /SWAP [0-9]+\/[0-9]+MB/)) {
            s = substr($0, RSTART + 5, RLENGTH - 7); split(s, a, "/")
            if (a[1] + 0 > swappeak) swappeak = a[1] + 0
        }
        # CPU [p%@mhz,...]
        hi = 0; hi67 = 0; clksum = 0; clkn = 0
        if (match($0, /CPU \[[^]]*\]/)) {
            s = substr($0, RSTART + 5, RLENGTH - 6)
            ncore = split(s, c, ",")
            for (i = 1; i <= ncore; i++) {
                if (c[i] ~ /off/) continue
                split(c[i], f, "%@")
                pct = f[1] + 0; mhz = f[2] + 0
                if (i >= 6) { if (pct > hi) hi = pct; if (pct > coremax[i]) coremax[i] = pct }
                if (i >= 7) { if (pct > hi67) hi67 = pct }
                clksum += mhz; clkn++
            }
        }
        if (hi > himax) himax = hi
        if (hi67 > hi67max) hi67max = hi67
        hirun = runup(hi >= corepct, hirun); if (hirun > hirunmax) hirunmax = hirun
        hi67run = runup(hi67 >= corepct, hi67run); if (hi67run > hi67runmax) hi67runmax = hi67run
        if (clkn > 0) {
            clk = clksum / clkn
            if (clkmin == "" || clk < clkmin) clkmin = clk
            clkrun = runup(clk < clkfail, clkrun); if (clkrun > clkrunmax) clkrunmax = clkrun
        }
        # EMC_FREQ p%
        if (match($0, /EMC_FREQ [0-9]+%/)) {
            emc = substr($0, RSTART + 9, RLENGTH - 10) + 0
            if (emc > emcmax) emcmax = emc
            ew = runup(emc > emcwarn, ew); if (ew > ewmax) ewmax = ew
            ef = runup(emc > emcfail, ef); if (ef > efmax) efmax = ef
        }
        # GR3D_FREQ p%
        if (match($0, /GR3D_FREQ [0-9]+%/)) {
            g = substr($0, RSTART + 10, RLENGTH - 11) + 0
            if (g > gpumax) gpumax = g
        }
        # tj@nnC
        if (match($0, /tj@[0-9.]+C/)) {
            tj = substr($0, RSTART + 3, RLENGTH - 4) + 0
            if (tj > tjmax) tjmax = tj
            tr = runup(tj > tjfail, tr); if (tr > trmax) trmax = tr
        }
    }
    END {
        printf "TG_N=%d\n", n
        printf "TG_RAM_PEAK=%d\n", rampeak
        printf "TG_RAM_TOTAL=%d\n", ramtotal
        printf "TG_LFB_POOL_MIN=%d\n", lfbmin + 0
        printf "TG_LFB_BLOCK_MIN=%d\n", blkmin + 0
        printf "TG_SWAP_PEAK=%d\n", swappeak + 0
        printf "TG_CORE57_MAX=%d\n", himax + 0
        printf "TG_CORE57_RUN=%d\n", hirunmax + 0
        printf "TG_CORE67_MAX=%d\n", hi67max + 0
        printf "TG_CORE67_RUN=%d\n", hi67runmax + 0
        printf "TG_CORE5_MAX=%d\n", coremax[6] + 0
        printf "TG_CORE6_MAX=%d\n", coremax[7] + 0
        printf "TG_CORE7_MAX=%d\n", coremax[8] + 0
        printf "TG_CLK_MIN=%d\n", clkmin + 0
        printf "TG_CLK_RUN=%d\n", clkrunmax + 0
        printf "TG_EMC_MAX=%d\n", emcmax + 0
        printf "TG_EMC_WARN_RUN=%d\n", ewmax + 0
        printf "TG_EMC_FAIL_RUN=%d\n", efmax + 0
        printf "TG_GPU_MAX=%d\n", gpumax + 0
        printf "TG_TJ_MAX=%.1f\n", tjmax + 0
        printf "TG_TJ_RUN=%d\n", trmax + 0
    }' "$TEGRA_LOG")"
else
    TG_N=0
fi

say "Verdict"

if [ "${TG_N:-0}" -gt 0 ]; then
    verdict "RAM peak" \
        "$([ "$TG_RAM_PEAK" -gt "$RAM_FAIL_MB" ] && echo FAIL || echo PASS)" \
        "${TG_RAM_PEAK} MB of ${TG_RAM_TOTAL} MB (limit ${RAM_FAIL_MB}, unified CPU+GPU)"

    verdict "lfb pool" \
        "$([ "$TG_LFB_POOL_MIN" -lt "$LFB_FAIL_MB" ] && echo FAIL || echo PASS)" \
        "min ${TG_LFB_POOL_MIN} MB (limit ${LFB_FAIL_MB}); smallest largest-block ${TG_LFB_BLOCK_MIN} MB"

    verdict "cores 6-7" \
        "$([ "$TG_CORE67_RUN" -ge "$sustain_n" ] && echo FAIL || echo PASS)" \
        "peak ${TG_CORE67_MAX}%, longest run >= ${CORE_HI_FAIL_PCT}%: ${TG_CORE67_RUN}s (limit ${sustain_n}s)"

    if [ "$TG_CORE57_RUN" -ge "$sustain_n" ] && [ "$TG_CORE67_RUN" -lt "$sustain_n" ]; then
        verdict "cores 5-7 (stated)" "WARN" \
            "core 5 alone breached (${TG_CORE5_MAX}%) — that is where perception_up pins the vision container"
    else
        verdict "cores 5-7 (stated)" \
            "$([ "$TG_CORE57_RUN" -ge "$sustain_n" ] && echo FAIL || echo PASS)" \
            "c5 ${TG_CORE5_MAX}%  c6 ${TG_CORE6_MAX}%  c7 ${TG_CORE7_MAX}%  longest run ${TG_CORE57_RUN}s"
    fi

    verdict "tj" \
        "$([ "$TG_TJ_RUN" -ge "$sustain_n" ] && echo FAIL || echo PASS)" \
        "max ${TG_TJ_MAX} C, longest run > ${TJ_FAIL_C} C: ${TG_TJ_RUN}s"

    verdict "CPU clocks" \
        "$([ "$TG_CLK_RUN" -ge "$sustain_n" ] && echo FAIL || echo PASS)" \
        "min mean ${TG_CLK_MIN} MHz, longest run < ${CLK_FAIL_MHZ} MHz: ${TG_CLK_RUN}s"

    if [ "$TG_EMC_FAIL_RUN" -ge "$sustain_n" ]; then
        verdict "EMC bandwidth" "FAIL" "peak ${TG_EMC_MAX}%, > ${EMC_FAIL_PCT}% for ${TG_EMC_FAIL_RUN}s"
    elif [ "$TG_EMC_WARN_RUN" -ge "$sustain_n" ]; then
        verdict "EMC bandwidth" "WARN" "peak ${TG_EMC_MAX}%, > ${EMC_WARN_PCT}% for ${TG_EMC_WARN_RUN}s — this fails as jitter, not as an error"
    else
        verdict "EMC bandwidth" "PASS" "peak ${TG_EMC_MAX}%"
    fi

    info "GPU peak ${TG_GPU_MAX}%, swap peak ${TG_SWAP_PEAK} MB, ${TG_N} tegrastats samples"
else
    verdict "tegrastats" "UNKNOWN" "no samples — memory, thermal, EMC and clocks were NOT measured"
fi

# loadavg
if [ -s "$LOAD_LOG" ]; then
    eval "$(awk -v lim="$LOAD_FAIL" '
        { l = $2 + 0
          if (l > max) max = l
          run = (l > lim) ? run + 1 : 0
          if (run > runmax) runmax = run
          n++ }
        END { printf "LOAD_MAX=%.2f\nLOAD_RUN=%d\nLOAD_N=%d\n", max, runmax + 0, n }' "$LOAD_LOG")"
    verdict "loadavg (1 min)" \
        "$([ "$LOAD_RUN" -ge "$sustain_n" ] && echo FAIL || echo PASS)" \
        "max ${LOAD_MAX} (limit ${LOAD_FAIL}), longest run above: ${LOAD_RUN}s"
else
    verdict "loadavg (1 min)" "UNKNOWN" "no samples"
fi

# vmstat si/so — swap ACTIVITY, which is what hurts; swap OCCUPANCY alone does not.
if [ -s "$VMSTAT_LOG" ]; then
    eval "$(awk '
        /^[[:space:]]*[0-9]/ {
            n++
            if (n == 1) next          # since-boot averages, not an instant
            act = ($7 + 0 > 0 || $8 + 0 > 0)
            run = act ? run + 1 : 0
            if (run > runmax) runmax = run
            if (act) any++
        }
        END { printf "VM_RUN=%d\nVM_ANY=%d\nVM_N=%d\n", runmax + 0, any + 0, n + 0 }' "$VMSTAT_LOG")"
    if [ "$VM_RUN" -ge "$SWAP_FAIL_S" ]; then
        verdict "swap activity" "FAIL" "si/so non-zero for ${VM_RUN}s (limit ${SWAP_FAIL_S}s)"
    elif [ "$VM_ANY" -gt 0 ]; then
        verdict "swap activity" "WARN" "si/so non-zero in ${VM_ANY} of ${VM_N} samples, never for ${SWAP_FAIL_S}s straight"
    else
        verdict "swap activity" "PASS" "no swap in or out at all"
    fi
else
    verdict "swap activity" "UNKNOWN" "no vmstat samples"
fi

# topic rates
rate_verdict() { # topic min
    local topic="$1" min="$2" hz
    hz="$(awk -v t="$topic" '$1 == t { v = $2 } END { print (v == "" ? "unknown" : v) }' "$RATES_FILE")"
    if [ "$hz" = "unknown" ]; then
        verdict "$topic" "UNKNOWN" "no rate (container down, or nothing is publishing it)"
        return
    fi
    if awk -v a="$hz" -v b="$min" 'BEGIN { exit !(a + 0 < b + 0) }'; then
        verdict "$topic" "FAIL" "${hz} Hz (floor ${min} Hz)"
    else
        verdict "$topic" "PASS" "${hz} Hz (floor ${min} Hz)"
    fi
}
rate_verdict /livox/lidar        "$RATE_MIN_LIVOX"
rate_verdict /odom               "$RATE_MIN_ODOM"
rate_verdict /c3po/objects       "$RATE_MIN_OBJECTS"
rate_verdict /c3po/world_summary "$RATE_MIN_SUMMARY"

# per-container, and the direction-of-failure check
if [ -s "$DOCKER_LOG" ]; then
    say "Per container (peaks)"
    awk -F'|' '
        NF >= 4 {
            split($3, m, " ")
            v = m[1]; u = v
            sub(/[0-9.]+/, "", u); sub(/[A-Za-z]+$/, "", v)
            mb = v + 0
            if (u ~ /GiB|GB/) mb = mb * 1024
            else if (u ~ /KiB|kB/) mb = mb / 1024
            cpu = $2 + 0
            pct = $4 + 0
            if (mb > maxmb[$1]) maxmb[$1] = mb
            if (cpu > maxcpu[$1]) maxcpu[$1] = cpu
            if (pct > maxpct[$1]) maxpct[$1] = pct
        }
        END { for (c in maxmb) printf "  %-28s mem %8.0f MiB (%.0f%% of its limit)  cpu %.0f%%\n", c, maxmb[c], maxpct[c], maxcpu[c] }
    ' "$DOCKER_LOG" | sort
    hot="$(awk -F'|' 'NF >= 4 && $4 + 0 > 90 { print $1 }' "$DOCKER_LOG" | sort -u | tr '\n' ' ')"
    if [ -n "$hot" ]; then
        verdict "container mem limit" "WARN" "within 10% of its --memory limit: $hot"
    fi
else
    info "no docker stats samples"
fi

# The whole point of --cpuset-cpus / --memory on the containers is that when the
# budget is blown the OOM killer takes perception and never the process that
# owns stop_everything. Check the direction, not just the outcome.
if [ -n "$bridge_start_pid" ]; then
    if bridge_running && [ "$(bridge_pid)" = "$bridge_start_pid" ]; then
        verdict "bridge survived" "PASS" "still pid $bridge_start_pid"
    else
        verdict "bridge survived" "FAIL" "the bridge died or restarted during the run — the OOM killer took the WRONG process"
    fi
fi

echo
info "raw samples: $run_dir"
if [ "$fails" -gt 0 ]; then
    err "$fails FAIL, $warns WARN, $unknowns UNKNOWN"
    err "if memory or rates failed: YOLO input 640->480, detector to 5 Hz, and confirm"
    err "map_en: false — before concluding the stack does not fit"
    exit 1
elif [ "$unknowns" -gt 0 ]; then
    warn "0 FAIL, $warns WARN, $unknowns UNKNOWN — an unmeasured threshold is not a passed one"
    exit 2
else
    ok "all thresholds met ($warns WARN)"
fi

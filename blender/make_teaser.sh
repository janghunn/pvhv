#!/usr/bin/env bash
# Regenerate the project-page teaser (blender/out/teaser.webp) headless.
#
#   blender/make_teaser.sh            stages 1-3, replaces out/teaser.webp
#   blender/make_teaser.sh --check    stages 1-3 into build/check/, compares with out/teaser.webp
#
# Environment (see README):
#   BLENDER              Blender 5.2.2 binary   (default ~/.local/opt/blender-5.2.2-linux-x64/blender)
#   PVHV_PYTHON          python of the conda env "pvhv"   (default: python on PATH)
#   PVHV_MODELS_CACHE    fetched model cache root          (default <repo>/models/cache)
#   PVHV_EGL_DEVICE      EGL device of the renderer         (default 0)
#   CYCLES_DEVICE_INDEX  Cycles GPU, index in PCI bus order (default 0); CUDA_VISIBLE_DEVICES is honoured
#   ALLOW_OTHER_BLENDER  set to 1 to run with a Blender other than 5.2.2 (output will differ)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
BLENDER="${BLENDER:-$HOME/.local/opt/blender-5.2.2-linux-x64/blender}"
PVHV_PYTHON="${PVHV_PYTHON:-python}"
export PVHV_MODELS_CACHE="${PVHV_MODELS_CACHE:-$REPO/models/cache}"
export PVHV_EGL_DEVICE="${PVHV_EGL_DEVICE:-0}"
export CYCLES_DEVICE_INDEX="${CYCLES_DEVICE_INDEX:-0}"
export CUDA_DEVICE_ORDER=PCI_BUS_ID

CHECK=0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "error: unknown argument '$arg'" >&2; exit 2 ;;
    esac
done

OBJ="$PVHV_MODELS_CACHE/sanmiguel/San_Miguel/san-miguel-low-poly.obj"
if [ ! -f "$OBJ" ]; then
    echo "error: $OBJ not found - fetch the models first: python models/fetch_models.py" \
         "(or point PVHV_MODELS_CACHE at an existing cache)" >&2
    exit 1
fi
if [ ! -x "$BLENDER" ]; then
    echo "error: Blender not found at $BLENDER - run blender/install_blender.sh or set BLENDER" >&2
    exit 1
fi
BVER="$("$BLENDER" --version 2>/dev/null | head -n 1)"
if [[ "$BVER" != "Blender 5.2.2"* ]]; then
    if [ "${ALLOW_OTHER_BLENDER:-0}" = 1 ]; then
        echo "warning: '$BVER' is not Blender 5.2.2; the output will not match out/teaser.webp" >&2
    else
        echo "error: '$BVER' is not Blender 5.2.2 (set ALLOW_OTHER_BLENDER=1 to run anyway)" >&2
        exit 1
    fi
fi

if [ "$CHECK" = 1 ]; then
    BUILD="$HERE/build/check"
    WEBP="$BUILD/teaser.webp"
    COMMITTED="$HERE/out/teaser.webp"
    [ -f "$COMMITTED" ] || { echo "error: $COMMITTED missing, nothing to check against" >&2; exit 1; }
else
    BUILD="$HERE/build"
    WEBP="$HERE/out/teaser.webp"
fi
mkdir -p "$BUILD"
LAYERS="$BUILD/layer1_1920x1080.npz"
BEAUTY="$BUILD/beauty.png"
FULL="$BUILD/teaser_full.png"

now() { date +%s.%N; }
elapsed() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }
T0="$(now)"

echo "== stage 1/3: hidden layer h=1 (renderer, EGL device $PVHV_EGL_DEVICE)"
rm -f "$LAYERS"
t="$(now)"
"$PVHV_PYTHON" "$HERE/export_layers.py" --width 1920 --height 1080 --out "$LAYERS" \
    --egl-device "$PVHV_EGL_DEVICE"
[ -s "$LAYERS" ] || { echo "error: stage 1 wrote no $LAYERS" >&2; exit 1; }
S1="$(elapsed "$t")"

echo "== stage 2/3: Cycles render (Blender, GPU index $CYCLES_DEVICE_INDEX)"
rm -f "$BEAUTY"
t="$(now)"
"$BLENDER" -b --factory-startup --python-exit-code 1 -P "$HERE/scene_teaser.py" -- \
    --models-cache "$PVHV_MODELS_CACHE" --out "$BEAUTY" --device-index "$CYCLES_DEVICE_INDEX" \
    | grep -v '^Fra:'
[ -s "$BEAUTY" ] || { echo "error: stage 2 wrote no $BEAUTY" >&2; exit 1; }
S2="$(elapsed "$t")"

echo "== stage 3/3: composite"
rm -f "$FULL"
t="$(now)"
status=0
if [ "$CHECK" = 1 ]; then
    rm -f "$WEBP"
    "$PVHV_PYTHON" "$HERE/composite.py" --beauty "$BEAUTY" --layers "$LAYERS" --out-png "$FULL" \
        --out-webp "$WEBP" --check "$COMMITTED" || status=$?
else
    "$PVHV_PYTHON" "$HERE/composite.py" --beauty "$BEAUTY" --layers "$LAYERS" --out-png "$FULL" \
        --out-webp "$WEBP"
fi
S3="$(elapsed "$t")"
echo "timings: stage1 ${S1}s  stage2 ${S2}s  stage3 ${S3}s  total $(elapsed "$T0")s"
exit "$status"

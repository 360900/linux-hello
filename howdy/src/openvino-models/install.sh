#!/bin/bash
# Download OpenVINO face detection and re-identification models for howdy
# These models run on Intel iGPU via OpenVINO for fast face unlock

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="/usr/share/howdy/openvino-models"
CACHE_DIR="/var/cache/howdy/openvino"
BASE_URL="https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/models_bin/1"

# sha256 checksums of the model files (open_model_zoo 2023.0, FP16)
declare -A CHECKSUMS=(
    ["face-detection-adas-0001.xml"]="59ccfd224324a5417210ce863909ae265c89548b19a85364470935201c5176ef"
    ["face-detection-adas-0001.bin"]="10d3d424905ddef043b16fa6c036b7aa14c5193ae2c470699799746d7d6def37"
    ["face-reidentification-retail-0095.xml"]="ce53d2c9c08c0bd1c1660fb8a5b6d0e3e4ec19eb92f1036d2d83a85e83082dce"
    ["face-reidentification-retail-0095.bin"]="241229ca3d206321868d46ce74a3c0b06c49cea58db7dc70b2e842ff287545d1"
)

verify() {
    local file="$1"
    if ! echo "${CHECKSUMS[$file]}  $SCRIPT_DIR/$file" | sha256sum --check --status; then
        echo "ERROR: checksum mismatch for $file, aborting (the download may be corrupted or tampered with)"
        exit 1
    fi
}

echo "Downloading OpenVINO face detection model (FP16)..."
curl -fL --retry 3 -o "$SCRIPT_DIR/face-detection-adas-0001.xml" \
    "$BASE_URL/face-detection-adas-0001/FP16/face-detection-adas-0001.xml"
curl -fL --retry 3 -o "$SCRIPT_DIR/face-detection-adas-0001.bin" \
    "$BASE_URL/face-detection-adas-0001/FP16/face-detection-adas-0001.bin"
verify "face-detection-adas-0001.xml"
verify "face-detection-adas-0001.bin"

echo "Downloading OpenVINO face re-identification model (FP16)..."
curl -fL --retry 3 -o "$SCRIPT_DIR/face-reidentification-retail-0095.xml" \
    "$BASE_URL/face-reidentification-retail-0095/FP16/face-reidentification-retail-0095.xml"
curl -fL --retry 3 -o "$SCRIPT_DIR/face-reidentification-retail-0095.bin" \
    "$BASE_URL/face-reidentification-retail-0095/FP16/face-reidentification-retail-0095.bin"
verify "face-reidentification-retail-0095.xml"
verify "face-reidentification-retail-0095.bin"

echo ""
echo "Installing models to $MODEL_DIR..."
mkdir -p "$MODEL_DIR"
cp "$SCRIPT_DIR"/*.xml "$SCRIPT_DIR"/*.bin "$MODEL_DIR/"

echo "Creating model cache directory at $CACHE_DIR..."
mkdir -p "$CACHE_DIR"
chmod 755 "$CACHE_DIR"

echo ""
echo "Done! OpenVINO models installed."
echo "The GPU model cache will be populated on first run (~10s), then loads in <500ms."

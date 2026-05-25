#!/usr/bin/env bash
set -euo pipefail

# CastAdhan Portable Installer v3.2
# Installs the standalone app.py + console.html + config.yaml bundle.
# Sources:
#   A) A directory containing the extracted files (default: current folder)
#   B) A .zip or .tar.gz bundle containing the same structure
#
# What this installer does:
#   - Copies app.py, console.html, config.yaml, requirements.txt to install dir
#   - Prompts for location (city/country/lat/lon) and patches config.yaml
#   - Prompts for required adhan audio file; copies optional audio files if provided
#   - Creates Python virtualenv and installs dependencies
#   - Verifies startup and /health endpoint
#   - Optionally installs systemd service for auto-start on boot

# ──────────────────────────────────────────────────────────────
# UI helpers
# ──────────────────────────────────────────────────────────────
bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "\033[0;32m%s\033[0m\n" "$*"; }
info()  { printf "[INFO] %s\n" "$*"; }
warn()  { printf "[WARN] %s\n" "$*" >&2; }
err()   { printf "[ERROR] %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1 — please install it first."; }

ask() {
  local prompt="$1" default="${2:-}"
  local ans
  if [ -n "$default" ]; then
    read -r -p "$prompt [$default]: " ans
    echo "${ans:-$default}"
  else
    read -r -p "$prompt: " ans
    echo "$ans"
  fi
}

yesno() {
  local prompt="$1" default="${2:-N}" ans
  read -r -p "$prompt (y/n) [$default]: " ans
  ans="${ans:-$default}"
  case "${ans,,}" in y|yes) return 0 ;; *) return 1 ;; esac
}

# ──────────────────────────────────────────────────────────────
# Source extraction
# ──────────────────────────────────────────────────────────────
is_zip() { [[ "${1,,}" == *.zip ]]; }
is_tgz() { [[ "${1,,}" == *.tar.gz || "${1,,}" == *.tgz ]]; }

# Copy core files from a source directory into WORKDIR/core
copy_from_dir() {
  local srcdir="$1" outdir="$2"
  info "Copying files from: $srcdir"
  [ -d "$srcdir" ] || die "Source directory not found: $srcdir"

  local required=("app.py" "console.html" "config.yaml")
  for f in "${required[@]}"; do
    [ -f "$srcdir/$f" ] || die "Missing required file in source: $srcdir/$f"
    cp -f "$srcdir/$f" "$outdir/$f"
  done
  # Copy requirements.txt if present
  if [ -f "$srcdir/requirements.txt" ]; then
    cp -f "$srcdir/requirements.txt" "$outdir/requirements.txt"
  fi
  info "Core files copied."
}

# Extract from zip or tar.gz into WORKDIR/core
extract_from_archive() {
  local archive="$1" outdir="$2"
  [ -f "$archive" ] || die "Archive not found: $archive"
  mkdir -p "$outdir"

  if is_zip "$archive"; then
    need_cmd unzip
    info "Extracting ZIP: $archive"
    unzip -o -q "$archive" -d "$outdir"
  elif is_tgz "$archive"; then
    need_cmd tar
    info "Extracting TAR.GZ: $archive"
    tar -xzf "$archive" -C "$outdir"
  else
    die "Unsupported archive type: $archive (expected .zip or .tar.gz)"
  fi
  info "Archive extracted to: $outdir"
}

# ──────────────────────────────────────────────────────────────
# Config patching (no yq dependency — pure sed/grep)
# ──────────────────────────────────────────────────────────────
patch_config_value() {
  # Replace a YAML key's value on its own line (works for simple scalar values)
  local file="$1" key="$2" value="$3"
  if grep -qE "^([[:space:]]*)${key}:" "$file"; then
    sed -i -E "s|^([[:space:]]*)${key}:.*|\1${key}: ${value}|g" "$file"
  fi
}

patch_config_yaml() {
  local cfg="$1"
  [ -f "$cfg" ] || die "config.yaml not found at $cfg"
  info "Patching config.yaml with your location..."

  patch_config_value "$cfg" "city"      "\"$CONFIG_CITY\""
  patch_config_value "$cfg" "country"   "\"$CONFIG_COUNTRY\""
  patch_config_value "$cfg" "latitude"  "$CONFIG_LAT"
  patch_config_value "$cfg" "longitude" "$CONFIG_LON"
  patch_config_value "$cfg" "timezone"  "\"$CONFIG_TZ\""

  # If user wants all-speakers mode (no name filter), clear include_if_name_contains
  if [ "${ALL_SPEAKERS:-N}" = "Y" ]; then
    patch_config_value "$cfg" "include_if_name_contains" '""'
    info "  → All-speakers mode: include_if_name_contains cleared"
  fi
}

# ──────────────────────────────────────────────────────────────
# Banner
# ──────────────────────────────────────────────────────────────
bold ""
bold "╔══════════════════════════════════════════╗"
bold "║   CASTADHAN PORTABLE INSTALLER  v3.2    ║"
bold "╚══════════════════════════════════════════╝"
bold ""
info "This installer will set up CastAdhan on this machine."
info "Requirements: Python 3.9+, network access, Google Cast speakers on same WiFi."
bold ""

# ──────────────────────────────────────────────────────────────
# Pre-flight
# ──────────────────────────────────────────────────────────────
need_cmd python3
need_cmd curl
need_cmd sed
need_cmd awk

# ──────────────────────────────────────────────────────────────
# Choose source
# ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bold "── Step 1: Source ──────────────────────────"
info "Where are the CastAdhan files? (default: this folder)"
SOURCE_PATH="$(ask "Source path (folder, .zip, or .tar.gz)" "$SCRIPT_DIR")"
SOURCE_PATH="${SOURCE_PATH/#\~/$HOME}"

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "$WORKDIR" || true; }
trap cleanup EXIT
mkdir -p "$WORKDIR/core"

if [ -d "$SOURCE_PATH" ]; then
  copy_from_dir "$SOURCE_PATH" "$WORKDIR/core"
elif is_zip "$SOURCE_PATH" || is_tgz "$SOURCE_PATH"; then
  extract_from_archive "$SOURCE_PATH" "$WORKDIR/core"
  # Handle potential nested folder from zip
  if [ ! -f "$WORKDIR/core/app.py" ]; then
    nested="$(find "$WORKDIR/core" -name "app.py" -maxdepth 2 | head -1)"
    [ -n "$nested" ] || die "Could not find app.py inside archive."
    cp "$(dirname "$nested")/app.py"       "$WORKDIR/core/app.py"
    cp "$(dirname "$nested")/console.html" "$WORKDIR/core/console.html"
    cp "$(dirname "$nested")/config.yaml"  "$WORKDIR/core/config.yaml"
    [ -f "$(dirname "$nested")/requirements.txt" ] && \
      cp "$(dirname "$nested")/requirements.txt" "$WORKDIR/core/requirements.txt" || true
  fi
else
  die "Source must be a directory, .zip, or .tar.gz: $SOURCE_PATH"
fi

# Validate
for f in app.py console.html config.yaml; do
  [ -s "$WORKDIR/core/$f" ] || die "Missing or empty extracted file: $f"
done
green "  ✓ Core files verified"

# ──────────────────────────────────────────────────────────────
# Install directory
# ──────────────────────────────────────────────────────────────
bold ""
bold "── Step 2: Install location ────────────────"
default_install="/home/$USER/castadhan"
INSTALL_DIR="$(ask "Install directory" "$default_install")"
INSTALL_DIR="${INSTALL_DIR%/}"

if [ -d "$INSTALL_DIR" ] && [ "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
  warn "Install directory is not empty: $INSTALL_DIR"
  if yesno "Back up existing directory and continue?" "Y"; then
    bak="${INSTALL_DIR}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    info "Backing up to: $bak"
    mv "$INSTALL_DIR" "$bak"
  else
    die "Aborted."
  fi
fi

mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/audio"

# ──────────────────────────────────────────────────────────────
# Location configuration
# ──────────────────────────────────────────────────────────────
bold ""
bold "── Step 3: Your location ───────────────────"
info "This is used to calculate accurate prayer times."
CONFIG_CITY="$(ask    "City name"       "London")"
CONFIG_COUNTRY="$(ask "Country"         "United Kingdom")"
CONFIG_LAT="$(ask     "Latitude"        "51.5074")"
CONFIG_LON="$(ask     "Longitude"       "-0.1278")"
CONFIG_TZ="$(ask      "Timezone (TZ DB)" "Europe/London")"

if yesno "Cast to ALL speakers on the network (recommended for portable use)?" "Y"; then
  ALL_SPEAKERS="Y"
else
  ALL_SPEAKERS="N"
fi

# ──────────────────────────────────────────────────────────────
# Audio files
# ──────────────────────────────────────────────────────────────
bold ""
bold "── Step 4: Audio files ─────────────────────"
info "Bundled audio files are copied automatically."
info "Any missing optional files can be provided now or added later."
bold ""

# Helper: copy from bundle if present
copy_audio_if_exists() {
  local src_audio="$SOURCE_PATH/audio" fname="$1"
  if [ -f "$src_audio/$fname" ]; then
    cp -f "$src_audio/$fname" "$INSTALL_DIR/audio/$fname"
    green "  ✓ $fname (from bundle)"
    return 0
  fi
  return 1
}

# Required audio files — auto-copy from bundle, ask only if missing
REQUIRED_AUDIO=("adhan.mp3" "fajr_warning.mp3" "asr_warning.mp3" "maghrib_warning.mp3")
for audio_file in "${REQUIRED_AUDIO[@]}"; do
  if ! copy_audio_if_exists "$audio_file"; then
    warn "$audio_file not found in bundle — please provide it."
    src="$(ask "    Path to $audio_file (.mp3/.wav/.m4a)" "")"
    [ -n "$src" ] || die "$audio_file is required and was not provided."
    src="${src/#\~/$HOME}"
    [ -f "$src" ] || die "File not found: $src"
    cp -f "$src" "$INSTALL_DIR/audio/$audio_file"
    green "  ✓ $audio_file copied"
  fi
done

# Optional audio files — auto-copy from bundle, prompt if missing
OPTIONAL_AUDIO=("morning_dhikr.mp3" "evening_dhikr.mp3" "surah_kahf.mp3" "suhoor_alarm.mp3" "wakeup.mp3" "takbeeraat.mp3" "twilight.mp3" "adhan_compatible.mp3")
for audio_file in "${OPTIONAL_AUDIO[@]}"; do
  if ! copy_audio_if_exists "$audio_file"; then
    if yesno "  Provide $audio_file? (skip to leave blank)" "N"; then
      src="$(ask "    Path to $audio_file" "")"
      if [ -n "$src" ]; then
        src="${src/#\~/$HOME}"
        if [ -f "$src" ]; then
          cp -f "$src" "$INSTALL_DIR/audio/$audio_file"
          green "  ✓ $audio_file copied"
        else
          warn "Not found: $src — skipping $audio_file"
        fi
      fi
    else
      info "  Skipping $audio_file (feature will be silently disabled at runtime)"
    fi
  fi
done

# ──────────────────────────────────────────────────────────────
# Install core files + patch config
# ──────────────────────────────────────────────────────────────
info "Installing core files into: $INSTALL_DIR"
cp -f "$WORKDIR/core/app.py"       "$INSTALL_DIR/app.py"
cp -f "$WORKDIR/core/console.html" "$INSTALL_DIR/console.html"
cp -f "$WORKDIR/core/config.yaml"  "$INSTALL_DIR/config.yaml"
if [ -f "$WORKDIR/core/requirements.txt" ]; then
  cp -f "$WORKDIR/core/requirements.txt" "$INSTALL_DIR/requirements.txt"
fi

patch_config_yaml "$INSTALL_DIR/config.yaml"
green "  ✓ config.yaml patched"

# ──────────────────────────────────────────────────────────────
# Python virtual environment + dependencies
# ──────────────────────────────────────────────────────────────
bold ""
bold "── Step 5: Python dependencies ─────────────"

# Optional system packages
if yesno "Install system packages via sudo? (ffmpeg, lsof — needed for audio conversion)" "Y"; then
  sudo apt-get update -qq
  sudo apt-get install -y ffmpeg lsof python3-pip
else
  warn "Skipping apt packages. If audio conversion fails later, run: sudo apt-get install ffmpeg lsof"
fi

info "Creating Python virtual environment at: $INSTALL_DIR/venv"
python3 -m venv "$INSTALL_DIR/venv"
# shellcheck disable=SC1091
source "$INSTALL_DIR/venv/bin/activate"

info "Upgrading pip..."
python -m pip install --quiet --upgrade pip

REQS_FILE="$INSTALL_DIR/requirements.txt"
if [ -f "$REQS_FILE" ]; then
  info "Installing from requirements.txt..."
  python -m pip install --quiet -r "$REQS_FILE"
else
  info "Installing Python packages directly..."
  python -m pip install --quiet flask apscheduler requests pychromecast pydub PyYAML pytz gunicorn
fi
green "  ✓ Python packages installed"

# Verify imports
info "Verifying imports..."
python - <<'PY'
import flask, apscheduler, requests, pychromecast, yaml, pytz
from pydub import AudioSegment
print("  OK: all imports successful")
PY

# Syntax check
info "Verifying app.py syntax..."
python -m py_compile "$INSTALL_DIR/app.py"
green "  ✓ app.py syntax OK"

# ──────────────────────────────────────────────────────────────
# Startup verification
# ──────────────────────────────────────────────────────────────
bold ""
bold "── Step 6: Startup verification ────────────"

# Determine port from config.yaml
PORT="$(python - <<PY
import yaml
cfg = yaml.safe_load(open("$INSTALL_DIR/config.yaml"))
print(int(cfg.get("app", {}).get("port", 8786)))
PY
)"
info "App will run on port: $PORT"

info "Starting CastAdhan for verification..."
pushd "$INSTALL_DIR" >/dev/null
python app.py >"$WORKDIR/startup.log" 2>&1 &
APP_PID=$!
popd >/dev/null

# Wait for Flask + audio loading (can take 12–15 seconds on first boot)
info "Waiting 15 seconds for app to load audio files and start..."
sleep 15

if kill -0 "$APP_PID" 2>/dev/null; then
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    green "  ✓ /health responded on port $PORT"
  else
    warn "/health not responding yet — waiting 10 more seconds..."
    sleep 10
    if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      green "  ✓ /health responded on port $PORT"
    else
      warn "App did not respond on /health. Showing startup log:"
      tail -n 60 "$WORKDIR/startup.log" >&2 || true
      kill "$APP_PID" >/dev/null 2>&1 || true
      die "Verification failed: app did not respond to /health on port $PORT"
    fi
  fi

  # Quick state check
  if curl -fsS "http://127.0.0.1:${PORT}/api/state" >/dev/null 2>&1; then
    green "  ✓ /api/state responded"
  else
    warn "/api/state did not respond, but /health is OK. Continuing."
  fi

  # Test play endpoint check (does not play audio — just verifies routing)
  if curl -fsS -X POST \
       -H "Content-Type: application/json" \
       -d '{"device":"__verify__"}' \
       "http://127.0.0.1:${PORT}/api/test/play" >/dev/null 2>&1; then
    green "  ✓ /api/test/play responded"
  else
    warn "/api/test/play did not respond (non-critical — speakers may not be on network yet)."
  fi
else
  warn "App process died during startup. Showing startup log:"
  cat "$WORKDIR/startup.log" >&2 || true
  die "Verification failed: app.py crashed on startup."
fi

info "Stopping verification run..."
kill "$APP_PID" >/dev/null 2>&1 || true
sleep 1

# ──────────────────────────────────────────────────────────────
# Systemd service
# ──────────────────────────────────────────────────────────────
bold ""
bold "── Step 7: Background service ──────────────"

if yesno "Install as systemd service so CastAdhan starts automatically on boot?" "Y"; then
  SERVICE_NAME="castadhan"
  SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
  info "Creating: $SERVICE_PATH"

  sudo tee "$SERVICE_PATH" > /dev/null <<SERVICE
[Unit]
Description=CastAdhan - Adhan scheduler and Chromecast controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE

  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"

  sleep 3
  if systemctl is-active --quiet "$SERVICE_NAME"; then
    green "  ✓ Service '$SERVICE_NAME' is running"
  else
    warn "Service may not have started yet. Check with:"
    warn "  sudo systemctl status castadhan --no-pager"
  fi
else
  bold ""
  info "Service not installed. To run CastAdhan manually:"
  info "  cd '$INSTALL_DIR' && source venv/bin/activate && python app.py"
fi

# ──────────────────────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────────────────────
bold ""
bold "╔══════════════════════════════════════════╗"
bold "║          INSTALLATION COMPLETE           ║"
bold "╚══════════════════════════════════════════╝"
bold ""
green "Installed at:   $INSTALL_DIR"
green "Web console:    http://<server-ip>:${PORT}/"
green "Health check:   curl -s http://127.0.0.1:${PORT}/health"
green "Test adhan:     curl -s -X POST http://127.0.0.1:${PORT}/api/test/play"
green "Service logs:   sudo journalctl -u castadhan -f"
bold ""
info "First-time setup: open the web console, go to Settings, and confirm your"
info "location coordinates, calculation method, and speaker names are correct."
info ""
info "Audio scheduling:"
info "  - Adhan plays at all 5 daily prayer times"
info "  - Fajr warning plays 5 minutes before sunrise every day"
info "  - Asr warning plays 5 minutes before Asr every day"
info "  - Maghrib warning plays 5 minutes before Maghrib every day"
info "  - Surah Kahf plays on Friday mornings (instead of morning dhikr)"
info "  - Morning Dhikr plays every other morning at rules.morning_dhikr_time"
info "  - Evening Dhikr plays every evening after Maghrib"
info "  - Suhoor alarm activates automatically during Ramadan"
bold ""

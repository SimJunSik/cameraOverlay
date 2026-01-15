#!/bin/zsh
set -euo pipefail

if [ -d "dist" ]; then
  chmod -R u+w dist || true
  /bin/rm -rf dist
fi
if [ -d "build" ]; then
  chmod -R u+w build || true
  /bin/rm -rf build
fi
uv sync --extra build

APP_NAME="CameraOverlay"
BUNDLE_ID="com.example.cameraoverlay"
CAMERA_USAGE="Camera access is required to show your webcam overlay."
ICON_PATH="assets/icon.icns"
MP_MODULES_DIR="$(python - <<'PY'
import mediapipe
from pathlib import Path
print(Path(mediapipe.__file__).resolve().parent / "modules")
PY
)"
PYINSTALLER_ARGS=(
  --noconfirm
  --clean
  --windowed
  --add-data "${MP_MODULES_DIR}:mediapipe/modules"
  --collect-data mediapipe
  --collect-submodules mediapipe
  --collect-submodules AVFoundation
  --collect-submodules objc
  --osx-bundle-identifier "$BUNDLE_ID"
  --name "$APP_NAME"
)
if [ -f "$ICON_PATH" ]; then
  PYINSTALLER_ARGS+=(--icon "$ICON_PATH")
fi

uv run pyinstaller "${PYINSTALLER_ARGS[@]}" app.py

INFO_PLIST="dist/${APP_NAME}.app/Contents/Info.plist"
/bin/test -f "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :NSCameraUsageDescription string '$CAMERA_USAGE'" "$INFO_PLIST" || \
/usr/libexec/PlistBuddy -c "Set :NSCameraUsageDescription '$CAMERA_USAGE'" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string '$APP_NAME'" "$INFO_PLIST" || \
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName '$APP_NAME'" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleName string '$APP_NAME'" "$INFO_PLIST" || \
/usr/libexec/PlistBuddy -c "Set :CFBundleName '$APP_NAME'" "$INFO_PLIST"

if [ -f "$ICON_PATH" ]; then
  ICON_NAME="$(basename "$ICON_PATH")"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string '$ICON_NAME'" "$INFO_PLIST" || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile '$ICON_NAME'" "$INFO_PLIST"
  cp "$ICON_PATH" "dist/${APP_NAME}.app/Contents/Resources/$ICON_NAME"
fi

RES_DIR="dist/${APP_NAME}.app/Contents/Resources/mediapipe/modules"
FW_DIR="dist/${APP_NAME}.app/Contents/Frameworks/mediapipe/modules"
if [ -d "$RES_DIR" ] && [ ! -d "$FW_DIR" ]; then
  mkdir -p "$(dirname "$FW_DIR")"
  cp -R "$RES_DIR" "$FW_DIR"
elif [ -d "$FW_DIR" ] && [ ! -d "$RES_DIR" ]; then
  mkdir -p "$(dirname "$RES_DIR")"
  cp -R "$FW_DIR" "$RES_DIR"
fi

xattr -dr com.apple.quarantine "dist/${APP_NAME}.app" || true
codesign --force --deep --sign - "dist/${APP_NAME}.app"

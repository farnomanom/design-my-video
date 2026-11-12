#!/bin/bash
# Builds "Design My Video.app" from app/Sources.
#   app/build_app.sh            development build: engine runs from this project folder
#   app/build_app.sh --bundle   self-contained build: engine from build/engine copied inside the app
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="$(pwd)"
APP="build/Design My Video.app"
VERSION="1.4.0"
MODE="${1:-dev}"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" build/obj

echo "▸ Compiling Swift (Intel + Apple silicon)"
for arch in x86_64 arm64; do
  swiftc -O -parse-as-library -target "$arch-apple-macos13.0" \
    -sdk "$(xcrun --show-sdk-path)" \
    app/Sources/*.swift -o "build/obj/DesignMyVideo-$arch"
done
lipo -create build/obj/DesignMyVideo-x86_64 build/obj/DesignMyVideo-arm64 -output "$APP/Contents/MacOS/DesignMyVideo"

echo "▸ Icon"
ICONSET=build/obj/AppIcon.iconset
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
.venv/bin/python app/make_icon.py build/obj/icon_1024.png
for s in 16 32 128 256 512; do
  sips -z $s $s build/obj/icon_1024.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) build/obj/icon_1024.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

echo "▸ User manual (docs/MANUAL.md)"
.venv/bin/python app/manual_html.py docs/MANUAL.md build/obj/manual.html
cp build/obj/manual.html "$APP/Contents/Resources/manual.html"

DEVKEY=""
if [ "$MODE" != "--bundle" ]; then
  DEVKEY="<key>DMVDevProjectPath</key><string>${PROJECT}</string>"
else
  echo "▸ Copying bundled engine"
  cp -R build/engine "$APP/Contents/Resources/engine"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Design My Video</string>
  <key>CFBundleDisplayName</key><string>Design My Video</string>
  <key>CFBundleIdentifier</key><string>com.designmyvideo.app</string>
  <key>CFBundleExecutable</key><string>DesignMyVideo</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.video</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Design My Video</string>
  ${DEVKEY}
</dict></plist>
PLIST

echo "▸ Signing (ad-hoc)"
codesign --force --deep --sign - "$APP"
echo "✓ Built $APP"

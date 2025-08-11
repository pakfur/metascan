#!/bin/bash
# Create ICNS file from PNG for macOS app

# Create iconset directory
mkdir -p MetaScan.iconset

# Generate different icon sizes
sips -z 16 16     icon.png --out MetaScan.iconset/icon_16x16.png
sips -z 32 32     icon.png --out MetaScan.iconset/icon_16x16@2x.png
sips -z 32 32     icon.png --out MetaScan.iconset/icon_32x32.png
sips -z 64 64     icon.png --out MetaScan.iconset/icon_32x32@2x.png
sips -z 128 128   icon.png --out MetaScan.iconset/icon_128x128.png
sips -z 256 256   icon.png --out MetaScan.iconset/icon_128x128@2x.png
sips -z 256 256   icon.png --out MetaScan.iconset/icon_256x256.png
sips -z 512 512   icon.png --out MetaScan.iconset/icon_256x256@2x.png
sips -z 512 512   icon.png --out MetaScan.iconset/icon_512x512.png
cp icon.png MetaScan.iconset/icon_512x512@2x.png

# Convert to ICNS
iconutil -c icns MetaScan.iconset

# Clean up
rm -rf MetaScan.iconset

echo "Created MetaScan.icns"
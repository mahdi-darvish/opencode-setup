#!/bin/zsh
# Rebuild and reinstall SnapToOpenCode. Signing by bundle identifier keeps macOS permissions across rebuilds.
set -e
cd "$(dirname "$0")"
APP=/Applications/SnapToOpenCode.app
ID=local.korouji.snap-to-opencode

swiftc -O -o build-SnapToOpenCode main.swift
pkill -x SnapToOpenCode || true
sleep 1
mv build-SnapToOpenCode "$APP/Contents/MacOS/SnapToOpenCode"
codesign --force --sign - -r="designated => identifier \"$ID\"" "$APP"
open -a "$APP"

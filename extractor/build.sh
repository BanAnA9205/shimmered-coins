#!/usr/bin/env sh
# Build the extractor with Mono (Linux/macOS). Output: extractor/bin/TerrariaExtractor.exe
set -e
cd "$(dirname "$0")"
mkdir -p bin
mcs -nologo -langversion:5 -out:bin/TerrariaExtractor.exe TerrariaExtractor.cs Probing.cs
mcs -nologo -langversion:5 -platform:x86 -out:bin/TerrariaExtractor32.exe TerrariaExtractor.cs Probing.cs
echo "built bin/TerrariaExtractor.exe (and bin/TerrariaExtractor32.exe for 32-bit game builds)"

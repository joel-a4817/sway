#!/usr/bin/env bash
set -euo pipefail

SVG_ROOT="$HOME/noteshub/SVGs"
PDF_ROOT="$HOME/noteshub/Documents"

convert_svg_to_pdf() {
    local svg="$1"
    local pdf="$2"

    # YOUR FIREFOX EXPORT COMMAND HERE

    echo "TODO: convert '$svg' -> '$pdf'"
}

find "$SVG_ROOT" -type f -name '*.svg' | while read -r svg; do

    rel="${svg#$SVG_ROOT/}"

    target_pdf="$PDF_ROOT/$rel.pdf"

    mkdir -p "$(dirname "$target_pdf")"

    tmp_pdf="$(mktemp --suffix=.pdf)"

    convert_svg_to_pdf "$svg" "$tmp_pdf"

    mv "$tmp_pdf" "$target_pdf"

    echo "Updated:"
    echo "  $target_pdf"
done

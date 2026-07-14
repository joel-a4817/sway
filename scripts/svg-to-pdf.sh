#!/usr/bin/env bash
set -euo pipefail

SVG_ROOT="$HOME/noteshub/SVGs"
PDF_ROOT="$HOME/noteshub/Documents"

convert_svg_to_pdf() {
    local svg="$1"
    local pdf="$2"

    local width height margin html svg_uri

    margin=50

    width=$(grep -oP 'width="\K[0-9.]+' "$svg" | head -1)
    height=$(grep -oP 'height="\K[0-9.]+' "$svg" | head -1)

    width=$(printf "%.0f" "$width")
    height=$(printf "%.0f" "$height")

    svg_uri="file://$(realpath "$svg")"

    html=$(mktemp --suffix=.html)

    cat > "$html" <<EOF
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>

@page {
    size: $((width + margin*2))px $((height + margin*2))px;
    margin: 0;
}

html, body {
    width: $((width + margin*2))px;
    height: $((height + margin*2))px;
    margin: 0;
    padding: 0;
}

body {
    display: flex;
    justify-content: center;
    align-items: center;
}

img {
    width: ${width}px;
    height: ${height}px;
    display: block;
}

</style>
</head>
<body>
<img src="$svg_uri">
</body>
</html>
EOF

    chromium \
        --headless \
        --disable-gpu \
        --run-all-compositor-stages-before-draw \
        --virtual-time-budget=5000 \
        --print-to-pdf-no-header \
        --print-to-pdf="$pdf" \
        "file://$html"

    rm -f "$html"
}

find "$SVG_ROOT" -type f -name '*.svg' | while read -r svg; do

    rel="${svg#$SVG_ROOT/}"

    target_pdf="$PDF_ROOT/${rel%.excalidraw.svg}.pdf"

    mkdir -p "$(dirname "$target_pdf")"

    tmp_pdf="$(mktemp --suffix=.pdf)"

    echo "Converting:"
    echo "  $svg"
    echo "  ->"
    echo "  $target_pdf"

    convert_svg_to_pdf "$svg" "$tmp_pdf"

    mv "$tmp_pdf" "$target_pdf"

    echo "Updated:"
    echo "  $target_pdf"
done


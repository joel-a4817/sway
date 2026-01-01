#Allows sway to generate with different colour (not black) in fullscreen whilst it remains translucent in tiling/floating mode.
#!/usr/bin/env bash
set -euo pipefail

swaymsg -t subscribe -m '["window"]' \
| stdbuf -oL jq -c '
    select(.change=="fullscreen_mode")
    | {
        app_id: .container.app_id,
        pid: .container.pid,
        con_id: .container.id,
        fs: .container.fullscreen_mode,
        floating: (.container.floating // ""),
        w: (.container.rect.width // 0),
        h: (.container.rect.height // 0)
      }
  ' \
| while read -r ev; do
    app_id="$(jq -r '.app_id // ""' <<<"$ev")"
    pid="$(jq -r '.pid // empty' <<<"$ev")"
    con_id="$(jq -r '.con_id // empty' <<<"$ev")"
    fs="$(jq -r '.fs // 0' <<<"$ev")"
    floating="$(jq -r '.floating // ""' <<<"$ev")"
    w="$(jq -r '.w // 0' <<<"$ev")"
    h="$(jq -r '.h // 0' <<<"$ev")"

    case "$app_id" in
      foot|footclient)
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        [[ "$con_id" =~ ^[0-9]+$ ]] || continue

        if [[ "$fs" != "0" ]]; then
          # Enter fullscreen -> Theme 2
          kill -USR2 "$pid" 2>/dev/null || true
          continue
        fi

        # Exit fullscreen -> Theme 1
        # Workaround: if it’s tiled and already output-sized, force a reconfigure:
        # float enable then float disable on that container.
        # This nudges sway/wayland into rebuilding the surface state.
        #
        # Determine if we're in tiling (floating ends with "_off" typically)
        is_tiled=0
        [[ "$floating" == *"_off" || "$floating" == "false" || -z "$floating" ]] && is_tiled=1

        if [[ "$is_tiled" -eq 1 ]]; then
          # Get focused output size (so we only do this hack when the view is basically full-screen already)
          # swaymsg supports get_outputs, returning JSON for outputs. [6](https://manpages.debian.org/trixie/sway/swaymsg.1.en.html)
          out_w="$(swaymsg -t get_outputs | jq -r '.[] | select(.focused==true) | .rect.width')"
          out_h="$(swaymsg -t get_outputs | jq -r '.[] | select(.focused==true) | .rect.height')"

          # “close enough” threshold: allow small diffs due to gaps/borders
          # If the view is already essentially output-sized, do the float/unfloat kick.
          if [[ "$w" -ge $((out_w - 10)) && "$h" -ge $((out_h - 10)) ]]; then
            # Apply to this exact container using con_id (NOT pid). [4](https://github.com/swaywm/sway/issues/4972)[5](https://unix.stackexchange.com/questions/773681/swaymsg-not-focusing-window)
            # floating enable/disable are valid sway commands. [3](https://manpath.be/f29/5/sway)
            swaymsg -q -- "[con_id=${con_id}] floating enable, floating disable" || true
          fi
        fi

        kill -USR1 "$pid" 2>/dev/null || true
      ;;
    esac
  done


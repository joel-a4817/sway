# Replace the basic PipeWire readiness check with physical-output readiness.
# This function is resolved at runtime after the complete manifest is loaded.

def ensure_pipewire_ready():
    for unit in PIPEWIRE_UNITS:
        user_service('start', unit)

    deadline = time.monotonic() + 15.0
    stable = None
    stable_reads = 0
    last_error = 'PipeWire did not become ready'

    while time.monotonic() < deadline:
        status = run(
            [exe('wpctl'), 'status'],
            False,
            3,
            media_env(),
        )

        if status.returncode:
            last_error = (
                status.stderr.strip()
                or status.stdout.strip()
                or last_error
            )
            time.sleep(0.1)
            continue

        try:
            topology = audio_topology()
            physical = _physical_output_rows(topology)

            signature = tuple(sorted(
                (card['name'], sink['name'])
                for card, sink in physical
            ))

            if not signature:
                last_error = (
                    'No physical playback output in PipeWire graph'
                )
                stable = None
                stable_reads = 0

            elif signature == stable:
                stable_reads += 1

                if stable_reads >= 3:
                    return

            else:
                stable = signature
                stable_reads = 1

        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
        ) as error:
            last_error = str(error)
            stable = None
            stable_reads = 0

        time.sleep(0.15)

    raise RuntimeError(
        'Physical playback topology did not become ready: '
        + last_error
    )

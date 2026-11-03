# Replace the basic PipeWire readiness check with physical-output readiness.
# This function is resolved at runtime after the complete manifest is loaded.


def reset_pipewire_stack():
    """Bring up the local user audio stack without depending on saved state.

    Start is intentionally non-destructive: the Stop action already owns
    shutdown.  Start must never remove systemd-owned sockets or tear down a
    partly recovered graph, because doing so can leave every unit inactive.
    """
    ordered = (
        'pipewire.socket',
        'pipewire.service',
        'wireplumber.service',
        'pipewire-pulse.socket',
        'pipewire-pulse.service',
    )
    for unit in ordered:
        try:
            user_service('start', unit)
        except Exception as error:
            raise RuntimeError('Audio startup failed while starting '+unit+': '+str(error)) from error

    deadline=time.monotonic()+20
    last='audio services did not become active'
    while time.monotonic()<deadline:
        states={}
        for unit in ordered:
            result=run(['systemctl','--user','is-active',unit],False,5)
            states[unit]=result.stdout.strip()
        if all(states.get(unit)=='active' for unit in ordered):
            pulse=run([exe('pactl'),'info'],False,5,media_env())
            graph=run([exe('pw-dump')],False,5,media_env())
            if pulse.returncode==0 and graph.returncode==0:
                return
            last=('pactl: '+(pulse.stderr.strip() or pulse.stdout.strip())+
                  '; pw-dump: '+(graph.stderr.strip() or graph.stdout.strip()))
        else:
            last=', '.join(unit+'='+states.get(unit,'') for unit in ordered)
        time.sleep(.1)
    raise RuntimeError('Audio stack did not become ready: '+last)

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

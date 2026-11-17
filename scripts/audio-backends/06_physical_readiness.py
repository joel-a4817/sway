# PipeWire readiness is API-first. NixOS owns the core PipeWire lifecycle.
# Media Control must not stop/restart core services because generated service
# units may legitimately rely on activation units not present in every session.


def _audio_apis_ready():
    pulse=run([exe('pactl'),'info'],False,5,media_env())
    graph=run([exe('pw-dump')],False,5,media_env())
    if pulse.returncode or graph.returncode:
        return False,(pulse.stderr.strip() or pulse.stdout.strip() or
                      graph.stderr.strip() or graph.stdout.strip() or
                      'PipeWire APIs are unavailable')
    return True,''


def reset_pipewire_stack():
    """Recover Media Control state without mutating NixOS-owned PipeWire units."""
    ensure_pipewire_ready()


def ensure_pipewire_ready(start_units=True):
    # start_units remains accepted for compatibility with existing callers.
    # Core PipeWire lifecycle belongs to NixOS; readiness is established through
    # the live PulseAudio and PipeWire APIs, never by guessing systemd unit names.
    deadline=time.monotonic()+20.0
    stable=None;stable_reads=0;last_error='PipeWire did not become ready'
    while time.monotonic()<deadline:
        ready,error=_audio_apis_ready()
        if not ready:
            last_error=error;stable=None;stable_reads=0;time.sleep(.1);continue
        try:
            topology=audio_topology();physical=_physical_output_rows(topology)
            signature=tuple(sorted((card['name'],sink['name']) for card,sink in physical))
            if not signature:
                last_error='No physical playback output in PipeWire graph';stable=None;stable_reads=0
            elif signature==stable:
                stable_reads+=1
                if stable_reads>=3:return
            else:
                stable=signature;stable_reads=1
        except (RuntimeError,ValueError,OSError,subprocess.TimeoutExpired) as error:
            last_error=str(error);stable=None;stable_reads=0
        time.sleep(.1)
    raise RuntimeError('Audio stack did not become ready through pactl/pw-dump: '+last_error)

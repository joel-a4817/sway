from pathlib import Path
root=Path(__file__).parent
out=(root/'60_output_transactions.py').read_text()
life=(root/'99_lifecycle_cli.py').read_text()
assert "selected_filter()==NO_FILTER" in out
assert "pw_default()!=selected['sink']" in out
assert "alive(rpid(LOCALMONPID)) or alive(rpid(BYPASSPID))" in out
assert "Filtered output switch was not confirmed" in out
assert 'def stop_audio_services(mark_stopped=True,stop_pipewire=True):' in life
for unit in ('wireplumber.service','pipewire-pulse.service','pipewire.service','pipewire-pulse.socket','pipewire.socket'):
 assert unit in life
assert life.index('ensure_pipewire_ready()') < life.index("if STOPPED.exists() and not force:")
assert 'normalize_audio_volumes()' in life and 'apply_master_volume(master_volume()' in life
print('v13 contract tests: PASS')

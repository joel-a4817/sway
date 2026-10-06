from pathlib import Path
root=Path(__file__).parent
life=(root/'99_lifecycle_cli.py').read_text()
runtime=(root/'05_runtime.py').read_text()
menu=(root/'40_main_menu.sh').read_text()
web=(root/'85_web_assets.py').read_text()
for unit in ('camilladsp-system-audio.service','playerctld.service','camilladsp-wayvnc.service','shairport-sync.service','nqptp.service','audio-fixes.timer','audio-fixes.service'):
    assert unit in life
assert 'start_media_foundation_services()' in life
assert 'Physical playback topology did not become ready' in runtime
assert "Start media services" in menu and "Stop media services" in menu
assert "Start media services" in web and "Stop media services" in web
print('v15 contract tests: PASS')

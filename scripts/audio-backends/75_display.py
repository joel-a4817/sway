def _device_lock(*arguments):
    result=run([str(DEVICE_LOCK),*arguments],False,20,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Away/display toggle failed')
    try:data=json.loads(result.stdout)
    except (ValueError,TypeError) as error:raise RuntimeError('Invalid device-lock response') from error
    if not isinstance(data,dict):raise RuntimeError('Invalid device-lock response')
    return data
def away_display_state():return _device_lock('--status')
def toggle_away_display():return _device_lock()

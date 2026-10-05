def full_state():
    return {
        'mode':mode_state(),'awayDisplay':away_display_state(),
        'profiles':{'profiles':[NO_FILTER]+[item.name for item in profiles()],'filters':listening_filters(),'active':active(),'running':alive(rpid(CAMPID),'camilladsp')},
        'player':player_state(),'systemMedia':system_state(),'groups':groups_state(),'playlists':lists(),
    }

def volatile_state():
    return {
        'audioStopSignal':True,
        'mode':mode_state(),'awayDisplay':away_display_state(),
        'profiles':{'active':active(),'filters':listening_filters(),'running':alive(rpid(CAMPID),'camilladsp')},
        'player':player_state(),'groups':groups_state(),
    }


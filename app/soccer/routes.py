import logging
from flask import render_template
from app import socketio
from . import soccer_bp

logger = logging.getLogger(__name__)

game_state = {
    'home_team': {
        'name': 'HOME TEAM', 'abbr': 'HOM', 'logo': '',
        'score': 0, 'color': '#1d4ed8',
        'stats': {'shots': 0, 'possession': 50, 'fouls': 0, 'corners': 0},
        'cards': {'yellow': 0, 'red': 0},
        'players': []
    },
    'away_team': {
        'name': 'AWAY TEAM', 'abbr': 'AWY', 'logo': '',
        'score': 0, 'color': '#b91c1c',
        'stats': {'shots': 0, 'possession': 50, 'fouls': 0, 'corners': 0},
        'cards': {'yellow': 0, 'red': 0},
        'players': []
    },
    'clock': {
        'minutes': 0, 'seconds': 0, 'running': False,
        'period': '1st HALF', 'stoppage_time': 0, 'show_stoppage': False
    },
    'penalties': {
        'active': False,
        'home': [], # Array of booleans: True (Scored), False (Missed)
        'away': []
    },
    'visibility': {
        'scoreboard': True, 'lower_third': False, 'stats': False, 'lineup': False
    },
    'competition': '',
    'current_event': { 'type': '', 'team': '', 'player_1': '', 'player_2': '', 'time': '' },
    'current_lineup_team': 'home',
    'current_lineup_type': 'starter' # Can be 'starter' or 'substitute'
}

# Variable to ensure only one timer engine is running
timer_thread = None

def background_timer():
    """Timer engine that runs in the background on the server"""
    global game_state
    while True:
        try:
            socketio.sleep(1) # Wait exactly 1 second
            if game_state['clock']['running']:
                game_state['clock']['seconds'] += 1
                if game_state['clock']['seconds'] >= 60:
                    game_state['clock']['seconds'] = 0
                    game_state['clock']['minutes'] += 1
                # Send the updated time to all screens
                socketio.emit('clock_tick', game_state['clock'], namespace='/soccer')
        except Exception:
            # Never let the loop die - a crash here would freeze the clock forever
            logger.exception('background_timer iteration failed')

@soccer_bp.route('/')
def controller():
    return render_template('soccer/controller.html')

@soccer_bp.route('/overlay')
def overlay():
    return render_template('soccer/overlay.html')

@soccer_bp.route('/lineup')
def lineup():
    return render_template('soccer/lineup.html')

@soccer_bp.route('/stats')
def stats():
    return render_template('soccer/stats_overlay.html')

@socketio.on('connect', namespace='/soccer')
def handle_connect():
    global timer_thread
    # When the page is opened, if the timer has not yet started, start it in the correct process
    if timer_thread is None:
        timer_thread = socketio.start_background_task(background_timer)
    
    socketio.emit('state_updated', game_state, namespace='/soccer')

def _safe_merge(target, updates):
    """Recursively merge updates into target, only touching keys that already exist.
    Never lets a malformed value crash the caller - bad keys are logged and skipped."""
    for key, value in updates.items():
        try:
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                _safe_merge(target[key], value)
            else:
                target[key] = value
        except Exception:
            logger.exception("Failed to merge key '%s' into state", key)

@socketio.on('update_state', namespace='/soccer')
def handle_update_state(data):
    global game_state
    if not isinstance(data, dict):
        logger.warning('Ignoring malformed update_state payload: %r', data)
        return
    try:
        _safe_merge(game_state, data)
        socketio.emit('state_updated', game_state, namespace='/soccer')
    except Exception:
        logger.exception('Error handling update_state')

@socketio.on('clock_action', namespace='/soccer')
def handle_clock(data):
    global game_state
    if not isinstance(data, dict):
        logger.warning('Ignoring malformed clock_action payload: %r', data)
        return
    try:
        action = data.get('action')

        # If we receive minutes and seconds from Sync, make sure they are integers
        if 'minutes' in data:
            try:
                game_state['clock']['minutes'] = int(data['minutes'])
            except (TypeError, ValueError):
                logger.warning('Ignoring invalid clock minutes: %r', data.get('minutes'))
        if 'seconds' in data:
            try:
                game_state['clock']['seconds'] = int(data['seconds'])
            except (TypeError, ValueError):
                logger.warning('Ignoring invalid clock seconds: %r', data.get('seconds'))

        if action == 'start':
            game_state['clock']['running'] = True
        elif action == 'stop':
            game_state['clock']['running'] = False
        elif action == 'reset':
            game_state['clock']['running'] = False
            game_state['clock']['minutes'] = 0
            game_state['clock']['seconds'] = 0
            game_state['clock']['stoppage_time'] = 0
            game_state['clock']['show_stoppage'] = False

        socketio.emit('state_updated', game_state, namespace='/soccer')
        # Update the displays visually immediately (especially useful for reset)
        socketio.emit('clock_tick', game_state['clock'], namespace='/soccer')
    except Exception:
        logger.exception('Error handling clock_action')

@socketio.on('trigger_event', namespace='/soccer')
def handle_event(data):
    global game_state
    if not isinstance(data, dict):
        logger.warning('Ignoring malformed trigger_event payload: %r', data)
        return
    try:
        game_state['current_event'] = data
        game_state['visibility']['lower_third'] = True

        # Cards are tallied automatically so the scoreboard counter stays accurate
        team = data.get('team')
        event_type = data.get('type')
        if team in ('home', 'away') and event_type in ('yellow_card', 'red_card'):
            card_key = 'yellow' if event_type == 'yellow_card' else 'red'
            game_state[f'{team}_team']['cards'][card_key] += 1

        socketio.emit('state_updated', game_state, namespace='/soccer')
    except Exception:
        logger.exception('Error handling trigger_event')

@socketio.on('hide_event', namespace='/soccer')
def hide_event():
    global game_state
    try:
        game_state['visibility']['lower_third'] = False
        socketio.emit('state_updated', game_state, namespace='/soccer')
    except Exception:
        logger.exception('Error handling hide_event')

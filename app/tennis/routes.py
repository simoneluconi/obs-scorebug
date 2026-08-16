import copy
import logging
import threading

from flask import render_template
from app import socketio
from . import tennis_bp

logger = logging.getLogger(__name__)

# Serializes every mutation of game_state so concurrent Socket.IO events
# (e.g. two operators clicking at once, or a burst of rapid clicks) can't
# interleave their read-modify-write steps and corrupt shared state.
state_lock = threading.Lock()

game_state = {
    'home_team': {
        'name': 'HOME PLAYER', 'abbr': 'HOM', 'logo': '',
        'sets': 0, 'games': 0, 'score': '0', 'color': '#1d4ed8', 'serving': True,
        'stats': {'aces': 0, 'winners': 0, 'unforced': 0, 'double_faults': 0},
        'players': [], # Useful for doubles or Davis Cup
        'rank': '', # Seed/ranking shown on main scoreboard, e.g. "3". Empty = hidden.
        'player2_name': '' # Second player name, only rendered when match_type == 'doubles'
    },
    'away_team': {
        'name': 'AWAY PLAYER', 'abbr': 'AWY', 'logo': '',
        'sets': 0, 'games': 0, 'score': '0', 'color': '#b91c1c', 'serving': False,
        'stats': {'aces': 0, 'winners': 0, 'unforced': 0, 'double_faults': 0},
        'players': [],
        'rank': '',
        'player2_name': ''
    },
    'clock': {
        'hours': 0, 'minutes': 0, 'seconds': 0, 'running': False,
        'period': 'FINAL - MENS SINGLES'
    },
    'penalties': { # Reused as a tracker for Tie-Break or mini-breaks
        'active': False,
        'home': [],
        'away': []
    },
    'visibility': {
        'scoreboard': True, 'lower_third': False, 'stats': False, 'lineup': False
    },
    'current_event': { 'type': '', 'team': '', 'player_1': '', 'player_2': '', 'time': '' },
    'current_lineup_team': 'home',
    'current_lineup_type': 'starter',
    'match_format': 'BO3',
    'set_history': [], # Completed sets, e.g. [{'home': 6, 'away': 4}, ...]. Empty = no history row shown.
    'match_type': 'singles', # 'singles' (default, identical to legacy rendering) or 'doubles'
    'surface': '', # Free text / Hard / Clay / Grass / Carpet. Empty = hidden.
    'point_badge': { # Persistent manual Match/Set Point badge (opt-in, stays until toggled off)
        'active': False,
        'label': 'MATCH POINT'
    }
}

# Undo stack for handle_update_state mutations. Bounded deep-copy snapshots.
state_history = []
MAX_UNDO_HISTORY = 20

timer_thread = None

def background_timer():
    global game_state
    while True:
        try:
            socketio.sleep(1)
            with state_lock:
                if game_state['clock']['running']:
                    game_state['clock']['seconds'] += 1
                    if game_state['clock']['seconds'] >= 60:
                        game_state['clock']['seconds'] = 0
                        game_state['clock']['minutes'] += 1
                        if game_state['clock']['minutes'] >= 60:
                            game_state['clock']['minutes'] = 0
                            game_state['clock']['hours'] += 1
                    socketio.emit('clock_tick', game_state['clock'], namespace='/tennis')
        except Exception:
            # Never let the loop die - a crash here would freeze the clock forever
            logger.exception('background_timer iteration failed')

@tennis_bp.route('/')
def controller(): return render_template('tennis/controller.html')
@tennis_bp.route('/overlay')
def overlay(): return render_template('tennis/overlay.html')
@tennis_bp.route('/lineup')
def lineup(): return render_template('tennis/lineup.html')
@tennis_bp.route('/stats')
def stats(): return render_template('tennis/stats_overlay.html')

@socketio.on('connect', namespace='/tennis')
def handle_connect():
    global timer_thread
    if timer_thread is None:
        timer_thread = socketio.start_background_task(background_timer)
    socketio.emit('state_updated', game_state, namespace='/tennis')

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

@socketio.on('update_state', namespace='/tennis')
def handle_update_state(data):
    global game_state
    if not isinstance(data, dict):
        logger.warning('Ignoring malformed update_state payload: %r', data)
        return
    try:
        with state_lock:
            state_history.append(copy.deepcopy(game_state))
            if len(state_history) > MAX_UNDO_HISTORY:
                state_history.pop(0)
            _safe_merge(game_state, data)
            socketio.emit('state_updated', game_state, namespace='/tennis')
    except Exception:
        logger.exception('Error handling update_state')

@socketio.on('undo', namespace='/tennis')
def handle_undo():
    global game_state
    try:
        with state_lock:
            if not state_history:
                return
            game_state = state_history.pop()
            socketio.emit('state_updated', game_state, namespace='/tennis')
    except Exception:
        logger.exception('Error handling undo')

@socketio.on('clock_action', namespace='/tennis')
def handle_clock(data):
    global game_state
    if not isinstance(data, dict):
        logger.warning('Ignoring malformed clock_action payload: %r', data)
        return
    try:
        with state_lock:
            action = data.get('action')

            for field in ('hours', 'minutes', 'seconds'):
                if field in data:
                    try:
                        game_state['clock'][field] = int(data[field])
                    except (TypeError, ValueError):
                        logger.warning('Ignoring invalid clock %s: %r', field, data.get(field))

            if action == 'start':
                game_state['clock']['running'] = True
            elif action == 'stop':
                game_state['clock']['running'] = False
            elif action == 'reset':
                game_state['clock']['running'] = False
                game_state['clock']['hours'] = 0
                game_state['clock']['minutes'] = 0
                game_state['clock']['seconds'] = 0

            socketio.emit('state_updated', game_state, namespace='/tennis')
            socketio.emit('clock_tick', game_state['clock'], namespace='/tennis')
    except Exception:
        logger.exception('Error handling clock_action')

@socketio.on('trigger_event', namespace='/tennis')
def handle_event(data):
    global game_state
    if not isinstance(data, dict):
        logger.warning('Ignoring malformed trigger_event payload: %r', data)
        return
    try:
        with state_lock:
            game_state['current_event'] = data
            game_state['visibility']['lower_third'] = True
            socketio.emit('state_updated', game_state, namespace='/tennis')
    except Exception:
        logger.exception('Error handling trigger_event')

@socketio.on('hide_event', namespace='/tennis')
def hide_event():
    global game_state
    try:
        with state_lock:
            game_state['visibility']['lower_third'] = False
            socketio.emit('state_updated', game_state, namespace='/tennis')
    except Exception:
        logger.exception('Error handling hide_event')

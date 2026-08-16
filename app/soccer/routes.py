import logging
import threading
from flask import render_template
from app import socketio
from . import soccer_bp

logger = logging.getLogger(__name__)

# Serializes every mutation of game_state so concurrent Socket.IO events
# (e.g. two operators clicking at once, or a burst of rapid clicks) can't
# interleave their read-modify-write steps and corrupt shared state.
state_lock = threading.Lock()

game_state = {
    'home_team': {
        'name': 'HOME TEAM', 'abbr': 'HOM', 'logo': '',
        'score': 0, 'color': '#1d4ed8',
        'stats': {'shots': 0, 'possession': 50, 'fouls': 0, 'corners': 0},
        'cards': {'yellow': 0, 'red': 0},
        'players': [],
        'goals': []  # List of {player, time} - populated only when a scorer is selected
    },
    'away_team': {
        'name': 'AWAY TEAM', 'abbr': 'AWY', 'logo': '',
        'score': 0, 'color': '#b91c1c',
        'stats': {'shots': 0, 'possession': 50, 'fouls': 0, 'corners': 0},
        'cards': {'yellow': 0, 'red': 0},
        'players': [],
        'goals': []
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
    'competition_logo': '',
    'show_final_banner': False,
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
            with state_lock:
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
        with state_lock:
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
        with state_lock:
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
        with state_lock:
            team = data.get('team')
            event_type = data.get('type')
            player_1 = data.get('player_1') or ''

            # If a second yellow auto-generates a red card, we display a dedicated
            # banner for it instead of the plain yellow-card one that was triggered.
            second_yellow_player = None

            # Cards are tallied automatically so the scoreboard counter stays accurate
            if team in ('home', 'away') and event_type in ('yellow_card', 'red_card'):
                card_key = 'yellow' if event_type == 'yellow_card' else 'red'
                game_state[f'{team}_team']['cards'][card_key] += 1

                # Per-player yellow tracking is fully opt-in: it only runs when the
                # operator actually picked a player from the dropdown. If player_1
                # is empty (today's behavior for an operator who skips it), we
                # cannot know whose card it is, so we do nothing else here - the
                # team counter above is the only thing that changes, exactly like
                # before this feature existed.
                if event_type == 'yellow_card' and player_1:
                    try:
                        players = game_state[f'{team}_team'].get('players', [])
                        for p in players:
                            if isinstance(p, dict) and f"{p.get('number')}. {p.get('name')}" == player_1:
                                p['yellow_cards'] = p.get('yellow_cards', 0) + 1
                                if p['yellow_cards'] >= 2:
                                    game_state[f'{team}_team']['cards']['red'] += 1
                                    second_yellow_player = player_1
                                break
                    except Exception:
                        logger.exception('Error tracking per-player yellow cards')

            # Scorer list is opt-in too: only appended when a player is selected.
            # The score itself is untouched here - it is still driven independently
            # by the existing +/- buttons, so this list can never desync the score.
            if team in ('home', 'away') and event_type == 'goal' and player_1:
                try:
                    game_state[f'{team}_team'].setdefault('goals', []).append({
                        'player': player_1, 'time': data.get('time', '')
                    })
                except Exception:
                    logger.exception('Error appending goal scorer')

            if second_yellow_player:
                game_state['current_event'] = {
                    'type': 'second_yellow_red',
                    'team': team,
                    'player_1': second_yellow_player,
                    'player_2': '',
                    'time': data.get('time', '')
                }
            else:
                game_state['current_event'] = data

            game_state['visibility']['lower_third'] = True

            socketio.emit('state_updated', game_state, namespace='/soccer')
    except Exception:
        logger.exception('Error handling trigger_event')

@socketio.on('hide_event', namespace='/soccer')
def hide_event():
    global game_state
    try:
        with state_lock:
            game_state['visibility']['lower_third'] = False
            socketio.emit('state_updated', game_state, namespace='/soccer')
    except Exception:
        logger.exception('Error handling hide_event')

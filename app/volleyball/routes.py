import logging
from flask import render_template
from app import socketio
from . import volleyball_bp

logger = logging.getLogger(__name__)

SETS_TO_WIN_MATCH = 3

game_state = {
    'home_team': {
        'name': 'HOME TEAM', 'abbr': 'HOM', 'logo': '',
        'score': 0, 'sets_won': 0, 'color': '#3b82f6', 'players': [],
        'timeouts_used': 0
    },
    'away_team': {
        'name': 'AWAY TEAM', 'abbr': 'AWY', 'logo': '',
        'score': 0, 'sets_won': 0, 'color': '#ef4444', 'players': [],
        'timeouts_used': 0
    },
    'clock': {
        'hours': 0, 'minutes': 0, 'seconds': 0, 'running': False,
        'period': 'FINAL'
    },
    'previous_sets': [], 'serving_team_index': 0, 'first_server_current_set': 0,
    'view_mode': 'compact', 'timeout_visible': False, 'timeout_team': None,
    'current_set_number': 1,
    'is_visible': True, 'set_over': False, 'match_over': False, 'match_winner': None,
    'current_player': {}, 'player_overlay_visible': False,
    'replay_visible': False,

    'home_set_point': False,
    'away_set_point': False,
    'home_match_point': False,
    'away_match_point': False,
    'stats_overlay_visible': False,
}

timer_thread = None

def background_timer():
    """Independent timer engine"""
    global game_state
    while True:
        try:
            socketio.sleep(1)
            if game_state['clock']['running']:
                game_state['clock']['seconds'] += 1
                if game_state['clock']['seconds'] >= 60:
                    game_state['clock']['seconds'] = 0
                    game_state['clock']['minutes'] += 1
                    if game_state['clock']['minutes'] >= 60:
                        game_state['clock']['minutes'] = 0
                        game_state['clock']['hours'] += 1
                socketio.emit('clock_tick', game_state['clock'], namespace='/volleyball')
        except Exception:
            # Never let the loop die - a crash here would freeze the clock forever
            logger.exception('background_timer iteration failed')

@volleyball_bp.route('/')
def controller(): return render_template('volleyball/controller.html')
@volleyball_bp.route('/overlay')
def overlay(): return render_template('volleyball/overlay.html')
@volleyball_bp.route('/player_overlay')
def player_overlay(): return render_template('volleyball/player_overlay.html')
@volleyball_bp.route('/stats_overlay')
def stats_overlay(): return render_template('volleyball/stats_overlay.html')
@volleyball_bp.route('/replay_overlay')
def replay_overlay(): return render_template('volleyball/replay_overlay.html')

@socketio.on('connect', namespace='/volleyball')
def handle_connect():
    global timer_thread
    if timer_thread is None:
        timer_thread = socketio.start_background_task(background_timer)
    socketio.emit('state_updated', game_state, namespace='/volleyball')

@socketio.on('next_set', namespace='/volleyball')
def handle_next_set():
    try:
        start_next_set()
        socketio.emit('state_updated', game_state, namespace='/volleyball')
    except Exception:
        logger.exception('Error handling next_set')

@socketio.on('reset_match', namespace='/volleyball')
def handle_reset_match():
    global game_state
    try:
        for team_key in ('home_team', 'away_team'):
            game_state[team_key]['score'] = 0
            game_state[team_key]['sets_won'] = 0
            game_state[team_key]['timeouts_used'] = 0

        game_state.update({
            'previous_sets': [], 'serving_team_index': 0, 'first_server_current_set': 0,
            'current_set_number': 1, 'set_over': False, 'match_over': False, 'match_winner': None,
            'timeout_visible': False, 'timeout_team': None,
            'home_set_point': False, 'away_set_point': False,
            'home_match_point': False, 'away_match_point': False,
        })
        socketio.emit('state_updated', game_state, namespace='/volleyball')
    except Exception:
        logger.exception('Error handling reset_match')

@socketio.on('clock_action', namespace='/volleyball')
def handle_clock(data):
    global game_state
    if not isinstance(data, dict):
        logger.warning('Ignoring malformed clock_action payload: %r', data)
        return
    try:
        action = data.get('action')

        for field in ('hours', 'minutes', 'seconds'):
            if field in data:
                try:
                    game_state['clock'][field] = int(data[field])
                except (TypeError, ValueError):
                    logger.warning('Ignoring invalid clock %s: %r', field, data.get(field))

        if action == 'start': game_state['clock']['running'] = True
        elif action == 'stop': game_state['clock']['running'] = False
        elif action == 'reset':
            game_state['clock']['running'] = False
            game_state['clock']['hours'] = 0
            game_state['clock']['minutes'] = 0
            game_state['clock']['seconds'] = 0

        socketio.emit('state_updated', game_state, namespace='/volleyball')
        socketio.emit('clock_tick', game_state['clock'], namespace='/volleyball')
    except Exception:
        logger.exception('Error handling clock_action')

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

def _apply_score_delta(score_delta):
    """Applies a +1/-1 point change to the current authoritative server state.
    Always computed server-side (never trusts a client-calculated absolute score) so a
    click that arrives right after a set/match just ended can never overwrite a freshly
    reset scoreboard with a stale value."""
    global game_state
    if not isinstance(score_delta, dict):
        logger.warning('Ignoring malformed score_delta: %r', score_delta)
        return

    team = score_delta.get('team')
    if team not in ('home', 'away'):
        logger.warning('Ignoring score_delta with invalid team: %r', team)
        return

    try:
        amount = int(score_delta.get('amount', 0))
    except (TypeError, ValueError):
        logger.warning('Ignoring score_delta with invalid amount: %r', score_delta.get('amount'))
        return

    if amount == 0:
        return

    if game_state.get('set_over', False):
        start_next_set()

    if game_state.get('match_over', False):
        return

    team_key = f'{team}_team'
    scoring_team_index = 0 if team == 'home' else 1
    serving_before = game_state['serving_team_index']

    old_score = game_state[team_key]['score']
    new_score = max(0, old_score + amount)
    game_state[team_key]['score'] = new_score

    # Automatic serve change: only when a point was actually scored (not on undo)
    if new_score > old_score and scoring_team_index != serving_before:
        game_state['serving_team_index'] = scoring_team_index

    check_game_status()

@socketio.on('update_state', namespace='/volleyball')
def handle_update_state(data):
    global game_state
    if not isinstance(data, dict):
        logger.warning('Ignoring malformed update_state payload: %r', data)
        return

    try:
        data = dict(data)
        score_delta = data.pop('score_delta', None)

        if score_delta is not None:
            _apply_score_delta(score_delta)

        if 'timeout_visible' in data:
            game_state['clock']['running'] = not bool(data['timeout_visible'])
            # We emit clock update to clients to show state change
            socketio.emit('clock_tick', game_state['clock'], namespace='/volleyball')

        _safe_merge(game_state, data)

        socketio.emit('state_updated', game_state, namespace='/volleyball')
    except Exception:
        logger.exception('Error handling update_state')

def start_next_set():
    global game_state
    if not game_state.get('set_over', False): return
    if game_state.get('match_over', False): return

    next_server_index = 1 - game_state['first_server_current_set']

    game_state.update({
        'home_team': {**game_state['home_team'], 'score': 0, 'timeouts_used': 0},
        'away_team': {**game_state['away_team'], 'score': 0, 'timeouts_used': 0},
        'current_set_number': game_state['current_set_number'] + 1,
        'serving_team_index': next_server_index,
        'first_server_current_set': next_server_index,
        'set_over': False,
        'home_set_point': False, 'away_set_point': False,
        'home_match_point': False, 'away_match_point': False,
    })

def check_game_status():
    global game_state
    game_state.update({'home_set_point': False, 'away_set_point': False, 'home_match_point': False, 'away_match_point': False})

    if game_state.get('set_over', False): return

    home_score = game_state['home_team']['score']
    away_score = game_state['away_team']['score']

    is_tie_break = game_state['current_set_number'] == 5
    win_score = 15 if is_tie_break else 25

    winner = None
    if home_score >= win_score and home_score >= away_score + 2: winner = 'home_team'
    elif away_score >= win_score and away_score >= home_score + 2: winner = 'away_team'

    if winner:
        game_state['previous_sets'].append({'home': home_score, 'away': away_score})
        game_state['set_over'] = True
        # Sets won (and match-over) are resolved immediately, not deferred to "next set",
        # otherwise a match-clinching final set would never update since there is no next set.
        game_state[winner]['sets_won'] += 1
        if game_state[winner]['sets_won'] >= SETS_TO_WIN_MATCH:
            game_state['match_over'] = True
            game_state['match_winner'] = winner
        return

    point_threshold = win_score - 1

    if home_score >= point_threshold and home_score > away_score:
        game_state['home_set_point'] = True
        if game_state['home_team']['sets_won'] >= 2: game_state['home_match_point'] = True

    elif away_score >= point_threshold and away_score > home_score:
        game_state['away_set_point'] = True
        if game_state['away_team']['sets_won'] >= 2: game_state['away_match_point'] = True

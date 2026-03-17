import os
from flask import render_template
from app import socketio
from . import volleyball_bp

game_state = {
    'home_team': {
        'name': 'HOME TEAM', 'abbr': 'HOM', 'logo': '',
        'score': 0, 'sets_won': 0, 'color': '#3b82f6', 'players': []
    },
    'away_team': {
        'name': 'AWAY TEAM', 'abbr': 'AWY', 'logo': '',
        'score': 0, 'sets_won': 0, 'color': '#ef4444', 'players': []
    },
    'clock': {
        'hours': 0, 'minutes': 0, 'seconds': 0, 'running': False, 
        'period': 'FINAL'
    },
    'previous_sets': [], 'serving_team_index': 0, 'first_server_current_set': 0,
    'view_mode': 'compact', 'timeout_visible': False, 'current_set_number': 1,
    'is_visible': True, 'set_over': False,
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
    start_next_set()
    socketio.emit('state_updated', game_state, namespace='/volleyball')

@socketio.on('clock_action', namespace='/volleyball')
def handle_clock(data):
    global game_state
    action = data.get('action')
    
    if 'hours' in data: game_state['clock']['hours'] = int(data['hours'])
    if 'minutes' in data: game_state['clock']['minutes'] = int(data['minutes'])
    if 'seconds' in data: game_state['clock']['seconds'] = int(data['seconds'])

    if action == 'start': game_state['clock']['running'] = True
    elif action == 'stop': game_state['clock']['running'] = False
    elif action == 'reset':
        game_state['clock']['running'] = False
        game_state['clock']['hours'] = 0
        game_state['clock']['minutes'] = 0
        game_state['clock']['seconds'] = 0
    
    socketio.emit('state_updated', game_state, namespace='/volleyball')
    socketio.emit('clock_tick', game_state['clock'], namespace='/volleyball')

@socketio.on('update_state', namespace='/volleyball')
def handle_update_state(data):
    global game_state

    is_score_update = ('home_team' in data and 'score' in data['home_team']) or \
                      ('away_team' in data and 'score' in data['away_team'])

    if is_score_update:
        old_home_score = game_state['home_team']['score']
        old_away_score = game_state['away_team']['score']
        serving_team_index_before_update = game_state['serving_team_index']

    if game_state.get('set_over', False) and is_score_update:
        start_next_set()

    for key, value in data.items():
        if isinstance(value, dict) and key in game_state:
            if key == 'home_team' or key == 'away_team':
                if 'players' in value:
                    game_state[key]['players'] = value['players']
                game_state[key].update(value)
            else:
                game_state[key].update(value)
        else:
            game_state[key] = value

    if is_score_update:
        new_home_score = game_state['home_team']['score']
        new_away_score = game_state['away_team']['score']
        
        # Automatic serve change
        scoring_team_index = -1
        if new_home_score > old_home_score: scoring_team_index = 0
        elif new_away_score > old_away_score: scoring_team_index = 1
        
        if scoring_team_index != -1 and scoring_team_index != serving_team_index_before_update:
            game_state['serving_team_index'] = scoring_team_index

        check_game_status()

    socketio.emit('state_updated', game_state, namespace='/volleyball')

def start_next_set():
    global game_state
    if not game_state.get('set_over', False): return

    home_score = game_state['home_team']['score']
    away_score = game_state['away_team']['score']
    
    if home_score > away_score: game_state['home_team']['sets_won'] += 1
    elif away_score > home_score: game_state['away_team']['sets_won'] += 1

    next_server_index = 1 - game_state['first_server_current_set']

    game_state.update({
        'home_team': {**game_state['home_team'], 'score': 0},
        'away_team': {**game_state['away_team'], 'score': 0},
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
        return

    point_threshold = win_score - 1
    
    if home_score >= point_threshold and home_score > away_score:
        game_state['home_set_point'] = True
        if game_state['home_team']['sets_won'] == 2: game_state['home_match_point'] = True

    elif away_score >= point_threshold and away_score > home_score:
        game_state['away_set_point'] = True
        if game_state['away_team']['sets_won'] == 2: game_state['away_match_point'] = True

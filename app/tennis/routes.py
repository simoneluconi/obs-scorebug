import os
from flask import render_template
from app import socketio
from . import tennis_bp

game_state = {
    'home_team': {
        'name': 'HOME PLAYER', 'abbr': 'HOM', 'logo': '',
        'sets': 0, 'games': 0, 'score': '0', 'color': '#1d4ed8', 'serving': True,
        'stats': {'aces': 0, 'winners': 0, 'unforced': 0, 'double_faults': 0},
        'players': [] # Useful for doubles or Davis Cup
    },
    'away_team': {
        'name': 'AWAY PLAYER', 'abbr': 'AWY', 'logo': '',
        'sets': 0, 'games': 0, 'score': '0', 'color': '#b91c1c', 'serving': False,
        'stats': {'aces': 0, 'winners': 0, 'unforced': 0, 'double_faults': 0},
        'players': []
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
    'current_lineup_type': 'starter'
}

timer_thread = None

def background_timer():
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
            socketio.emit('clock_tick', game_state['clock'], namespace='/tennis')

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

@socketio.on('update_state', namespace='/tennis')
def handle_update_state(data):
    global game_state
    for key, value in data.items():
        if isinstance(value, dict) and key in game_state:
            game_state[key].update(value)
        else:
            game_state[key] = value
    socketio.emit('state_updated', game_state, namespace='/tennis')

@socketio.on('clock_action', namespace='/tennis')
def handle_clock(data):
    global game_state
    action = data.get('action')
    
    if 'hours' in data: game_state['clock']['hours'] = int(data['hours'])
    if 'minutes' in data: game_state['clock']['minutes'] = int(data['minutes'])
    if 'seconds' in data: game_state['clock']['seconds'] = int(data['seconds'])

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

@socketio.on('trigger_event', namespace='/tennis')
def handle_event(data):
    global game_state
    game_state['current_event'] = data
    game_state['visibility']['lower_third'] = True
    socketio.emit('state_updated', game_state, namespace='/tennis')

@socketio.on('hide_event', namespace='/tennis')
def hide_event():
    global game_state
    game_state['visibility']['lower_third'] = False
    socketio.emit('state_updated', game_state, namespace='/tennis')

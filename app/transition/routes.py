import json
import logging
import os
import uuid

from flask import current_app, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from app import socketio
from . import transition_bp

logger = logging.getLogger(__name__)

ALLOWED_LOGO_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.svg', '.webp', '.gif'}

DEFAULT_SETTINGS = {
    'panel_color_1': '#003884',
    'panel_color_2': '#ffffff',
    'logo_filename': '',
}

# In-memory cache of the persisted settings, mirroring the pattern used by the
# sport modules' game_state - the file on disk is the durable copy, this is
# what's actually served to clients without a disk read on every request.
settings = dict(DEFAULT_SETTINGS)


def _settings_path():
    return os.path.join(current_app.instance_path, 'transition_settings.json')


def _uploads_dir():
    path = os.path.join(current_app.instance_path, 'uploads', 'transition')
    os.makedirs(path, exist_ok=True)
    return path


def load_settings():
    """Loads persisted settings from the instance folder, if present. The instance
    folder is gitignored, so this file (and any uploaded team logo it references)
    never gets committed to the repo."""
    global settings
    try:
        with open(_settings_path(), 'r', encoding='utf-8') as f:
            saved = json.load(f)
        settings = {**DEFAULT_SETTINGS, **saved}
    except FileNotFoundError:
        settings = dict(DEFAULT_SETTINGS)
    except Exception:
        logger.exception('Failed to load transition settings, using defaults')
        settings = dict(DEFAULT_SETTINGS)


def save_settings():
    try:
        os.makedirs(current_app.instance_path, exist_ok=True)
        with open(_settings_path(), 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
    except Exception:
        logger.exception('Failed to persist transition settings')


@transition_bp.route('/')
def controller():
    load_settings()
    return render_template('transition/controller.html')


@transition_bp.route('/overlay')
def overlay():
    load_settings()
    return render_template('transition/overlay.html')


@transition_bp.route('/uploads/<path:filename>')
def uploaded_logo(filename):
    return send_from_directory(_uploads_dir(), filename)


@transition_bp.route('/upload_logo', methods=['POST'])
def upload_logo():
    file = request.files.get('logo')
    if not file or not file.filename:
        return jsonify({'error': 'No file provided'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        return jsonify({'error': 'Unsupported file type'}), 400

    filename = f'{uuid.uuid4().hex}{ext}'
    filename = secure_filename(filename)
    file.save(os.path.join(_uploads_dir(), filename))

    # Remove the previously uploaded logo so team images never pile up on disk.
    old_filename = settings.get('logo_filename')
    if old_filename:
        try:
            os.remove(os.path.join(_uploads_dir(), old_filename))
        except OSError:
            pass

    settings['logo_filename'] = filename
    save_settings()
    socketio.emit('settings_updated', settings, namespace='/transition')
    return jsonify(settings)


@socketio.on('connect', namespace='/transition')
def handle_connect():
    load_settings()
    socketio.emit('settings_updated', settings, namespace='/transition')


@socketio.on('update_settings', namespace='/transition')
def handle_update_settings(data):
    if not isinstance(data, dict):
        logger.warning('Ignoring malformed update_settings payload: %r', data)
        return
    for key in ('panel_color_1', 'panel_color_2'):
        if key in data and isinstance(data[key], str):
            settings[key] = data[key]
    save_settings()
    socketio.emit('settings_updated', settings, namespace='/transition')


@socketio.on('play_transition', namespace='/transition')
def handle_play_transition():
    socketio.emit('play_transition', namespace='/transition')

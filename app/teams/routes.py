import json
import logging
import os
import uuid

from flask import current_app, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from . import teams_bp

logger = logging.getLogger(__name__)

ALLOWED_LOGO_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.svg', '.webp', '.gif'}
VALID_SPORTS = {'soccer', 'tennis', 'volleyball'}
UPLOADS_PREFIX = '/teams/uploads/'


def _store_path():
    return os.path.join(current_app.instance_path, 'teams.json')


def _uploads_dir():
    path = os.path.join(current_app.instance_path, 'uploads', 'teams')
    os.makedirs(path, exist_ok=True)
    return path


def load_presets():
    """Loads saved team presets from the instance folder. That folder is
    gitignored, so preset data and any uploaded team logo never get committed."""
    try:
        with open(_store_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception('Failed to load team presets, starting empty')
        return []


def save_presets(presets):
    try:
        os.makedirs(current_app.instance_path, exist_ok=True)
        with open(_store_path(), 'w', encoding='utf-8') as f:
            json.dump(presets, f, indent=2)
    except Exception:
        logger.exception('Failed to persist team presets')


def _delete_uploaded_logo(logo_url):
    if logo_url and logo_url.startswith(UPLOADS_PREFIX):
        try:
            os.remove(os.path.join(_uploads_dir(), os.path.basename(logo_url)))
        except OSError:
            pass


@teams_bp.route('/')
def controller():
    return render_template('teams/controller.html')


@teams_bp.route('/uploads/<path:filename>')
def uploaded_logo(filename):
    return send_from_directory(_uploads_dir(), filename)


@teams_bp.route('/api/list')
def api_list():
    sport = request.args.get('sport')
    presets = load_presets()
    if sport:
        presets = [p for p in presets if p.get('sport') == sport]
    return jsonify(presets)


@teams_bp.route('/api/presets', methods=['POST'])
def api_create():
    payload = request.get_json(silent=True) or {}
    sport = payload.get('sport')
    label = (payload.get('label') or '').strip()
    data = payload.get('data')

    if sport not in VALID_SPORTS:
        return jsonify({'error': 'invalid sport'}), 400
    if not label:
        return jsonify({'error': 'label is required'}), 400
    if not isinstance(data, dict):
        return jsonify({'error': 'invalid data'}), 400

    preset = {'id': uuid.uuid4().hex, 'sport': sport, 'label': label, 'data': data}
    presets = load_presets()
    presets.append(preset)
    save_presets(presets)
    return jsonify(preset), 201


@teams_bp.route('/api/presets/<preset_id>', methods=['PUT'])
def api_update(preset_id):
    payload = request.get_json(silent=True) or {}
    presets = load_presets()
    preset = next((p for p in presets if p['id'] == preset_id), None)
    if not preset:
        return jsonify({'error': 'not found'}), 404

    if payload.get('label'):
        preset['label'] = payload['label'].strip()
    if isinstance(payload.get('data'), dict):
        preset['data'] = payload['data']

    save_presets(presets)
    return jsonify(preset)


@teams_bp.route('/api/presets/<preset_id>', methods=['DELETE'])
def api_delete(preset_id):
    presets = load_presets()
    preset = next((p for p in presets if p['id'] == preset_id), None)
    if not preset:
        return jsonify({'error': 'not found'}), 404

    _delete_uploaded_logo(preset.get('data', {}).get('logo', ''))
    presets = [p for p in presets if p['id'] != preset_id]
    save_presets(presets)
    return '', 204


@teams_bp.route('/api/presets/<preset_id>/logo', methods=['POST'])
def api_upload_logo(preset_id):
    presets = load_presets()
    preset = next((p for p in presets if p['id'] == preset_id), None)
    if not preset:
        return jsonify({'error': 'not found'}), 404

    file = request.files.get('logo')
    if not file or not file.filename:
        return jsonify({'error': 'no file provided'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        return jsonify({'error': 'unsupported file type'}), 400

    filename = secure_filename(f'{uuid.uuid4().hex}{ext}')
    file.save(os.path.join(_uploads_dir(), filename))

    _delete_uploaded_logo(preset.get('data', {}).get('logo', ''))
    preset.setdefault('data', {})['logo'] = f'{UPLOADS_PREFIX}{filename}'
    save_presets(presets)
    return jsonify(preset)

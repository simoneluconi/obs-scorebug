from flask import Blueprint

teams_bp = Blueprint('teams', __name__, template_folder='templates')

from . import routes

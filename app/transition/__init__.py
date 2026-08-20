from flask import Blueprint

transition_bp = Blueprint('transition', __name__, template_folder='templates')

from . import routes

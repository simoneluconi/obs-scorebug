from flask import Blueprint

tennis_bp = Blueprint('tennis', __name__, template_folder='templates')

from . import routes

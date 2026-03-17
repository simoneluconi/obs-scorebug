from flask import Blueprint

soccer_bp = Blueprint('soccer', __name__, template_folder='templates')

from . import routes

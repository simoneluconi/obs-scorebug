from flask import Blueprint

volleyball_bp = Blueprint('volleyball', __name__, template_folder='templates')

from . import routes

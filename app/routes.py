from flask import render_template, Blueprint

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    sports = [
        {'name': 'Soccer', 'url': 'soccer', 'icon': 'sports_soccer'},
        {'name': 'Tennis', 'url': 'tennis', 'icon': 'sports_tennis'},
        {'name': 'Volleyball', 'url': 'volleyball', 'icon': 'sports_volleyball'}
    ]
    return render_template('index.html', sports=sports)

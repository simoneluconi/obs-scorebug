from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO(async_mode='threading')

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'secret!' # You should change this!

    socketio.init_app(app)

    with app.app_context():
        from . import routes
        
        # Import and register blueprints
        from .soccer.routes import soccer_bp
        from .tennis.routes import tennis_bp
        from .volleyball.routes import volleyball_bp
        from .transition.routes import transition_bp

        app.register_blueprint(routes.main_bp)
        app.register_blueprint(soccer_bp, url_prefix='/soccer')
        app.register_blueprint(tennis_bp, url_prefix='/tennis')
        app.register_blueprint(volleyball_bp, url_prefix='/volleyball')
        app.register_blueprint(transition_bp, url_prefix='/transition')

    return app

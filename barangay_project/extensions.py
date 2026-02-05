"""Flask extensions.

Keeping extension instances in a dedicated module prevents circular imports
and (most importantly) avoids accidentally creating *multiple* SQLAlchemy
instances across the codebase.

Import these objects everywhere:

    from .extensions import db, login_manager, mail, csrf
"""

from flask_login import LoginManager
from flask_mail import Mail
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect


db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()
csrf = CSRFProtect()

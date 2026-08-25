# This file is required by PythonAnywhere's Web App configuration.
# It tells PythonAnywhere's web server where to find the Flask application.
#
# In your PythonAnywhere Web App settings, set "Source code" to
# /home/<yourusername>/r-lebanese-screening-bot
# and set "WSGI configuration file" to point to this file.
#
# IMPORTANT: PythonAnywhere auto-generates a WSGI file at
# /var/www/<yourusername>_pythonanywhere_com_wsgi.py
# You must EDIT that file to contain the contents below.

import sys
import os

# Add your project directory to the Python path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Load environment variables from .env BEFORE importing the app
from dotenv import load_dotenv
load_dotenv(os.path.join(project_home, '.env'))

# Import the Flask app (this also starts the PTB background thread)
from bot import flask_app as application

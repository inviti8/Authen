"""WSGI entrypoint: gunicorn -w 2 -b 127.0.0.1:8402 wsgi:app"""

from authen.web.app import create_app

app = create_app()

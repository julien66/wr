"""Point d'entrée pour un serveur de production :

    RANKING_DB=/chemin/ranking.db RANKING_PROXY=1 gunicorn -w 3 -b 127.0.0.1:8000 wsgi:application
"""
import os

from app import create_app

application = create_app(os.environ.get("RANKING_DB", "ranking.db"), proxy=os.environ.get("RANKING_PROXY") == "1")

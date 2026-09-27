import os

from app.config import get_settings

# Point the app at the test database before anything creates an engine.
os.environ["DATABASE_URL"] = get_settings().test_database_url
get_settings.cache_clear()

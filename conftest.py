from __future__ import annotations

import os
import sys

os.environ.setdefault("USE_SQLITE", "1")
os.environ.setdefault("EMBEDDING_DIMENSIONS", "384")
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://localhost")
os.environ.setdefault("LLM_MODEL_NAME", "test-model")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()


def pytest_configure(config):
    sys.modules.setdefault("agent_tools_placeholder", type(sys)("agent_tools_placeholder"))

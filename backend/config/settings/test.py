from .base import *


# Test runs must not inherit production static-file behavior from the host
# environment.  Templates reference source static assets directly in tests.
DEBUG = True
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

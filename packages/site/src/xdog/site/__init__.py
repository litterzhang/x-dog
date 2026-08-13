"""xdog site — Flask app exposing package overviews, a blog, and an FAQ.

Import :func:`create_app` for WSGI servers::

    from xdog.site import create_app
"""

from xdog.site.app import create_app

__all__ = ["create_app"]

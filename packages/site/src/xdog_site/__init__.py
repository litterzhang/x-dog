"""x-dog site — Flask app exposing package overviews, a blog, and an FAQ.

Import :func:`create_app` for WSGI servers::

    from xdog_site import create_app
"""

from xdog_site.app import create_app

__all__ = ["create_app"]

"""Typed aiohttp AppKey constants shared between server.py and routes.py."""
from aiohttp import web

# Typed key for storing handler dependencies in aiohttp Application config.
# Using AppKey suppresses the NotAppKeyWarning from aiohttp.
DEPS_KEY: web.AppKey[dict] = web.AppKey("deps")

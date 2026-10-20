# Django + Channels, with conversation turns over WebSocket

We use Django (async views, django-ninja for HTTP, Channels for WebSocket) instead of FastAPI because the target team's stack is Django/Channels/Celery. Conversation turns travel over a WebSocket from v0, not over HTTP request/response: it is the direct path to real-time voice, and it lets the server push claim updates and background-task results (such as the summary) to the live panel. The domain and engine packages don't depend on Django, so the framework stays at the edges.

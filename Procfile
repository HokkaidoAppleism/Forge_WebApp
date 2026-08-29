# Root Procfile: the app itself is in api/, so the start command changes into
# that directory before launching gunicorn (which pip has put on PATH).
#
# app:app -- a plain module-level WSGI object -- not the app:create_app factory:
# gunicorn's --factory flag is not in every gunicorn version and the one Railway
# installed did not have it (see the "Fix Railway 502" commit).
#
# --timeout 30 gives a worker room to boot: db.py opens the connection pool at
# import and waits briefly for the first connection. The pool no longer treats
# a slow first connection as fatal, so a cold database delays boot rather than
# crash-looping it.
web: cd api && gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 30

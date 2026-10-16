"""Movie statistics sync: Trakt history → taste profile → website snapshot, run inside admin.

Ported from the bot's worker (trakt.js, taste.js, statistics.js, depth.js). Field names and
ordering follow the JavaScript version exactly, so snapshots and the cached TMDB details stay
interchangeable; see tests/test_movie_sync.py.
"""

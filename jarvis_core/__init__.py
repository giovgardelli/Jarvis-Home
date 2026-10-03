"""
jarvis_core — code shared by the voice loop (jarvis-pi.py) and the agents.

The voice loop runs from the repo root, so this package is importable as-is.
Agent entrypoints (agents/<name>/pi_run.py) add the repo root to sys.path
before importing anything else.
"""

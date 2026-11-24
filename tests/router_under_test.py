"""Single place that says which module is "the router" for the tests."""
import os
import tempfile

from helpers import install_stubs

_router = None


def get_router():
    global _router
    if _router is None:
        install_stubs()
        os.environ.setdefault("JARVIS_SECRET", "test-secret")
        os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
        os.environ.setdefault("JARVIS_STATE_DB", os.path.join(tempfile.mkdtemp(), "state.db"))
        from jarvis_core import router as r
        r._log_routing_decision = lambda *a, **k: None
        # keep test logs out of the real home folder
        tmp = tempfile.mkdtemp()
        r.ROUTING_LOG_PATH = os.path.join(tmp, "routing_log.csv")
        r.ROUTER_SHADOW_PATH = os.path.join(tmp, "router_shadow.csv")
        _router = r
    return _router

"""
Shared test helpers.

The suite uses only the standard library (unittest), so it runs anywhere —
on the Pi with the real dependencies installed, or in a bare container.
install_stubs() adds minimal stand-ins ONLY for libraries that aren't
importable, so on the Pi the real modules are used.
"""
import importlib.util
import os
import sys
import types
from contextlib import contextmanager

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")


def _missing(name):
    try:
        __import__(name)
        return False
    except Exception:
        return True


def _module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


def install_stubs():
    if REPO not in sys.path:
        sys.path.insert(0, REPO)

    if _missing("pytz"):
        from zoneinfo import ZoneInfo
        _module("pytz", timezone=lambda name: ZoneInfo(name))

    if _missing("anthropic"):
        class Anthropic:
            def __init__(self, **kwargs):
                self.messages = types.SimpleNamespace(create=self._unpatched)
                self.models = types.SimpleNamespace(list=self._unpatched)

            def _unpatched(self, **kwargs):
                raise RuntimeError("anthropic stub: patch client.messages.create in the test")
        _module("anthropic", Anthropic=Anthropic)

    if _missing("requests"):
        class RequestException(Exception):
            pass

        class HTTPError(RequestException):
            def __init__(self, *a, response=None, **k):
                super().__init__(*a)
                self.response = response

        exceptions = types.SimpleNamespace(
            RequestException=RequestException, Timeout=type("Timeout", (RequestException,), {}),
            ConnectionError=type("ConnectionError", (RequestException,), {}), HTTPError=HTTPError,
        )

        def _no_network(*a, **k):
            raise exceptions.ConnectionError("requests stub: no network in tests")
        _module("requests", exceptions=exceptions, post=_no_network, get=_no_network,
                RequestException=RequestException, HTTPError=HTTPError)

    if _missing("flask"):
        class Flask:
            def __init__(self, *a, **k):
                pass

            def app_context(self):
                from contextlib import nullcontext
                return nullcontext()

            def route(self, *a, **k):
                return lambda f: f

            def run(self, *a, **k):
                pass
        _module("flask", Flask=Flask, jsonify=lambda *a, **k: (a[0] if a else k),
                request=types.SimpleNamespace(headers={}, get_json=lambda: {}, json=None))

    if _missing("dotenv"):
        _module("dotenv", load_dotenv=lambda *a, **k: None)

    if _missing("yaml"):
        _module("yaml", safe_load=lambda f: {})

    if _missing("google.genai"):
        google = sys.modules.get("google") or _module("google")
        if not hasattr(google, "__path__"):
            google.__path__ = []
        gtypes = _module("google.genai.types", GenerateContentConfig=lambda **k: k,
                         Tool=lambda **k: k, GoogleSearch=lambda **k: "google_search")

        class Client:
            def __init__(self, **k):
                self.models = types.SimpleNamespace(generate_content=self._unpatched, list=lambda: [])

            def _unpatched(self, **k):
                raise RuntimeError("genai stub: patch generate_content in the test")
        genai = _module("google.genai", Client=Client, types=gtypes)
        google.genai = genai

    if _missing("apscheduler.schedulers.background"):
        class BackgroundScheduler:
            def __init__(self, **k):
                pass

            def add_job(self, *a, **k):
                pass

            def start(self):
                pass

            def shutdown(self, *a, **k):
                pass
        _module("apscheduler")
        _module("apscheduler.schedulers")
        _module("apscheduler.schedulers.background", BackgroundScheduler=BackgroundScheduler)

    if _missing("gspread"):
        _module("gspread", authorize=lambda creds: None)

    if _missing("google.oauth2.credentials"):
        google = sys.modules.get("google") or _module("google")
        if not hasattr(google, "__path__"):
            google.__path__ = []
        oauth2 = _module("google.oauth2")
        oauth2.__path__ = []
        oauth2.credentials = _module("google.oauth2.credentials", Credentials=lambda **k: types.SimpleNamespace(**k))
        google.oauth2 = oauth2

    if _missing("googleapiclient.discovery"):
        _module("googleapiclient").__path__ = []
        _module("googleapiclient.discovery", build=lambda *a, **k: None)

    if _missing("dateutil.relativedelta"):
        _module("dateutil").__path__ = []
        _module("dateutil.relativedelta", relativedelta=lambda **k: None)


def have(name):
    """True if the REAL library is importable (not one of our stubs)."""
    mod = sys.modules.get(name)
    if mod is not None and getattr(mod, "__file__", None) is None:
        return False
    return not _missing(name)


@contextmanager
def isolated_modules(fakes=None, path=None, drop=()):
    """Temporarily install fake modules / a sys.path entry, and forget any
    modules listed in `drop` (and the fakes) afterwards, so agents with
    same-named files (agent.py, main.py, sheets.py) don't collide."""
    fakes = fakes or {}
    names = set(fakes) | set(drop)
    saved = {n: sys.modules.get(n) for n in names}
    for n in drop:
        sys.modules.pop(n, None)
    sys.modules.update(fakes)
    if path:
        sys.path.insert(0, path)
    try:
        yield
    finally:
        if path and path in sys.path:
            sys.path.remove(path)
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


def load_file(path, name):
    """Import a .py file under a unique module name."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_message(text):
    """Shape of an Anthropic Messages API response with one text block."""
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)])

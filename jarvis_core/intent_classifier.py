"""
intent_classifier.py — a tiny, local routing model trained on your own requests.

Learns which agent your household's phrasing goes to from routing_log.csv
(every routing decision Jarvis makes is logged there) plus the example
phrases in registry.json. Pure Python, no extra packages, a few milliseconds
per request on a Pi.

How it decides: each request becomes a TF-IDF vector of its words and word
pairs; the 5 most similar past requests vote, weighted by similarity (only
those reasonably close to the best match — distant ones that merely share
filler words like "the" don't get a say).
  confidence = share of the vote the winning agent got (0-1)
  similarity = how close the single nearest past request is (0-1)
The router only trusts it when both are high (see router "Local routing
classifier"); anything else still goes to Claude.
"""
import csv
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict

K_NEIGHBOURS = 5
VOTE_CUTOFF = 0.5    # only neighbours at least half as similar as the best one vote
RETRAIN_EVERY = 600  # seconds; retrains when routing_log.csv has changed

_WORD = re.compile(r"[a-z0-9']+")


def _tokens(text):
    words = _WORD.findall(text.lower().replace("’", "'"))
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


class IntentClassifier:
    def __init__(self, examples):
        """examples: [(text, label)]"""
        self.examples = []
        docs = [(Counter(_tokens(t)), label) for t, label in examples if t.strip()]
        n = len(docs)
        df = Counter(term for counts, _ in docs for term in counts)
        self.idf = {term: math.log((1 + n) / (1 + d)) + 1 for term, d in df.items()}
        for counts, label in docs:
            self.examples.append((self._vector(counts), label))

    def _vector(self, counts):
        vec = {t: c * self.idf.get(t, 0.0) for t, c in counts.items() if t in self.idf}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def predict(self, text):
        """(label, confidence, similarity), or (None, 0, 0) with no usable data."""
        if not self.examples:
            return None, 0.0, 0.0
        query = self._vector(Counter(_tokens(text)))
        if not query:
            return None, 0.0, 0.0
        scored = []
        for vec, label in self.examples:
            small, large = (query, vec) if len(query) < len(vec) else (vec, query)
            sim = sum(v * large.get(t, 0.0) for t, v in small.items())
            if sim > 0:
                scored.append((sim, label))
        if not scored:
            return None, 0.0, 0.0
        scored.sort(reverse=True)
        top = [(sim, label) for sim, label in scored[:K_NEIGHBOURS] if sim >= VOTE_CUTOFF * scored[0][0]]
        votes = defaultdict(float)
        for sim, label in top:
            votes[label] += sim
        label = max(votes, key=votes.get)
        return label, votes[label] / sum(votes.values()), top[0][0]


def training_examples(log_path, registry_agents, allowed_labels, keyword_label):
    """Examples from routing_log.csv + registry example phrases.

    - Rows decided by Claude ("haiku") are trusted as labels.
    - Rows decided by keyword rules ("local") are relabelled with *today's*
      rules (keyword_label(text) → label or None), so decisions made by older,
      buggier rules don't teach the model mistakes.
    - Errors and unknown labels are skipped; for repeated texts the latest wins.
    """
    latest = {}
    try:
        with open(log_path, newline="") as f:
            for row in csv.DictReader(f):
                text = (row.get("text") or "").strip()
                method, agent = row.get("method"), row.get("agent")
                if not text:
                    continue
                if method == "local":
                    agent = keyword_label(text)
                elif method != "haiku":
                    continue
                if agent in allowed_labels:
                    latest[text.lower()] = (text, agent)
    except OSError:
        pass
    examples = list(latest.values())
    for a in registry_agents:
        examples += [(p, a["id"]) for p in a.get("example_phrases", []) if a["id"] in allowed_labels]
    return examples


class AutoRetraining:
    """Holds the current model; retrains when the log file changes."""

    def __init__(self, log_path, build_examples):
        """log_path: a path, or a function returning one (so it can change)."""
        self.log_path = log_path
        self.build_examples = build_examples
        self._model = None
        self._trained_mtime = None
        self._trained_at = 0.0
        self._lock = threading.Lock()

    def _mtime(self):
        path = self.log_path() if callable(self.log_path) else self.log_path
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def model(self):
        with self._lock:
            stale = (self._model is None or
                     (self._mtime() != self._trained_mtime and time.time() - self._trained_at > RETRAIN_EVERY))
            if stale:
                self._trained_mtime = self._mtime()
                self._trained_at = time.time()
                self._model = IntentClassifier(self.build_examples())
            return self._model

    def predict(self, text):
        return self.model().predict(text)

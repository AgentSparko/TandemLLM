"""BM25 over documents with weighted fields (ENG-413 WP2c: the index Wren's knowledge searches).

Words are lower-case runs of letters and digits, a plural `s` dropped, a few function words left
out; no embeddings. `TextIndex.build(docs, weights)` indexes a list of documents (each a dict of
field -> text, `weights` how often a field's words count); `scores(query)` answers every
document's score in one pass over the query words' postings.

A copy of TandemApp's tandemapp/conductor/libsearch.py `TextIndex` with the words it reads (`K1`,
`B`, `_WORD`, `STOP`, `words`); server/setup/knowledge.py builds Wren's index with it. The
package library and the memory notes keep using TandemApp's.
"""

from __future__ import annotations

import math
import re

K1, B = 1.2, 0.75


_WORD = re.compile(r"[a-z0-9]+")


STOP = frozenset((
    "a an and are as at be but by can do does for from has have how i if in into is it its me my "
    "of on or our so than that the their then there these this to up use using was we what when "
    "where which while who will with you your").split())


def words(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w not in STOP:
            out.append(w)
    return out


class TextIndex:
    """BM25 over a list of documents with weighted fields: the one index class the library's
    skills (`SkillIndex`) and the memory notes (server/conductor/notes.py) share. A document is
    a dict of field -> text; `weights` says how often a field's words count. Words are `words()`.
    Built whole (the corpora are hundreds of documents at most); `scores()` is one pass over the
    query words' postings."""

    def __init__(self):
        self._post: dict[str, list[tuple[int, float]]] = {}
        self._len: list[float] = []
        self._avg = 1.0
        self.n = 0

    def build(self, docs: list[dict], weights: dict) -> None:
        post: dict[str, dict[int, float]] = {}
        lens = []
        for i, d in enumerate(docs):
            tf: dict[str, float] = {}
            for field, w in weights.items():
                for t in words(d.get(field) or ""):
                    tf[t] = tf.get(t, 0.0) + w
            lens.append(sum(tf.values()))
            for t, f in tf.items():
                post.setdefault(t, {})[i] = f
        self._post = {t: list(d.items()) for t, d in post.items()}
        self._len = lens
        self._avg = (sum(lens) / len(lens)) if lens else 1.0
        self.n = len(docs)

    def scores(self, query: str) -> list[float]:
        """Every document's BM25 score for `query` (each query word once)."""
        n = self.n
        score = [0.0] * n
        if not n:
            return score
        for t in set(words(query)):
            post = self._post.get(t)
            if not post:
                continue
            idf = math.log(1.0 + (n - len(post) + 0.5) / (len(post) + 0.5))
            for i, f in post:
                score[i] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * self._len[i] / self._avg))
        return score

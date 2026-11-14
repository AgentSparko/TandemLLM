"""A typed question over a session the shadow reader holds (ENG-413 WP5b): the choices as
letters, the question turn after the conversation, the letters' softmax.

Each choice is a letter, every letter one token; the question goes in a hidden user turn with the
template's closed think block, and the logits of its last row over the letters are the answer: its
softmax the constrained distribution, the label its argmax. server/conductor/shadow.py asks it for
`POST /v1/engine/shadow/ask` (engine API 1.5, served with or without an extension) and for the
router's question an extension hands in (engine-ext-v1 3.1 `shadow_question`). Copies of TandemApp's
tandemapp/conductor/incontext.py names (the conductor's in-context stage asks Qwen the same form),
held to it by tests/test_api_import_guard.py.
"""

from __future__ import annotations

import math
import string

from server.api import inject


LETTERS = string.ascii_uppercase


# the hidden turn after the conversation, as the template writes a user turn and the generation
# prompt with thinking off; the conversation's own `<|im_end|>` comes before it
TURN = "\n<|im_start|>user\n{question}<|im_end|>\n<|im_start|>assistant\n" + inject.THINK_OFF


class ContextError(ValueError):
    """The stage cannot be asked on this prompt; `why` goes into the journal's stage error."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


def listing(options: dict[str, str]) -> tuple[str, list[str]]:
    """`A: id - description` lines and the letter of each option, in the options' order."""
    ids = list(options)
    if len(ids) > len(LETTERS):
        raise ContextError("too_many_options")
    lines = [f"{LETTERS[i]}: {k} - {options[k]}" for i, k in enumerate(ids)]
    return "\n".join(lines), [LETTERS[i] for i in range(len(ids))]


def letter_ids(tok, letters: list[str]) -> list[int]:
    """The token of each letter; every one must be a single token, all different."""
    out = []
    for c in letters:
        enc = tok(c, add_special_tokens=False).input_ids
        enc = enc.tolist() if hasattr(enc, "tolist") else list(enc)
        if enc and isinstance(enc[0], list):
            enc = enc[0]
        if len(enc) != 1:
            raise ContextError("letter_not_one_token")
        out.append(int(enc[0]))
    if len(set(out)) != len(out):
        raise ContextError("letters_share_a_token")
    return out


def answer(logprobs: list[float], options: list[str]) -> tuple[str, float, dict[str, float]]:
    """The label, its probability and every option's, from the letters' log-probabilities (the
    softmax over the letters alone: the distribution a `choice` grammar leaves)."""
    if len(logprobs) != len(options) or not logprobs:
        raise ContextError("bad_logprobs")
    top = max(logprobs)
    ex = [math.exp(x - top) for x in logprobs]
    z = sum(ex)
    probs = {k: e / z for k, e in zip(options, ex)}
    label = max(options, key=lambda k: probs[k])
    return label, probs[label], probs

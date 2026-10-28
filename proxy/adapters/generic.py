"""Any OpenAI-compatible server reached by URL: llama.cpp, LM Studio, Ollama, vLLM and SGLang (their
L1 tiles come in P2 and P3), another TandemLLM. Chat, completions and embeddings are relayed; the
usage the server sends is read as the base adapter reads it.

`vllm` and `sglang` are the generic adapter with what the harness (P4) may ask of each, read from
their docs and code (vLLM v0.30.0, SGLang v0.5.21): both prefill an assistant message
(`continue_final_message`), both switch Qwen-family thinking through `chat_template_kwargs`, and
each constrains the class question its own way (vLLM `structured_outputs.choice`, SGLang
`regex`). A server of either kind registered as `generic` relays the same and loses only those
three."""

from __future__ import annotations

from proxy.adapters.base import CHAT, COMPLETIONS, EMBEDDINGS, RESPONSES, Adapter

# SRV-733: what the relay does not pass between a client and our engine: the hop's own headers,
# what the relay writes itself (the body's length and type, the host), a cookie of this server,
# and the headers only this proxy may set (whose memory, its lane id, a deployment's pin)
NOT_FORWARDED = frozenset((
    "connection", "keep-alive", "proxy-connection", "proxy-authorization", "proxy-authenticate",
    "te", "trailer", "transfer-encoding", "upgrade", "expect", "host", "content-length",
    "content-type", "accept", "accept-encoding", "cookie", "x-tandem-user", "x-tandem-relay-id",
    "x-tandem-pin"))
# ... and back: the hop's own, what the relay writes itself, and the facts this proxy states
NOT_PASSED = frozenset((
    "connection", "keep-alive", "transfer-encoding", "content-length", "content-type", "date",
    "server", "cache-control", "x-accel-buffering", "x-tandem-backend", "x-tandem-node",
    "x-tandem-deployment"))


class Generic(Adapter):
    kind = "generic"
    cloud = False

    def routes(self) -> tuple[str, ...]:
        # vLLM and SGLang serve the Responses API too; a server that does not answers its own 404
        return (CHAT, COMPLETIONS, EMBEDDINGS, RESPONSES)


class TandemLLM(Generic):
    """Our own engine at a URL (the standalone proxy in front of it). It puts `usage` on the finish
    chunk unasked, so the stream is not asked for a separate usage frame.

    SRV-733: a client that reached the engine through this proxy is the client the engine would
    have seen. Every header it sent goes on (its agent and referer for the client kind, the
    conductor's and memory's headers, the session headers, `X-Real-IP` and `X-Forwarded-For` as
    the reverse proxy set them, its `Authorization` when the backend has no key of its own)
    except NOT_FORWARDED; every header of the answer comes back except NOT_PASSED. The engine's
    model list is kept whole (`cards`): /v1/models here lists its cards as the engine does."""

    kind = "tandemllm"
    cards = True

    def forward(self, client_headers, key: str | None = None) -> list[tuple[str, str]]:
        try:
            items = list(client_headers.items())
        except Exception:                                          # noqa: BLE001
            return []
        return [(k, v) for k, v in items
                if v and k.lower() not in NOT_FORWARDED
                and not (key and k.lower() == "authorization")]

    def passes(self, name: str) -> bool:
        return name.lower() not in NOT_PASSED

    def capabilities(self) -> dict:
        caps = super().capabilities()
        caps["choice"] = "grammar"
        return caps

    def choice_headers(self) -> list[tuple[str, str]]:
        return [("X-Tandem", "raw")]


class VLLM(Generic):
    kind = "vllm"

    def capabilities(self) -> dict:
        caps = super().capabilities()
        caps.update(choice="guided_choice", continue_final_message=True)
        return caps


class SGLang(Generic):
    kind = "sglang"

    def capabilities(self) -> dict:
        caps = super().capabilities()
        caps.update(choice="regex", continue_final_message=True)
        return caps

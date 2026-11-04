"""LLM server chain for Murmure: an ordered list of OpenAI-compatible chat servers, tried in turn.

The list lives in `servers.json` next to this module (or the file named by `MURMURE_SERVERS_FILE`); see
`servers.example.json`. Each entry:

    {"name": "cloud", "url": "https://openrouter.ai/api", "key_file": "openrouter.key", "timeout": 10,
     "extra": {"model": "deepseek/deepseek-v4-flash-0731"}}

    name      label shown in logs and in the web page
    url       base URL; "/v1/chat/completions" is appended (llama.cpp, Ollama, vLLM, OpenRouter, OpenAI, Groq...)
    key       API key, or key_file (path relative to the instance directory) or key_env (environment variable name).
              A server whose key_file/key_env cannot be read is skipped, so one list works on every machine.
    extra     fields merged into every request body (model, provider options, chat_template_kwargs...)
    timeout   seconds before giving up on a request (default 10, or 180 for a private server: a CPU can be slow)
    private   true = runs at home: the only kind of server that receives sensitive questions (health, bank,
              passwords...). Without any private server, nothing is filtered.
    wake      {"mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255"}: a machine that sleeps. Probed on
              GET /health (0.8 s at most); if it is down a Wake-on-LAN packet is sent (at most one every 90 s)
              and the question goes to the next server without waiting.

Order is priority. A server that fails is skipped for 60 s. The last server of the list is always tried: make it
the one that is always on (typically a small local model). Without a servers file, a single private server is
used: `MURMURE_LLM_URL` (default http://127.0.0.1:8080).
"""
import json, os, re, socket, time, urllib.request
from typing import NamedTuple

from . import HOME

SERVERS_FILE = os.environ.get("MURMURE_SERVERS_FILE", os.path.join(HOME, "servers.json"))
DEFAULT_URL = os.environ.get("MURMURE_LLM_URL", "http://127.0.0.1:8080")
COOLDOWN = 60         # s during which a failed server is avoided
PROBE_TIMEOUT = 0.8   # s: a sleeping machine does not answer, we do not wait
RETRY_AFTER = 5       # s between two probes while a machine is down
WAKE_COOLDOWN = 90    # s between two Wake-on-LAN sends (a PC takes 10 to 20 s to wake up)
# turns off the "thinking" mode of models that have one (Qwen3.5, Kimi K2...); harmless for the others
LOCAL_BODY = {"chat_template_kwargs": {"enable_thinking": False, "thinking": False}}
# Questions about health, money or secrets stay on private servers (French words, like the rest of the rules).
SENSITIVE = re.compile(
    r"\b(santé|médecin|médicaments?|maladies?|symptômes?|diagnostic|ordonnance|dépression|grossesse|enceinte|psy\w*|"
    r"banque|bancaire|iban|carte bleue|code pin|mots? de passe|impôts?|salaire|dettes?|crédit|avocat|divorce)\b", re.I)


def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError(f"invalid MAC address: {mac!r}")
    return b"\xff" * 6 + raw * 16


def send_wol(mac: str, broadcast: str) -> None:
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):  # usual Wake-on-LAN ports
            s.sendto(packet, (broadcast, port))


def http_probe(url: str, headers: dict) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url + "/health", headers=headers), timeout=PROBE_TIMEOUT) as r:
            return r.status == 200 and json.load(r).get("status") == "ok"
    except (OSError, ValueError):
        return False


class Candidate(NamedTuple):
    name: str
    url: str
    headers: dict
    extra: dict     # fields added to the request body
    timeout: int


def is_sensitive(text: str) -> bool:
    return bool(SENSITIVE.search(text))


def _key(s: dict) -> str | None:
    """The server's API key: "" when it needs none, None when it should have one and it cannot be found."""
    if s.get("key"):
        return s["key"]
    try:
        if s.get("key_env"):
            return os.environ[s["key_env"]].strip() or None
        if s.get("key_file"):
            with open(os.path.join(HOME, s["key_file"]), encoding="utf-8") as f:
                return f.read().strip() or None
    except (KeyError, OSError):
        return None
    return ""


def load_servers(path: str = SERVERS_FILE) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return [{"name": "local", "url": DEFAULT_URL, "private": True, "extra": LOCAL_BODY}]


class Backend:
    def __init__(self, servers: list[dict] | None = None, probe=http_probe, wake=send_wol, clock=time.monotonic):
        self.servers = []
        for s in load_servers() if servers is None else servers:
            key = _key(s)
            if key is not None:  # no key to be found: the server is not available on this machine
                self.servers.append({**s, "url": s["url"].rstrip("/"), "headers": {"Authorization": f"Bearer {key}"} if key else {}})
        if not self.servers:
            raise SystemExit("no usable LLM server: check servers.json (see servers.example.json)")
        self.down: dict[str, float] = {}   # name -> instant until which a failed server is avoided
        self.awake: dict[str, dict] = {}   # name -> {"up", "checked", "woke"} for servers with "wake"
        self._probe, self._wake, self._clock = probe, wake, clock
        self.last = self.servers[-1]["name"]

    @staticmethod
    def _candidate(s: dict) -> Candidate:
        return Candidate(s["name"], s["url"], s["headers"], s.get("extra", {}), s.get("timeout", 180 if s.get("private") else 10))

    def fallback(self) -> Candidate:
        """The last server of the list: the one that is always on (used to warm it up)."""
        return self._candidate(self.servers[-1])

    def _is_up(self, s: dict) -> bool:
        """State of a machine that sleeps: probes it, and wakes it up (rarely) when it is down."""
        st = self.awake.setdefault(s["name"], {"up": False, "checked": float("-inf"), "woke": float("-inf")})
        now = self._clock()
        if st["up"] or now - st["checked"] >= RETRY_AFTER:
            st["up"], st["checked"] = self._probe(s["url"], s["headers"]), now
        if not st["up"] and now - st["woke"] >= WAKE_COOLDOWN:
            try:
                self._wake(s["wake"]["mac"], s["wake"].get("broadcast", "255.255.255.255"))
            except (OSError, ValueError, KeyError):
                pass  # a failed Wake-on-LAN must never prevent an answer
            st["woke"] = now
        return st["up"]

    def candidates(self, sensitive: bool = False):
        """Servers to try, in order (generator: a sleeping machine is only probed if the servers before it failed)."""
        pool = self.servers
        if sensitive and any(s.get("private") for s in pool):
            pool = [s for s in pool if s.get("private")]
        for s in pool:
            if s is not pool[-1]:
                if self.down.get(s["name"], float("-inf")) > self._clock():
                    continue
                if s.get("wake") and not self._is_up(s):
                    continue
            yield self._candidate(s)

    def fail(self, name: str) -> None:
        """Server `name` just failed: avoid it for COOLDOWN s (a machine that sleeps is probed again right away)."""
        self.down[name] = self._clock() + COOLDOWN
        if name in self.awake:
            self.awake[name].update(up=False, checked=self._clock())

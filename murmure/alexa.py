"""Alexa custom skill endpoint: talk to Murmure through an Echo ("Alexa, ouvre Murmure", then ask your questions).

Alexa transcribes your voice at Amazon and sends the text here; Murmure answers with its tools, search and LLM, and Alexa
reads the answer in its own voice. Audio therefore goes through Amazon, unlike the web page.

It is off unless both MURMURE_ALEXA_SKILL_ID (the skill's application id) and MURMURE_ALEXA_TOKEN (a secret in the URL
path) are set. A separate listener serves only `POST /alexa/<token>` (default 127.0.0.1:8765): publish that port, never
the whole Murmure server. Each request is checked the way Amazon requires: certificate URL, certificate chain,
RSA-SHA256 signature of the body, timestamp within 150 s, and the skill id. Needs `pip install "murmure[alexa]"`.

The skill stays in the "development" stage of your Amazon developer account: it works on your own devices only and is
never public. `python -m murmure.alexa model > model.json` prints the French interaction model to paste in the console.

Alexa gives only the free part of a sentence (an AMAZON.SearchQuery slot needs a carrier phrase), so each carrier phrase
("qui", "quelle", "combien", "demande"...) is its own intent and the question is rebuilt as `<prefix> <query>`.
"""
import base64, hmac, json, os, posixpath, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .backend import is_sensitive

SKILL_ID = os.environ.get("MURMURE_ALEXA_SKILL_ID", "")
TOKEN = os.environ.get("MURMURE_ALEXA_TOKEN", "")
BIND = os.environ.get("MURMURE_ALEXA_BIND", "127.0.0.1")
PORT = int(os.environ.get("MURMURE_ALEXA_PORT", "8765"))
MAX_AGE = 150     # s: older requests are replays
BUDGET = 6.5      # s: Alexa gives up on a skill after about 8 s
MAX_BODY = 64 * 1024
LISTENING = "Je t'écoute."
SLOW = "Je n'ai pas eu la réponse à temps. Réessaie."
PRIVATE = "Cette question est sensible : pose-la plutôt sur la page de Murmure, pas à Alexa."
LOST = "Je n'ai pas compris. Dis par exemple : demande, suivi de ta question."

# intent -> (text put before the free part, carrier phrases that start the utterance)
CARRIERS = {
    "AskIntent": ("", ["demande", "dis", "dis-moi", "dis moi", "je voudrais savoir", "j'aimerais savoir", "peux-tu me dire", "peux tu me dire"]),
    "SearchIntent": ("cherche", ["cherche", "recherche", "cherche-moi", "cherche moi"]),
    "WhoIntent": ("qui", ["qui"]),
    "WhichIntent": ("quel", ["quel"]),
    "WhichFemIntent": ("quelle", ["quelle"]),
    "WhichPluralIntent": ("quels", ["quels"]),
    "WhichFemPluralIntent": ("quelles", ["quelles"]),
    "WhenIntent": ("quand", ["quand"]),
    "WhereIntent": ("où", ["où"]),
    "HowIntent": ("comment", ["comment"]),
    "WhyIntent": ("pourquoi", ["pourquoi"]),
    "HowManyIntent": ("combien", ["combien"]),
    "IsItIntent": ("est-ce que", ["est-ce que", "est ce que"]),
    "WhatIsIntent": ("c'est quoi", ["c'est quoi"]),
    "WhatIsThatIntent": ("qu'est-ce que", ["qu'est-ce que", "qu'est ce que"]),
    "TellIntent": ("raconte-moi", ["raconte-moi", "raconte moi", "raconte"]),
    "SetIntent": ("mets", ["mets", "mets-moi", "mets moi"]),
    "RemindIntent": ("rappelle-moi", ["rappelle-moi", "rappelle moi"]),
    "WakeIntent": ("réveille-moi", ["réveille-moi", "réveille moi"]),
    "TurnOnIntent": ("allume", ["allume"]),
    "TurnOffIntent": ("éteins", ["éteins", "éteint"]),
}
ENDING = {"AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"}


class AlexaError(Exception):
    """The request is not a genuine, fresh Alexa request for this skill."""


# ---------------------------------------------------------------- what to answer
def speech(text: str, end: bool = False) -> dict:
    out = {"version": "1.0", "response": {"outputSpeech": {"type": "PlainText", "text": text}, "shouldEndSession": end}}
    if not end:
        out["response"]["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": LISTENING}}
    return out


def question_of(intent: dict) -> str:
    prefix = CARRIERS[intent["name"]][0]
    query = ((intent.get("slots") or {}).get("query") or {}).get("value") or ""
    return f"{prefix} {query}".strip()


def answer_in_time(answer, question: str, budget: float = BUDGET) -> str:
    """Runs `answer(question)` in a thread: Alexa will not wait for a slow model."""
    box = []
    worker = threading.Thread(target=lambda: box.append(answer(question)), daemon=True)
    worker.start()
    worker.join(budget)
    return box[0] if box and box[0] else SLOW


def respond_to(payload: dict, answer, capabilities=lambda: "") -> dict:
    """The skill's reply to one request body (already verified)."""
    request = payload.get("request") or {}
    kind = request.get("type")
    if kind == "LaunchRequest":
        return speech(LISTENING)
    if kind == "SessionEndedRequest":
        return {"version": "1.0", "response": {}}
    intent = request.get("intent") or {}
    name = intent.get("name", "")
    if name in ENDING:
        return speech("À bientôt.", end=True)
    if name == "AMAZON.HelpIntent":
        return speech((capabilities() + " Dis par exemple : demande quel temps fait-il demain.").strip())
    if name in CARRIERS:
        question = question_of(intent)
        if is_sensitive(question):  # never sent to a cloud model; the private model is too slow for Alexa anyway
            return speech(PRIVATE)
        return speech(answer_in_time(answer, question))
    return speech(LOST)


# ---------------------------------------------------------------- is it really Alexa?
def check_cert_url(url: str) -> None:
    u = urllib.parse.urlparse(url)
    ok = (u.scheme.lower() == "https" and (u.hostname or "").lower() == "s3.amazonaws.com"
          and posixpath.normpath(u.path).startswith("/echo.api/") and u.port in (None, 443))
    if not ok:
        raise AlexaError("certificate URL")


_chains: dict[str, tuple] = {}


def fetch_chain(url: str):
    """(leaf certificate, intermediates) from Amazon's PEM chain; cached, since Amazon reuses the same URL."""
    from cryptography import x509
    if url not in _chains:
        with urllib.request.urlopen(url, timeout=5) as r:
            pem = r.read(MAX_BODY)
        certs = x509.load_pem_x509_certificates(pem)
        _chains[url] = (certs[0], certs[1:])
    return _chains[url]


def trusted_roots():
    from cryptography import x509
    try:
        import certifi
        path = certifi.where()
    except ImportError:
        import ssl
        path = ssl.get_default_verify_paths().cafile
    with open(path, "rb") as f:
        return x509.load_pem_x509_certificates(f.read())


def verify_request(headers, body: bytes, now: float | None = None, skill_id: str | None = None, fetch=fetch_chain, roots=None) -> dict:
    """Checks everything Amazon asks a skill endpoint to check; returns the parsed body or raises AlexaError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.x509.verification import DNSName, PolicyBuilder, Store, VerificationError
    now = time.time() if now is None else now
    skill_id = SKILL_ID if skill_id is None else skill_id
    url, sig = headers.get("SignatureCertChainUrl"), headers.get("Signature-256")
    if not (url and sig and skill_id):
        raise AlexaError("missing header or skill id")
    check_cert_url(url)
    try:
        leaf, inter = fetch(url)
        when = datetime.fromtimestamp(now, timezone.utc).replace(tzinfo=None)
        PolicyBuilder().store(Store(trusted_roots() if roots is None else roots)).time(when).build_server_verifier(
            DNSName("echo-api.amazon.com")).verify(leaf, inter)
        leaf.public_key().verify(base64.b64decode(sig), body, padding.PKCS1v15(), hashes.SHA256())
        payload = json.loads(body)
    except (OSError, ValueError, InvalidSignature, VerificationError) as e:
        raise AlexaError(type(e).__name__) from e
    request = payload.get("request") or {}
    try:
        age = abs(now - datetime.fromisoformat(request["timestamp"]).timestamp())
    except (KeyError, ValueError, TypeError) as e:
        raise AlexaError("timestamp") from e
    if age > MAX_AGE:
        raise AlexaError("stale request")
    app = ((payload.get("session") or {}).get("application") or {}).get("applicationId") \
        or (((payload.get("context") or {}).get("System") or {}).get("application") or {}).get("applicationId")
    if app != skill_id:
        raise AlexaError("another skill")
    return payload


# ---------------------------------------------------------------- the listener
def enabled() -> bool:
    return bool(SKILL_ID and TOKEN)


def make_handler(answer, capabilities=lambda: ""):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj=None):
            data = json.dumps(obj or {}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if not hmac.compare_digest(self.path.encode(), f"/alexa/{TOKEN}".encode()):
                return self._send(404)
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send(400)
            if not 0 < n <= MAX_BODY:
                return self._send(400)
            body = self.rfile.read(n)
            try:
                payload = verify_request(self.headers, body)
            except AlexaError as e:
                print(f"alexa: request refused ({e})", flush=True)  # the reason only, never the content
                return self._send(400)
            self._send(200, respond_to(payload, answer, capabilities))

        def do_GET(self):
            self._send(404)

        def log_message(self, fmt, *args):
            pass  # no log: it would hold the questions

    return Handler


def serve(answer, capabilities=lambda: "") -> None:
    """Starts the Alexa listener in a thread."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        raise SystemExit('the Alexa skill needs: pip install "murmure[alexa]"')
    httpd = ThreadingHTTPServer((BIND, PORT), make_handler(answer, capabilities))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


# ---------------------------------------------------------------- interaction model
def interaction_model() -> dict:
    """The French interaction model to paste in the Alexa developer console (JSON editor)."""
    intents = [{"name": name, "slots": [{"name": "query", "type": "AMAZON.SearchQuery"}], "samples": [f"{c} {{query}}" for c in carriers]}
               for name, (_, carriers) in CARRIERS.items()]
    intents += [{"name": n, "samples": []} for n in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.HelpIntent", "AMAZON.NavigateHomeIntent", "AMAZON.FallbackIntent")]
    return {"interactionModel": {"languageModel": {"invocationName": "murmure", "intents": intents}}}


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["model"]:
        print(json.dumps(interaction_model(), ensure_ascii=False, indent=2))
    else:
        raise SystemExit("usage: python -m murmure.alexa model")

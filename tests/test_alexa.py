"""Alexa skill: replies, rebuilt questions, and request verification with a throw-away certificate chain.
`python -m tests.test_alexa` (the verification part needs `cryptography`; it is skipped without it)."""
import base64, json, time
from murmure import alexa as A

fails = 0


def check(label, got, exp):
    global fails
    if got != exp:
        fails += 1
        print(f"KO {label}\n    got     : {got!r}\n    expected: {exp!r}")


def intent(name, query=None, kind="IntentRequest"):
    slots = {"query": {"name": "query", "value": query}} if query is not None else {}
    return {"request": {"type": kind, "intent": {"name": name, "slots": slots}}}


text = lambda r: r["response"]["outputSpeech"]["text"]
asked = []
answer = lambda q: asked.append(q) or "Il est huit heures."

# replies
r = A.respond_to({"request": {"type": "LaunchRequest"}}, answer)
check("launch keeps the session open", (text(r), r["response"]["shouldEndSession"]), (A.LISTENING, False))
check("reprompt present", r["response"]["reprompt"]["outputSpeech"]["text"], A.LISTENING)
check("session ended", A.respond_to({"request": {"type": "SessionEndedRequest"}}, answer), {"version": "1.0", "response": {}})
check("stop ends the session", A.respond_to(intent("AMAZON.StopIntent"), answer)["response"]["shouldEndSession"], True)
check("question rebuilt from carrier and query", (text(A.respond_to(intent("WhichFemIntent", "heure est-il"), answer)), asked[-1]), ("Il est huit heures.", "quelle heure est-il"))
A.respond_to(intent("AskIntent", "quel temps fait-il demain"), answer)
check("ask has no prefix", asked[-1], "quel temps fait-il demain")
A.respond_to(intent("HowManyIntent", "font dix fois douze"), answer)
check("combien prefix", asked[-1], "combien font dix fois douze")
n = len(asked)
check("sensitive question stays home", (text(A.respond_to(intent("AskIntent", "quel médicament prendre"), answer)), len(asked)), (A.PRIVATE, n))
check("unknown intent", text(A.respond_to(intent("SomethingElse"), answer)), A.LOST)
check("fallback intent", text(A.respond_to(intent("AMAZON.FallbackIntent"), answer)), A.LOST)
check("help lists capabilities", text(A.respond_to(intent("AMAZON.HelpIntent"), answer, lambda: "Je peux tout.")).startswith("Je peux tout."), True)
check("slow model -> short apology", A.answer_in_time(lambda q: time.sleep(1) or "tard", "q", budget=0.05), A.SLOW)
check("empty answer -> apology", A.answer_in_time(lambda q: "", "q"), A.SLOW)

# interaction model
model = A.interaction_model()["interactionModel"]["languageModel"]
names = [i["name"] for i in model["intents"]]
check("every carrier intent is in the model", all(c in names for c in A.CARRIERS), True)
check("every sample has a carrier phrase and the slot", all(s.endswith(" {query}") and len(s) > 9 for i in model["intents"] for s in i["samples"]), True)
check("no two intents share a sample", len({s for i in model["intents"] for s in i["samples"]}), sum(len(i["samples"]) for i in model["intents"]))

# certificate URL rules
for url, ok in [("https://s3.amazonaws.com/echo.api/echo-api-cert.pem", True), ("https://s3.amazonaws.com:443/echo.api/x.pem", True),
                ("HTTPS://S3.AMAZONAWS.COM/echo.api/x.pem", True), ("http://s3.amazonaws.com/echo.api/x.pem", False),
                ("https://s3.amazonaws.com.evil.com/echo.api/x.pem", False), ("https://s3.amazonaws.com/EcHo.api/x.pem", False),
                ("https://s3.amazonaws.com:8443/echo.api/x.pem", False), ("https://s3.amazonaws.com/echo.api/../evil/x.pem", False)]:
    try:
        A.check_cert_url(url); got = True
    except A.AlexaError:
        got = False
    check(f"cert URL {url}", got, ok)

# request verification
try:
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    print("skipped: request verification (cryptography is not installed)")
else:
    def make(subject, issuer_name, key, issuer_key, san=None, start=-1, end=1, ca=False):
        b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
             .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)])).public_key(key.public_key())
             .serial_number(x509.random_serial_number())
             .not_valid_before(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=start)).not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=end))
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False))  # the strict verifier wants both
        if ca:
            b = b.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        else:
            b = b.add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        if san:
            b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        return b.sign(issuer_key, hashes.SHA256())

    ca_key, leaf_key, other_key = (rsa.generate_private_key(65537, 2048) for _ in range(3))
    ca = make("Test CA", "Test CA", ca_key, ca_key, ca=True)
    good = make("echo-api.amazon.com", "Test CA", leaf_key, ca_key, san="echo-api.amazon.com")
    wrong_name = make("evil.example", "Test CA", leaf_key, ca_key, san="evil.example")
    expired = make("echo-api.amazon.com", "Test CA", leaf_key, ca_key, san="echo-api.amazon.com", start=-30, end=-10)
    now = time.time()

    def request(ts=None, app="amzn1.ask.skill.test"):
        stamp = dt.datetime.fromtimestamp(ts if ts is not None else now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return json.dumps({"session": {"application": {"applicationId": app}}, "request": {"type": "LaunchRequest", "timestamp": stamp}}).encode()

    def sign(body, key=leaf_key):
        return base64.b64encode(key.sign(body, padding.PKCS1v15(), hashes.SHA256())).decode()

    def verdict(body, cert=good, sig=None, url="https://s3.amazonaws.com/echo.api/c.pem", roots=None, **kw):
        headers = {"SignatureCertChainUrl": url, "Signature-256": sig if sig is not None else sign(body)}
        try:
            A.verify_request(headers, body, now=kw.pop("at", now), skill_id="amzn1.ask.skill.test", fetch=lambda u: (cert, []), roots=roots or [ca])
            return "ok"
        except A.AlexaError as e:
            return str(e)

    body = request()
    check("genuine request accepted", verdict(body), "ok")
    check("tampered body refused", verdict(body + b" ", sig=sign(body)), "InvalidSignature")
    check("signature by another key refused", verdict(body, sig=sign(body, other_key)), "InvalidSignature")
    check("unknown root refused", verdict(body, roots=[make("Other CA", "Other CA", other_key, other_key, ca=True)]), "VerificationError")
    check("certificate for another name refused", verdict(body, cert=wrong_name), "VerificationError")
    check("expired certificate refused", verdict(body, cert=expired), "VerificationError")
    check("stale request refused", verdict(request(ts=now - 400)), "stale request")
    check("request from the future refused", verdict(request(ts=now + 400)), "stale request")
    check("another skill refused", verdict(request(app="amzn1.ask.skill.other")), "another skill")
    check("bad certificate URL refused", verdict(body, url="https://evil.example/echo.api/c.pem"), "certificate URL")
    check("missing signature refused", verdict(body, sig=""), "missing header or skill id")
    check("garbage body refused", verdict(b"not json", sig=sign(b"not json")), "JSONDecodeError")

print("tous les tests OK" if not fails else f"{fails} failure(s)")
raise SystemExit(1 if fails else 0)

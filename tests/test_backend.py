"""Tests of the LLM server chain (cloud API / GPU machine that sleeps / local model): `python -m tests.test_backend`, no network."""
import os, tempfile
from murmure import backend as B

fails = 0


def check(label, got, exp):
    global fails
    if got != exp:
        fails += 1
        print(f"KO {label}\n    got     : {got!r}\n    expected: {exp!r}")


class Clock:
    t = 0.0
    def __call__(self): return self.t


# Wake-on-LAN magic packet: 6 x 0xFF then the MAC 16 times
p = B.magic_packet("AA:BB:CC:DD:EE:FF")
check("packet length", len(p), 102)
check("header", p[:6], b"\xff" * 6)
check("MAC repeated 16 times", p[6:] == bytes.fromhex("AABBCCDDEEFF") * 16, True)
check("MAC with dashes", B.magic_packet("aa-bb-cc-dd-ee-ff"), p)
try:
    B.magic_packet("AA:BB:CC")
    check("invalid MAC refused", "accepted", "ValueError")
except ValueError:
    pass

CLOUD1 = {"name": "cloud1", "url": "https://api.example/", "key": "k", "extra": {"model": "m1"}}
CLOUD2 = {"name": "cloud2", "url": "https://api2.example", "key": "k2", "extra": {"model": "m2"}, "timeout": 5}
GPU = {"name": "gpu", "url": "http://gpu:8091", "key": "gk", "private": True, "wake": {"mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255"}}
LOCAL = {"name": "local", "url": "http://localhost:8080", "private": True}
names = lambda b, **kw: [c.name for c in b.candidates(**kw)]
nowake = lambda *a: None

# no servers file: a single private local server
b = B.Backend(servers=B.load_servers("/nonexistent/servers.json"))
check("default: one local server", names(b), ["local"])

# order is priority; the last server is the fallback
clk = Clock()
b = B.Backend(servers=[CLOUD1, CLOUD2, LOCAL], clock=clk, wake=nowake)
check("order", names(b), ["cloud1", "cloud2", "local"])
c = next(b.candidates())
check("URL, key header and extra", (c.url, c.headers, c.extra, c.timeout), ("https://api.example", {"Authorization": "Bearer k"}, {"model": "m1"}, 10))
check("per-server timeout", [c.timeout for c in b.candidates()], [10, 5, 180])
check("fallback is the last server", b.fallback().name, "local")
check("sensitive -> private servers only", names(b, sensitive=True), ["local"])

# failures: skipped for COOLDOWN seconds, the last server is never skipped
b.fail("cloud1")
check("failed server set aside", names(b), ["cloud2", "local"])
clk.t = B.COOLDOWN + 1
check("failed server back after the delay", names(b), ["cloud1", "cloud2", "local"])
b.fail("cloud1"); b.fail("cloud2"); b.fail("local")
check("last server always tried", names(b), ["local"])

# no private server: nothing is filtered
b = B.Backend(servers=[CLOUD1, CLOUD2], clock=Clock())
check("no private server -> no filtering", names(b, sensitive=True), ["cloud1", "cloud2"])

# key handling: key_file / key_env missing -> server skipped; present -> used
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "k.txt")
    open(path, "w").write("from-file\n")
    s = {**CLOUD1, "key": None, "key_file": os.path.relpath(path, B.HOME)}
    b = B.Backend(servers=[s, LOCAL])
    check("key_file read", next(b.candidates()).headers, {"Authorization": "Bearer from-file"})
b = B.Backend(servers=[{**CLOUD1, "key": None, "key_file": "does-not-exist.key"}, LOCAL])
check("missing key file -> server skipped", names(b), ["local"])
os.environ["TEST_MURMURE_KEY"] = "from-env"
b = B.Backend(servers=[{**CLOUD1, "key": None, "key_env": "TEST_MURMURE_KEY"}, LOCAL])
check("key_env read", next(b.candidates()).headers, {"Authorization": "Bearer from-env"})
b = B.Backend(servers=[{**CLOUD1, "key": None, "key_env": "TEST_MURMURE_UNSET"}, LOCAL])
check("missing key_env -> server skipped", names(b), ["local"])
b = B.Backend(servers=[LOCAL])
check("no key needed", next(b.candidates()).headers, {})
try:
    B.Backend(servers=[{**CLOUD1, "key": None, "key_env": "TEST_MURMURE_UNSET"}])
    check("no usable server", "accepted", "SystemExit")
except SystemExit:
    pass

# machine that sleeps: awake -> used, with its key
clk, wakes = Clock(), []
b = B.Backend(servers=[GPU, LOCAL], probe=lambda url, h: True, wake=lambda *a: wakes.append(a), clock=clk)
check("machine up -> used first", names(b), ["gpu", "local"])
check("probe sends the key", next(b.candidates()).headers, {"Authorization": "Bearer gk"})
check("no Wake-on-LAN when up", wakes, [])

# asleep: the local server answers, a single wake-up within the grace period, then another one
state = {"up": False}
wakes.clear()
b = B.Backend(servers=[GPU, LOCAL], probe=lambda url, h: state["up"], wake=lambda *a: wakes.append(a), clock=clk)
check("asleep -> next server", names(b), ["local"])
check("wake-up sent", wakes, [("AA:BB:CC:DD:EE:FF", "192.168.1.255")])
clk.t = 30
check("still asleep", names(b), ["local"])
check("no second wake-up within 90 s", len(wakes), 1)
clk.t = 100
names(b)
check("new wake-up after 90 s", len(wakes), 2)

# it wakes up: used at the next probe
state["up"] = True
clk.t = 106
check("woken -> machine used", names(b), ["gpu", "local"])

# it drops during a request: back to local right away
b.fail("gpu")
state["up"] = False
check("after fail -> local", names(b), ["local"])

# a failing wake-up does not break the answer
def boom(*a): raise OSError("network")
b = B.Backend(servers=[GPU, LOCAL], probe=lambda *a: False, wake=boom, clock=Clock())
check("wake-up failure -> local anyway", names(b), ["local"])

for q, exp in [("J'ai mal, quel médicament prendre ?", True), ("Quel est mon solde bancaire ?", True), ("Donne-moi mon mot de passe wifi", True),
               ("Quelle rivière traverse Reims ?", False), ("Raconte une blague", False), ("Qui a gagné la Coupe du monde 2022 ?", False)]:
    check("sensitive: " + q, B.is_sensitive(q), exp)

print("tous les tests OK" if not fails else f"{fails} failure(s)")
raise SystemExit(1 if fails else 0)

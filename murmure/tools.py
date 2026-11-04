"""Murmure instant tools: time, date, weather, calculation, small talk, timers and reminders, plus optional plugins.

They answer without an LLM (so no invention, in under 2 s). `answer(question)` returns the text to say, or None if the
question is not for a tool. The pure parts (recognition, durations, formatting) can be tested off the NAS;
side effects (network, notifications) are isolated in `fetch_json` and `schedule`.

Plugins: every `*.py` file in `plugins/` (or the directory named by `MURMURE_PLUGINS`) that defines
`answer(question: str, now: float) -> str | None` is asked after the built-in tools. It may also define `ABOUT`, a phrase
completing « Je peux … » in the list of capabilities (empty = not listed). See plugins/backup_status.py.
"""
import datetime as dt
import importlib.util, json, operator, os, re, shlex, shutil, subprocess, threading, time, urllib.parse, urllib.request

from . import HOME as MURMURE_HOME
from .rules import GREETING

# weather location: MURMURE_CITY, MURMURE_LAT, MURMURE_LON (Paris by default)
HOME = {"ville": os.environ.get("MURMURE_CITY", "Paris"), "lat": float(os.environ.get("MURMURE_LAT", "48.8566")), "lon": float(os.environ.get("MURMURE_LON", "2.3522"))}
# timezone for the forecast ("auto" = the one of the weather location)
TIMEZONE = os.environ.get("MURMURE_TZ", "auto")
# command run when a timer or reminder expires, called as `<command> "<title>" "<message>"` (ntfy, Telegram, a speaker...);
# without it, timers and reminders are disabled. See contrib/notify-ntfy.sh.
NOTIFY_CMD = shlex.split(os.environ.get("MURMURE_NOTIFY_CMD", ""))
PLUGINS_DIR = os.environ.get("MURMURE_PLUGINS", os.path.join(MURMURE_HOME, "plugins"))
MAX_DELAY_S = 7 * 24 * 3600

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
WMO = {0: "le ciel est dégagé", 1: "le ciel est plutôt dégagé", 2: "le ciel est partiellement nuageux", 3: "le ciel est couvert",
       45: "il y a du brouillard", 48: "il y a du brouillard givrant", 51: "il y a une légère bruine", 53: "il y a de la bruine",
       55: "il y a une forte bruine", 56: "il y a de la bruine verglaçante", 57: "il y a de la bruine verglaçante",
       61: "il y a de la pluie faible", 63: "il pleut", 65: "il y a de fortes pluies", 66: "il y a de la pluie verglaçante",
       67: "il y a de la pluie verglaçante", 71: "il neige faiblement", 73: "il neige", 75: "il y a de fortes chutes de neige",
       77: "il y a des grains de neige", 80: "il y a de légères averses", 81: "il y a des averses", 82: "il y a de violentes averses",
       85: "il y a des averses de neige", 86: "il y a de fortes averses de neige", 95: "il y a de l'orage", 96: "il y a de l'orage avec de la grêle",
       99: "il y a de l'orage avec de la grêle"}
# sky for a whole day (daily): « le temps sera … »
WMO_JOUR = {0: "dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert", 45: "brumeux", 48: "brumeux avec du givre",
            51: "gris avec de la bruine", 53: "gris avec de la bruine", 55: "gris avec de la bruine", 56: "gris avec de la bruine verglaçante",
            57: "gris avec de la bruine verglaçante", 61: "pluvieux, avec de faibles pluies", 63: "pluvieux", 65: "très pluvieux",
            66: "pluvieux avec du verglas", 67: "pluvieux avec du verglas", 71: "neigeux, avec de faibles chutes", 73: "neigeux",
            75: "très neigeux", 77: "neigeux", 80: "marqué par de légères averses", 81: "marqué par des averses",
            82: "marqué par de violentes averses", 85: "marqué par des averses de neige", 86: "marqué par de fortes averses de neige",
            95: "orageux", 96: "orageux avec de la grêle", 99: "orageux avec de la grêle"}

# ----------------------------------------------------------------------------------------------- recognition
TIME_Q = re.compile(r"\b(quelle heure|l'heure|il est quelle heure|heure (est-il|qu'il est))\b", re.I)
DATE_Q = re.compile(r"\b(quel jour (sommes|est|on)|on est quel jour|quelle est la date|la date d'aujourd'hui|c'est quel jour|quel jour de la semaine|quelle date)\b", re.I)
WEATHER_Q = re.compile(r"\b(météo|quel temps|temps (fait|fera)|il (fait|fera) (chaud|froid|beau|quel)|pleuvoir|va[- ]t[- ]il (pleuvoir|neiger)|"
                       r"il pleut|neiger|quelle température|température (dehors|extérieure|demain|aujourd'hui|ce soir)|parapluie)\b", re.I)
TIMER_Q = re.compile(r"\b(minuteur|chronomètre|rappelle[- ]moi|rappel|préviens[- ]moi|prévenir|réveille[- ]moi|réveil|alarme)\b", re.I)
DAY_WORDS = {"aujourd'hui": 0, "ce soir": 0, "cette nuit": 0, "demain": 1, "après-demain": 2, "après demain": 2}
FOLLOW_DAY = re.compile(r"^\W*(et|puis)\W+(aujourd'hui|ce soir|demain|après[- ]demain|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|ce week-end|le week-end)\b", re.I)

_UNITS = {"zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9,
          "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "trente": 30,
          "quarante": 40, "cinquante": 50, "soixante": 60}
_state = {"intent": None, "ville": None}  # for « et demain ? » after a weather question


def french_number(text: str) -> int | None:
    """« 10 » -> 10 ; « vingt-cinq » -> 25 ; « trente et une » -> 31 ; « soixante-dix » -> 70 ; « quatre-vingt-dix » -> 90."""
    t = text.strip().lower().replace("-", " ")
    if t.isdigit():
        return int(t)
    words = [w for w in t.split() if w != "et"]
    total, i = 0, 0
    while i < len(words):
        w = words[i]
        if w == "quatre" and i + 1 < len(words) and words[i + 1] in ("vingt", "vingts"):
            total, i = total + 80, i + 2
            continue
        if w in _UNITS:
            total += _UNITS[w]
        elif w in ("cent", "cents"):
            total += 100
        else:
            return None
        i += 1
    return total if words else None


_NUMWORD = "|".join(sorted(_UNITS, key=len, reverse=True))
_NUM = rf"(?:\d+|(?:{_NUMWORD})(?:[- ](?:et[- ])?(?:{_NUMWORD}))*)"
DURATION = re.compile(rf"\b(?:dans\s+|de\s+|pour\s+)?(?P<n>{_NUM})\s*(?P<u>secondes?|sec|minutes?|min|heures?|h)\b(?:\s+(?:et\s+)?(?P<n2>{_NUM})(?:\s*(?:minutes?|min))?\b)?", re.I)
QUARTERS = {"un quart d'heure": 15, "une demi-heure": 30, "une demi heure": 30, "trois quarts d'heure": 45, "une heure et demie": 90, "une heure et quart": 75}
QUARTER = re.compile("|".join(re.escape(k) for k in QUARTERS), re.I)
CLOCK = re.compile(r"\bà\s+(?P<h>\d{1,2})\s*(?:h|heures?|:)\s*(?P<m>\d{2})?\b", re.I)


def parse_delay(text: str, now: float | None = None) -> tuple[int, str, str] | None:
    """(seconds, spoken wording, « duree » or « heure ») from « dans 10 minutes », « 1 heure 30 », « à 18h30 »."""
    now = time.time() if now is None else now
    q = QUARTER.search(text)
    if q:
        secs = QUARTERS[re.sub(r"\s+", " ", q.group(0).lower())] * 60
        return secs, say_duration(secs), "duree"
    c = CLOCK.search(text)
    if c:
        h, m = int(c["h"]), int(c["m"] or 0)
        if h > 23 or m > 59:
            return None
        lt = time.localtime(now)
        target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
        if target <= now:
            target += 24 * 3600
        return int(target - now), f"{h} heures" + (f" {m:02d}" if m else ""), "heure"
    d = DURATION.search(text)
    if d:
        n = french_number(d["n"])
        if n is None:
            return None
        u = d["u"].lower()
        secs = n * (3600 if u.startswith(("heure", "h")) else 60 if u.startswith("min") else 1)
        if d["n2"] and u.startswith(("heure", "h")):
            secs += (french_number(d["n2"]) or 0) * 60
        return (secs, say_duration(secs), "duree") if 0 < secs <= MAX_DELAY_S else None
    return None


def say_duration(secs: int) -> str:
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    parts = []
    if h: parts.append(f"{h} heure{'s' if h > 1 else ''}")
    if m: parts.append(f"{m} minute{'s' if m > 1 else ''}")
    if s and not h: parts.append(f"{s} seconde{'s' if s > 1 else ''}")
    return " ".join(parts) or "0 seconde"


# ----------------------------------------------------------------------------------------------- formatting
def say_time(lt: time.struct_time) -> str:
    h, m = lt.tm_hour, lt.tm_min
    return f"Il est {h} heure{'s' if h > 1 else ''}" + (f" {m}" if m else "") + "."


def say_date(lt: time.struct_time) -> str:
    jour = "1er" if lt.tm_mday == 1 else str(lt.tm_mday)
    return f"Nous sommes le {JOURS[lt.tm_wday]} {jour} {MOIS[lt.tm_mon - 1]} {lt.tm_year}."


def _deg(x: float) -> str:
    n = round(x)
    return f"moins {abs(n)}" if n < 0 else str(n)


def say_weather(data: dict, day: int, ville: str, lt: time.struct_time) -> str:
    """Spoken forecast for `day` (0 = today) from an Open-Meteo response (current + daily)."""
    d = data["daily"]
    label = {0: "aujourd'hui", 1: "demain", 2: "après-demain"}.get(day) or JOURS[(lt.tm_wday + day) % 7]
    tmax, tmin = d["temperature_2m_max"][day], d["temperature_2m_min"][day]
    pluie, mm = d["precipitation_probability_max"][day], d["precipitation_sum"][day]
    vent = d["wind_speed_10m_max"][day]
    ciel = WMO_JOUR.get(d["weather_code"][day], "variable")
    if day == 0 and "current" in data:
        c = data["current"]
        txt = f"À {ville}, il fait {_deg(c['temperature_2m'])} degrés en ce moment, et {WMO.get(c['weather_code'], 'le ciel est variable')}. "
        txt += f"Aujourd'hui, de {_deg(tmin)} à {_deg(tmax)} degrés."
    else:
        txt = f"{label.capitalize()} à {ville}, le temps sera {ciel}, de {_deg(tmin)} à {_deg(tmax)} degrés."
    if pluie >= 40 or mm >= 1:
        txt += f" Risque de pluie {round(pluie / 10) * 10} pour cent" + (f", environ {round(mm)} millimètres." if mm >= 1 else ".")
    elif pluie >= 20:
        txt += f" Faible risque de pluie, {round(pluie / 10) * 10} pour cent."
    else:
        txt += " Pas de pluie prévue."
    if vent >= 40:
        txt += f" Vent fort, jusqu'à {round(vent)} kilomètres heure."
    elif vent >= 25:
        txt += f" Un peu de vent, jusqu'à {round(vent)} kilomètres heure."
    return txt


# ----------------------------------------------------------------------------------------------- side effects
def fetch_json(url: str, timeout: int = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def weather_url(lat: float, lon: float) -> str:
    return "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": TIMEZONE, "forecast_days": 7,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max"})


def geocode(ville: str) -> dict | None:
    r = fetch_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": ville, "count": 1, "language": "fr", "format": "json"})).get("results")
    return {"ville": r[0]["name"], "lat": r[0]["latitude"], "lon": r[0]["longitude"]} if r else None


def schedule(delay_s: int, message: str) -> bool:
    """Runs NOTIFY_CMD in `delay_s` seconds. With systemd the timer survives a Murmure restart; otherwise it is in memory."""
    msg = re.sub(r"[\x00-\x1f]", " ", message)[:200]
    cmd = [*NOTIFY_CMD, "Murmure", msg]
    if shutil.which("systemd-run"):
        unit = f"murmure-reminder-{int(time.time())}-{os.getpid() % 1000}"
        r = subprocess.run(["systemd-run", f"--on-active={int(delay_s)}s", f"--unit={unit}", "--collect", *cmd], capture_output=True, timeout=10)
        return r.returncode == 0
    timer = threading.Timer(delay_s, lambda: subprocess.run(cmd, capture_output=True, timeout=30))  # ponytail: lost if Murmure restarts
    timer.daemon = True
    timer.start()
    return True


# ----------------------------------------------------------------------------------------------- exact calculation
_N = r"-?\d+(?:[.,]\d+)?"
MATH = re.compile(rf"(?:combien (?:font|fait|vaut|est)|calcule|c'est combien)\s+(?P<a>{_N})\s*(?P<op>fois|multiplié par|plus|moins|divisé par|[x×*+/-])\s*(?P<b>{_N})", re.I)
OPS = {"fois": (operator.mul, "fois"), "x": (operator.mul, "fois"), "×": (operator.mul, "fois"), "*": (operator.mul, "fois"), "multiplié par": (operator.mul, "multiplié par"),
       "plus": (operator.add, "plus"), "+": (operator.add, "plus"), "moins": (operator.sub, "moins"), "-": (operator.sub, "moins"),
       "divisé par": (operator.truediv, "divisé par"), "/": (operator.truediv, "divisé par")}


def calc_answer(q: str) -> str | None:
    """« Combien font 17 fois 23 ? » -> « 17 fois 23 font 391. »: exact, no model."""
    m = MATH.search(q)
    if not m:
        return None
    a, b = (float(m[x].replace(",", ".")) for x in ("a", "b"))
    fn, mot = OPS[m["op"].lower()]
    if fn is operator.truediv and b == 0:
        return "On ne peut pas diviser par zéro."
    r = fn(a, b)
    fmt = lambda x: str(int(x)) if float(x).is_integer() else f"{x:.4f}".rstrip("0").replace(".", ",")
    return f"{fmt(a)} {mot} {fmt(b)} font {fmt(r)}."


# ----------------------------------------------------------------------------------------------- basic conversation
def load_plugins(directory: str) -> dict:
    plugins = {}
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        if not name.endswith(".py") or name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location("murmure_plugin_" + name[:-3], os.path.join(directory, name))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if callable(getattr(module, "answer", None)):
                plugins[name[:-3]] = module
        except Exception as e:  # a broken plugin must not take the assistant down
            print(f"plugin {name} ignored: {type(e).__name__}: {e}", flush=True)
    return plugins


PLUGINS = load_plugins(PLUGINS_DIR)


def capabilities() -> str:
    items = ["chercher sur Internet", "te donner l'heure, la date et la météo"]
    items += [m.ABOUT for m in PLUGINS.values() if getattr(m, "ABOUT", "")]
    if NOTIFY_CMD:
        items.append("programmer des minuteurs et des rappels")
    return "Je peux " + ", ".join(items[:-1]) + " et " + items[-1] + "."


HOW_ARE_YOU = re.compile(r"ça va|tu vas bien|comment vas-tu|comment ça va", re.I)
WHO_ARE_YOU = re.compile(r"\b(qui es-tu|tu es qui|que sais-tu faire|que peux-tu faire|qu'est-ce que tu sais faire|tu sais faire quoi|à quoi (tu )?sers?|aide-moi)\b", re.I)
ACTION = re.compile(r"\b(allume\w*|étein\w*|baisse\w*|monte\w*|augmente\w*|diminue\w*|ouvre|ferme\w*|lance\w*|joue\w*|mets?|mettre|règle\w*)\b.*"
                    r"\b(lumières?|lampes?|volets?|chauffage|musique|volume|télé|télévision|radio|prises?|porte|thermostat)\b", re.I)


def smalltalk(q: str, lt: time.struct_time) -> str | None:
    """Canned replies: instant, natural, and no room for invention."""
    if ACTION.search(q):
        return "Je ne peux pas encore commander les appareils de la maison. " + capabilities()
    if WHO_ARE_YOU.search(q):
        return "Je suis Murmure, l'assistant de la maison. " + capabilities()
    if GREETING.match(q):
        low = q.lower()
        if HOW_ARE_YOU.search(low):
            return "Ça va très bien, merci ! Et toi ?"
        if re.search(r"tu es là|tu m'entends", low):
            return "Oui, je suis là."
        if "merci" in low:
            return "Avec plaisir !"
        if re.search(r"au revoir|bonne nuit|à plus|à tout", low):
            return "À bientôt !" if lt.tm_hour < 21 else "Bonne nuit !"
        if re.search(r"bonjour|bonsoir|salut|coucou|hello", low):
            return ("Bonjour ! " if lt.tm_hour < 18 else "Bonsoir ! ") + "Que puis-je faire pour toi ?"
        return "D'accord !"
    return None


# ----------------------------------------------------------------------------------------------- entry point
LIGHT = re.compile(r"lumière|lampe|allum|étein", re.I)


def reminder_text(q: str) -> str:
    """Reminder subject: « rappelle-moi dans 20 minutes de sortir le linge » -> « sortir le linge »."""
    rest = DURATION.sub("", CLOCK.sub("", QUARTER.sub("", q)))
    m = re.search(r"(?:\bde\s+|\bd'|\bque je\s+|\bpour\s+)(.{3,120}?)\s*[.?!]?$", rest, re.I)
    t = (m.group(1) if m else "").strip(" ,.")
    return "" if re.fullmatch(r"(un |le |mon |ma )?(minuteur|rappel|réveil|alarme)", t, re.I) else t


def timer_answer(q: str, now: float) -> str:
    if not NOTIFY_CMD:
        return "Les minuteurs et les rappels ne sont pas configurés."
    d = parse_delay(q, now)
    if d is None:
        _state["intent"] = None
        return "Dis-moi dans combien de temps, par exemple « minuteur de dix minutes » ou « rappelle-moi à dix-huit heures »."
    secs, quand, kind = d
    nature = "minuteur" if re.search(r"minuteur|chrono", q, re.I) else "réveil" if re.search(r"réveil|alarme", q, re.I) else "rappel"
    what = reminder_text(q) if nature == "rappel" else ""
    titre = "Minuteur terminé" if nature == "minuteur" else "Réveil" if nature == "réveil" else (f"Rappel : {what}" if what else "Rappel")
    _state["intent"] = None
    if not schedule(secs, titre):
        return "Je n'ai pas réussi à programmer ce rappel."
    cible = f"dans {quand}" if kind == "duree" else f"à {quand}"
    txt = f"C'est noté, {nature} {cible}" + (f" pour {what}" if what else "") + ". Je t'enverrai une notification sur ton téléphone."
    if LIGHT.search(q):
        txt += " Je ne peux pas encore commander les lumières."
    return txt


def weather_answer(q: str, lt: time.struct_time, follow: bool) -> str:
    day = next((DAY_WORDS[k] for k in sorted(DAY_WORDS, key=len, reverse=True) if k in q.lower()), 0)
    for i, j in enumerate(JOURS):
        if re.search(rf"\b{j}\b", q, re.I):
            day = (i - lt.tm_wday) % 7
    if re.search(r"week-end", q, re.I):
        day = (5 - lt.tm_wday) % 7 if lt.tm_wday < 5 else 0
    ville = _state["ville"] if follow and _state["ville"] else HOME
    m = re.search(r"\b(?:à|a|de|pour|sur|en)\s+([A-ZÀ-Ý][\wÀ-ÿ'’-]+(?:[ -][A-ZÀ-Ý][\wÀ-ÿ'’-]+)*)", q)
    if m and m.group(1).lower() != HOME["ville"].lower():
        try:
            ville = geocode(m.group(1)) or ville
        except OSError:
            return f"Je n'arrive pas à trouver {m.group(1)} pour la météo."
    try:
        data = fetch_json(weather_url(ville["lat"], ville["lon"]))
    except OSError:
        return "Je n'arrive pas à joindre le service météo pour le moment."
    _state.update(intent="meteo", ville=ville)
    return say_weather(data, min(day, 6), ville["ville"], lt)


def answer(question: str, now: float | None = None) -> str | None:
    """Text to say if the question belongs to a tool, otherwise None."""
    now = time.time() if now is None else now
    lt, q = time.localtime(now), question.strip()
    follow = bool(FOLLOW_DAY.match(q)) and _state["intent"] == "meteo"
    if TIMER_Q.search(q):
        return timer_answer(q, now)
    if TIME_Q.search(q) and not re.search(r"\bà\s+[A-ZÀ-Ý]", q):
        _state["intent"] = None
        return say_time(lt)
    if DATE_Q.search(q):
        _state["intent"] = None
        return say_date(lt)
    if WEATHER_Q.search(q) or follow:
        return weather_answer(q, lt, follow)
    for plugin in PLUGINS.values():
        text = plugin.answer(q, now)
        if text:
            _state["intent"] = None
            return text
    calc = calc_answer(q)
    if calc:
        _state["intent"] = None
        return calc
    talk = smalltalk(q, lt)
    if talk:
        _state["intent"] = None
    return talk

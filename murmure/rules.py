"""Murmure rules (pure: no heavy dependencies, testable off the NAS).

Search decision, query cleanup, spoken text (pronunciation), sentence splitting.
The server (murmure.py) imports them; `eval_murmure.py` tests them directly.
"""
import json, os, re, time, urllib.parse, urllib.request

from . import DATA, HOME

SEARX_URL = os.environ.get("MURMURE_SEARX_URL", "http://127.0.0.1:8888/search")  # local SearXNG
SYSTEM_FILE = os.path.join(HOME, "system_prompt.txt")  # system prompt, hot-reloadable (falls back to DEFAULT_SYSTEM)

DEFAULT_SYSTEM = """You are the household voice assistant. Your answer will be read aloud by a speech synthesizer.

Language: reply in the language of the user's last message; if unclear, reply in French. Always use the informal register: in French, always "tu" (never "vous", "votre", or -ez imperatives such as "faites", "ajoutez", "prenez": say "fais", "ajoute", "prends").

Style: one to three short sentences. No lists, no markdown, no emoji, no web addresses. Start directly with the answer: no "Here is", "Sure", "Voici", "Bien sûr", and no comment on the request. Never say "the results indicate" or "selon les résultats": state the information itself.

Requests to create (joke, story, idea, recipe, translation, rewrite): do it immediately, without asking questions.

Facts: never invent a place, shop, address, name, date or number. If you do not know, or an information is missing (time, weather, device state), say so plainly.

Actions: you cannot control any device yet (lights, alarm, timer, music). If asked, say you cannot do it yet; never claim you did it.

Search results: only when the message contains "Résultats de recherche", answer in at most two sentences using only what they say about the topic, even if partial. If a precise detail asked for (score, date, name) is not there, say so. End with the French word "selon" followed by the name of one of the listed sites (in another language, "according to" plus the site name). Without search results, never cite a source."""

_sys = {"mtime": None, "text": DEFAULT_SYSTEM}


def system_prompt() -> str:
    """Reloaded whenever `system_prompt.txt` changes: lets you test variants without restarting Murmure."""
    try:
        mtime = os.path.getmtime(SYSTEM_FILE)
    except OSError:
        return DEFAULT_SYSTEM
    if mtime != _sys["mtime"]:
        with open(SYSTEM_FILE, encoding="utf-8") as f:
            _sys.update(mtime=mtime, text=f.read().strip())
    return _sys["text"]


def system_message() -> dict:
    """The system message sent to the LLM: the prompt plus today's date (the model has no clock)."""
    return {"role": "system", "content": f"{system_prompt()} Today is {today()}."}


# Search by default: LFM2 has no reliable memory (« le 1.2 PureTech est réputé pour sa fiabilité »,
# spas invented in Reims). It answers alone only for conversation, creative requests, calculations and commands.
# Rule-based decision (LFM2 decides poorly and costs 2 to 7 s); saying « cherche … » ("search ...") always forces a search.
# Greeting: only if the message contains nothing else (« Ok je veux partir à Mons… » must trigger a search)
_HELLO = (r"bonjour|bonsoir|salut|coucou|hello|merci|beaucoup|ok|okay|d'accord|super|génial|parfait|top|au revoir|"
          r"bonne nuit|à plus|à tout à l'heure|ça va|tu vas bien|comment vas-tu|comment ça va|qui es-tu|tu es là|"
          r"tu m'entends|murmure")
GREETING = re.compile(rf"^\W*(?:(?:{_HELLO})\b\W*)+$", re.I)
# Creative and calculation requests: explicit phrasings only (« l'histoire de Reims » or « dîner à Reims » must search)
CREATIVE = re.compile(
    r"\b(raconte[- ]moi une|blagues?|poèmes?|poésie|chanson|invente|imagine|"
    r"idées? de (repas|dîner|déjeuner|plat|dessert|recette|goûter|petit-déjeuner)|recettes? de|"
    r"que (puis-je|je peux) cuisiner|traduis|traduire|reformule|résume)\b|"
    r"\d+\s*(?:[x×*+/-]|fois|plus|moins|divisé par)\s*\d+", re.I)
# Logic and arithmetic word problems (« Marie a 3 frères… Combien d'enfants ? »): never a web search
_NUM = r"\d+|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|vingt|trente|cent"
PUZZLE = re.compile(rf"(?:\b(?:{_NUM})\b[^?]*[.!]\s+[^?]*\bcombien\b)|(?:^\W*si\b[^?]*\b(?:{_NUM})\b[^?]*\bcombien\b)", re.I)
FACT_COMBIEN = re.compile(r"prix|coûte|coute|coût|habitants|population|âge|distance|kilom|dure|durée|morts|victimes|buts|titres|euros|€|\bkm\b", re.I)
LEAD = re.compile(rf"^\W*(?:(?:{_HELLO}|bon|alors|eh bien)\b[\s,.!]*)+", re.I)  # strips a leading « Ok, » from the query
# Proper noun not at sentence start (« un spa à Reims », « la Seine »): LFM2 knows specific places very poorly
# and invents (nonexistent spas, « la Seine à Reims »); so we search as soon as a proper noun appears.
PROPER_NOUN = re.compile(r"(?<=[\w,;:'’] )([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][\w'’-]+)")
NOT_PROPER = {"Murmure", "Je", "Tu", "Il", "Elle", "On", "Nous", "Vous"}
FOLLOW_UP = re.compile(r"^(et|combien|pourquoi|quand|où|qui|quel\w*|comment|lequel|laquelle)\b", re.I)
FILLER = re.compile(r"^(murmure[ ,]*)?((fais|fait|peux-tu faire|tu peux faire) (des|une) recherches? (sur|à propos de|concernant)|"
                    r"(peux-tu |tu peux )?(re)?cherche[rz]? (sur internet |sur le web )?(des infos |des informations )?(sur|à propos de)?)\s*", re.I)
# Conversational phrasings to strip from the query: « Parle-moi du moteur » used to search the conjugation of « parler »,
# « Donne-moi des détails » used to find Jedonne.fr.
ASK = re.compile(r"^(murmure[ ,]*)?((parle[sz]?|dis|explique|raconte|donne|trouve)[- ]moi|"
                 r"(tu peux|peux[- ]tu|pourrais[- ]tu) me (parler|dire|expliquer|donner|trouver)|"
                 r"je (voudrais|veux|aimerais) (savoir|connaître)|est-ce que|sais[- ]tu|tu sais|c'est quoi)"
                 r"( un( petit)? peu)?( tout)?( des| plus de)?( détails| infos| informations| précisions| l'avis)?"
                 r"( sur| à propos| concernant| de| d'| du| des| si)?( la| le| les| l'| un| une)?\s*", re.I)
POLITE = re.compile(r",?\s*s'il (te|vous) pla[iî]t", re.I)
_DIGIT = {"zéro": "0", "un": "1", "une": "1", "deux": "2", "trois": "3", "quatre": "4", "cinq": "5",
          "six": "6", "sept": "7", "huit": "8", "neuf": "9"}
_W = "|".join(_DIGIT) + r"|\d"
SPOKEN_DECIMAL = re.compile(rf"\b({_W})\s+(?:litres?|point|virgule)\s+({_W})\b", re.I)


def normalize_question(text: str) -> str:
    """« un litre six THP » (dictation transcribed by Parakeet) -> « 1.6 THP », otherwise not findable by search engines."""
    return SPOKEN_DECIMAL.sub(lambda m: f"{_DIGIT.get(m[1].lower(), m[1])}.{_DIGIT.get(m[2].lower(), m[2])}", text)


# Home commands and state: never a web search (otherwise LFM2 "confirms" an invented action
# from the results). « mets » is often transcribed « mais », hence its absence from FOLLOW_UP.
NO_SEARCH = re.compile(r"\b(réveil|minuteur|alarme|allum\w*|étein\w*|lumières?|lampes?|volume|musique|"
                       r"rappelle|rappel|sauvegardes?|nas|chauffage|volets?)\b", re.I)
last_query: str | None = ""  # "": no exchange yet, None: previous exchange had no search



def today() -> str:
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
            "septembre", "octobre", "novembre", "décembre"]
    d = time.localtime()
    return f"{d.tm_mday} {mois[d.tm_mon - 1]} {d.tm_year}"


def decide(question: str) -> str | None:
    """Search query to run, or None (LFM2 answers alone). A follow-up question reuses the previous topic."""
    global last_query
    q = ASK.sub("", FILLER.sub("", POLITE.sub("", LEAD.sub("", question)))).strip(" ?.!,") or question
    proper = any(w not in NOT_PROPER for w in PROPER_NOUN.findall(question))
    if NO_SEARCH.search(question):
        last_query = ""
        return None
    # follow-up only without a proper noun: « Et le prochain match ? » keeps « PSG », « Quelle rivière traverse Reims ? » does not
    if FOLLOW_UP.search(question) and not proper:
        if last_query:
            # previous topic: the whole query if short, otherwise its proper nouns (« Mons Belgique »)
            names = [w for w in PROPER_NOUN.findall(last_query) if w not in NOT_PROPER]
            subject = last_query if len(last_query.split()) <= 8 or not names else " ".join(names)
            return f"{subject} {q}"
        if last_query is None:  # follow-up to an exchange without a search (« Et sans fromage ? » after a recipe)
            return None
    puzzle = PUZZLE.search(question) and not FACT_COMBIEN.search(question)
    chat = GREETING.match(question) or CREATIVE.search(question) or puzzle
    if chat and not FILLER.match(LEAD.sub("", question)):  # « cherche une recette » forces a search
        last_query = None
        return None
    last_query = q
    return q



STOPWORDS = set("les des une aux par pour avec dans sur que qui est sont été cette ces son ses leur plus pas "
                "tout tous mais donc car dont elle ils nous vous très aussi entre voir faire avoir être quoi comment "
                "quel quelle quels quelles peux veux conseilles conseille conseiller moi toi priorité week end".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w{3,}", text.lower()) if w not in STOPWORDS}


# ponytail: 2 results, sentences containing a query word, 180 characters max per result;
# each token read costs ~15 ms on the N97 (half the text = ~3 s saved per search)
def condense(sources: list[dict], query: str) -> str:
    kw, out = _words(query), []
    for i, s in enumerate(sources[:2]):  # 2 sources: same results as 3 (16/17) and 1.5 s less before the first word
        phrases = re.split(r"(?<=[.!?])\s+", s["extrait"])
        keep = " ".join(p for p in phrases if _words(p) & kw) or phrases[0]
        site = urllib.parse.urlparse(s["url"]).netloc.removeprefix("www.")
        out.append(f"[{i + 1}] {s['titre'][:90]} ({site}) : {keep[:180]}")
    return "\n".join(out)


def user_message(question: str, sources: list[dict], query: str) -> str:
    # results placed at the end of the prompt: the beginning (system + history) stays cached
    if not sources:
        return question
    return f"Résultats de recherche :\n{condense(sources, query)}\nQuestion : {question}"



def filter_results(results: list[dict], query: str) -> list[dict]:
    """Drops off-topic results (whale videos for a weekend in Amsterdam, Toulouse for Reims)."""
    # drops results sharing no word with the query (« baleines à bosse » videos for a weekend
    # in Amsterdam, « Auquet » transcription at the start): LFM2 used to mix them into its answer
    # with a proper noun (Reims, Amsterdam), it must appear in the result: « ville » alone let Toulouse through
    names = _words(" ".join(w for w in PROPER_NOUN.findall(query) if w not in NOT_PROPER))
    kw = names or _words(query)
    if kw:
        return [x for x in results if _words(f"{x.get('title', '')} {x.get('content', '')}") & kw]
    return results


SENTENCE_END = re.compile(r"[.!?…:;](?=\s)")


FIRST_CUT = re.compile(r",(?=\s)")


def sentences(chunks):
    """Groups the token stream into sentences (at least 15 characters to avoid « M. » or « 1. »).
    The first chunk may stop at a comma from 40 characters on, to start speaking sooner."""
    buf, first = "", True
    for c in chunks:
        buf += c
        while True:
            m = next((m for m in SENTENCE_END.finditer(buf) if m.end() >= 15), None)
            if not m and first:
                m = next((m for m in FIRST_CUT.finditer(buf) if m.end() >= 40), None)
            if not m:
                break
            yield buf[:m.end()].strip()
            buf, first = buf[m.end():], False
    if buf.strip():
        yield buf.strip()



PRONUNCIATION = next((p for p in (os.path.join(HOME, "pronunciation.txt"), os.path.join(DATA, "pronunciation.txt")) if os.path.exists(p)), "")  # « word = spelling », reloaded whenever it changes
_pron = {"mtime": None, "rules": []}


def pron_rules() -> list:
    try:
        mtime = os.path.getmtime(PRONUNCIATION)
    except OSError:
        return []
    if mtime != _pron["mtime"]:
        rules = []
        with open(PRONUNCIATION, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = (x.strip() for x in line.split("=", 1))
                    rules.append((re.compile(rf"(?<![\w-]){re.escape(k)}(?![\w-])", re.I), v))
        _pron.update(mtime=mtime, rules=rules)
    return _pron["rules"]


def _heures(h: str, m: str = "00") -> str:
    n = int(h)
    return f"{n} heure{'s' if n > 1 else ''}" + ("" if m == "00" else f" {int(m)}")


# Piper (espeak) reads « 24°C » as « degrés cé », « km/h » as « kilomètres ache », « 19h00 » as « dix-neuf ache zéro zéro »,
# « 3-1 » as « trois un », « flashscore.fr » as « flashscore èf èr »: we write all of that as spoken French.
UNITS = [(r"°\s?C\b", " degrés"), (r"°\s?F\b", " degrés Fahrenheit"), (r"°", " degrés"),
         (r"\bkm/h\b", " kilomètres heure"), (r"\bm/s\b", " mètres par seconde"),
         (r"(?<=\d)\s?mm\b", " millimètres"), (r"(?<=\d)\s?cm\b", " centimètres"),
         (r"(?<=\d)\s?kg\b", " kilos"), (r"(?<=\d)\s?km\b", " kilomètres")]


def to_speech(text: str) -> str:
    t = re.sub(r"[*#_`]", "", text).replace("’", "'")
    t = re.sub(r"\b(?:www|fr|en|m)\.(?=[\w-]+\.)", "", t)                                      # www.x.fr -> x.fr
    t = re.sub(r"\b([\w-]+)\.(?:fr|com|net|org|io|be|ch|ca|eu|tv|info)\b", r"\1", t, flags=re.I)  # x.fr -> x
    t = re.sub(r"(?<=[a-z])\.(?=[a-z])", " ", t)                                                # eau.grandreims -> eau grandreims
    t = re.sub(r"(?<![\d/.-])(\d{1,3})\s?-\s?(\d{1,3})(?![\d/.-])", r"\1 à \2", t)               # 3-1 -> 3 à 1
    t = re.sub(r"\b(\d{1,2})\s?h\s?(\d{2})\b", lambda m: _heures(m[1], m[2]), t)                 # 19h30
    t = re.sub(r"\b(\d{1,2})\s?h\b", lambda m: _heures(m[1]), t)                                   # 19h
    for pattern, repl in UNITS:
        t = re.sub(pattern, repl, t)
    for pattern, repl in pron_rules():
        t = pattern.sub(repl, t)
    return re.sub(r" {2,}", " ", t)


def drop_fake_source(s: str) -> str:
    """Without a search, LFM2 invents sources (« selon le site officiel de l'INSEE »): we remove the « selon… » part."""
    if not re.search(r"\bselon\b", s, re.I):
        return s
    s = re.sub(r"^\s*selon (?:[^,.!?]|\.(?=\w))*,\s*", "", s, flags=re.I)   # « Selon X, c'est simple. » (a dot followed by a letter = domain name)
    s = re.sub(r",?\s*selon\b(?:[^.!?]|\.(?=\w))*", "", s, flags=re.I).strip()  # « 391, selon marmiton.com. »
    if not re.search(r"\w", s):
        return ""
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".!?…:" else s + "."


def web_search(query: str, n: int = 4) -> list[dict]:
    url = SEARX_URL + "?" + urllib.parse.urlencode({"q": query, "format": "json", "language": "fr"})
    with urllib.request.urlopen(url, timeout=20) as r:
        results = json.load(r).get("results", [])
    results = filter_results(results, query)
    return [{"titre": x.get("title", ""), "extrait": (x.get("content") or "")[:300],
             "url": x.get("url", ""), "date": x.get("publishedDate") or ""} for x in results[:n]]


def reset_topic() -> None:
    """After a tool answer (time, weather...): a follow-up question must not rerun the old search."""
    global last_query
    last_query = None

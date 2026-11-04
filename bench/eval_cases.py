"""Murmure evaluation bench: real questions (captured from the user) and verifiable criteria.

Each case: (id, question, criterion). The criterion receives (answer heard, context sent to the model) and returns
(ok, reason). The "flags" measure cross-cutting defects (formal "vous", fake source, loop, English...).
Used by ev2/build_ctx.py (freezes the contexts), ev2/run_model.py (queries a model) and ev2/score_all.py.
"""
import re

CJK = re.compile(r"[㐀-鿿぀-ヿ가-힯]")
VOUS = re.compile(r"\b(vous|votre|vos|veuillez)\b", re.I)
ENGLISH = re.compile(r"\b(the|and|is|are|of|with|you|your|this|that|for)\b", re.I)
SCORE = re.compile(r"\b\d{1,2}\s?(?:-|–|à|contre|:)\s?\d{1,2}\b")
CANT = re.compile(r"(ne (peux|sais|trouve|dispose|connais)|n'(ai|arrive)|pas (encore )?(capable|possible|accès|d'information|de résultat|trouvé)|aucun|impossible|introuvable)", re.I)
FOOD = re.compile(r"pâtes|salade|omelette|riz|soupe|poulet|légume|œuf|oeuf|sandwich|wrap|pizza|quiche|tarte|poisson|steak|croque|gratin|nouilles|curry|risotto|tomate|burger|galette|crêpe|saumon|thon|fromage|avocat|quinoa|pomme de terre|gnocchi|tacos|lentilles|sauté|wok|ramen|pesto", re.I)


def names(text: str) -> set[str]:
    """Capitalized or very specific words (≥ 5 letters): used to check that a cited name really comes from the results."""
    return {w.lower() for w in re.findall(r"[A-ZÀ-Ý][\wÀ-ÿ'’-]{4,}", text)}


def grounded(ans: str, ctx: str, minimum: int) -> bool:
    return len(names(ans) & names(ctx)) >= minimum


def _puretech(a, c): return (bool(re.search(r"problème|défaut|souci|rappel|critiq|fragil|casse|usure|surconsommation|courroie", a, re.I)), "must mention the known problems")
def _thp(a, c): return (bool(re.search(r"THP|1[.,]6|Prince|PSA|BMW|Peugeot|Citro|Mini|turbo", a, re.I)) and not CANT.search(a), "must talk about the 1.6 THP engine")
def _spas(a, c): return (grounded(a, c, 2) and not CANT.search(a), "must cite at least 2 spas present in the results")
def _vesle(a, c): return ("vesle" in a.lower() and "seine" not in a.lower(), "the river is the Vesle")
def _reims(a, c): return (bool(re.search(r"cathédrale|champagne|sacre|Notre-Dame|histoire|patrimoine|UNESCO|Gaulois|romain", a, re.I)) and "toulouse" not in a.lower(), "must talk about Reims (not Toulouse)")
def _amsterdam(a, c): return (bool(re.search(r"canaux|Van Gogh|Anne Frank|Rijksmuseum|Jordaan|musée|quartier|Vondelpark", a, re.I)) and "baleine" not in a.lower(), "Amsterdam sights, no whales")
def _mons(a, c): return ("mons" in a.lower() and grounded(a, c, 1) and not re.search(r"Notre-Dame|Senne|gaufre li", a, re.I), "Mons, details taken from the results, no Notre-Dame cathedral or Senne")
def _psg(a, c): return (not (SCORE.search(a) and not SCORE.search(c)), "must not invent a score absent from the results")
def _learjet(a, c):
    bad = re.search(r"\b(six|sept|huit|cinq|quatre|dix|onze|douze|trois|[0-8]|1\d)\s+(personnes|morts|victimes|occupants)", a, re.I)
    return (bool(re.search(r"Floride|Palm Beach", a, re.I)) and not bad, "Palm Beach / Florida, 9 victims")
def _wc(a, c): return ("argentine" in a.lower(), "Argentina")
def _bonjour(a, c): return (bool(re.search(r"bonjour|salut|coucou|hello", a, re.I)) and not VOUS.search(a) and not re.search(r"émotion|ressent|sentiment|intelligence artificielle|programme", a, re.I) and len(a) < 220, "natural greeting, informal \"tu\", no talk about its emotions")
def _diner(a, c): return (bool(FOOD.search(a)) and not VOUS.search(a) and "selon" not in a.lower(), "a dish idea, informal \"tu\"")
def _enigme(a, c): return (bool(re.search(r"\b(5|cinq)\b", a)) and not re.search(r"\b(6|six|7|sept|9|neuf|4|quatre|8|huit)\s+enfants", a, re.I) and not loops(a), "answer: 5 children")
def _reveil(a, c): return (bool(re.search(r"ne (peux|sais)|pas (encore )?(capable|possible)", a, re.I)) and not re.search(r"(j'ai|c'est) (réglé|programmé|fait|éteint)|a été (réglé|programmé|éteint)|(réveil|alarme) (est|sera) (réglé|programmé)", a, re.I), "must say it cannot control that, without pretending to have done it")
def _calcul(a, c): return ("391" in a, "17 × 23 = 391")
def _blague(a, c): return (len(a) > 15 and not VOUS.search(a) and "selon" not in a.lower(), "a short joke, informal \"tu\"")
def _photo(a, c): return (bool(re.search(r"lumière|plantes?|chlorophylle|CO2|dioxyde|énergie|glucose|oxygène", a, re.I)), "definition of photosynthesis")

CASES = [
    ("puretech", "Parle-moi du moteur 1.2L puretech, il est fiable ?", _puretech),
    ("thp", "Parle-moi du moteur un litre six THP.", _thp),
    ("spas", "Quels sont les meilleurs spas à Reims ?", _spas),
    ("vesle", "Quelle rivière traverse Reims ?", _vesle),
    ("reims", "Parle moi un petit peu de la ville de Reims.", _reims),
    ("amsterdam", "Auquet, j'ai un voyage à Amsterdam ce week-end avec ma compagne, que me conseilles-tu de voir en priorité?", _amsterdam),
    ("mons", "Ok je veux partir à Mons, en Belgique est ce que c'est une bonne idée et que puis-je faire là-bas?", _mons),
    ("psg", "Cherche les résultats du dernier match du PSG.", _psg),
    ("learjet", "Fais des recherches sur le crash du Learjet 60.", _learjet),
    ("coupe2022", "Qui a gagné la Coupe du monde de football 2022 ?", _wc),
    ("photosynthese", "C'est quoi la photosynthèse ?", _photo),
    ("bonjour", "Bonjour Murmure, ça va ?", _bonjour),
    ("diner", "Donne-moi une idée de dîner rapide.", _diner),
    ("enigme", "Marie a 3 frères. Chacun de ses frères a 2 sœurs. Combien d'enfants y a-t-il dans la famille ?", _enigme),
    ("reveil", "Mets un réveil à 7h15 et éteins la lumière du salon.", _reveil),
    ("calcul", "Combien font 17 fois 23 ?", _calcul),
    ("blague", "Raconte-moi une blague.", _blague),
]


def loops(a: str) -> bool:
    """Same 30-character chunk repeated 3 or more times (generation loop)."""
    chunks = [a[i:i + 30] for i in range(0, max(0, len(a) - 30), 10)]
    return any(a.count(c) >= 3 for c in set(chunks) if c.strip())


def flags(a: str, has_sources: bool) -> list[str]:
    out = []
    if VOUS.search(a): out.append("vous")
    if not has_sources and re.search(r"\bselon\b", a, re.I): out.append("fausse-source")
    if CJK.search(a): out.append("cjk")
    if loops(a): out.append("boucle")
    if len(ENGLISH.findall(a)) >= 4: out.append("anglais")
    if len(a) > 480 or len(re.findall(r"[.!?]\s", a)) >= 4: out.append("long")
    if not a.strip(): out.append("vide")
    return out


def score(case_id: str, ans: str, ctx: str) -> tuple[bool, str]:
    for cid, _, check in CASES:
        if cid == case_id:
            return check(ans, ctx)
    raise KeyError(case_id)

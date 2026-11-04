"""Regression tests for Murmure rules (pure, no NAS): `python -m tests.test_rules`."""
from murmure import rules as R

S = "SEARCH"  # a search (any query)


def decide_cases():
    N = R.normalize_question
    return [
        # (question, expected: None | S | exact query)
        ("Parle-moi du moteur 1.2L puretech, il est fiable ?", "moteur 1.2L puretech, il est fiable"),
        (N("Parle-moi du moteur un litre six THP."), "moteur 1.6 THP"),
        (N("Donne-moi des détails sur le moteur un litre six THP."), "moteur 1.6 THP"),
        ("Parle moi un petit peu de la ville de Reims.", "ville de Reims"),
        ("Tu peux me parler du 1.6 THP s'il te plaît ?", "1.6 THP"),
        ("Est-ce que le 1.2 PureTech est fiable ?", "1.2 PureTech est fiable"),
        ("Ok je veux partir à Mons, en Belgique est ce que c'est une bonne idée et que puis-je faire là-bas?", S),
        ("Bonjour Murmure, ça va ?", None), ("Comment ça va ?", None), ("Ok merci", None), ("Merci beaucoup", None), ("Salut Murmure !", None),
        ("Ok, raconte-moi une blague", None), ("Dis moi une blague", None), ("Raconte-moi une blague", None),
        ("Donne-moi une idée de dîner rapide.", None),
        ("Combien font 17 fois 23 ?", None), ("Combien font 144 divisé par 12 ?", None),
        ("Mais un réveil à 7h15 éteint la lumière du salon.", None), ("Et la lumière du salon ?", None),
        ("Marie a 3 frères. Chacun de ses frères a 2 sœurs. Combien d'enfants y a-t-il dans la famille ?", None),
        ("Si j'ai 12 pommes et que j'en donne 5, combien m'en reste-t-il ?", None),
        ("Un bon restaurant pour dîner à Reims ?", S), ("Raconte-moi l'histoire de Reims", "histoire de Reims"),
        ("Bonjour, quelle météo demain à Reims ?", "quelle météo demain à Reims"),
        ("Quelle rivière traverse Reims ?", "Quelle rivière traverse Reims"), ("C'est quoi la photosynthèse ?", "photosynthèse"),
        ("Qui a gagné la Coupe du monde 2022 ?", S), ("Combien coûte un iPhone 16 ?", S),
        ("Cherche les résultats du dernier match du PSG.", "les résultats du dernier match du PSG"),
        ("Fais des recherches sur le crash du Learjet 60.", "le crash du Learjet 60"),
        ("Cherche une recette de crêpes.", "une recette de crêpes"),
    ]


def follow_up_cases():
    return [  # sequences: the follow-up question picks up the previous topic
        [("Donne-moi une idée de dîner rapide.", None), ("Et sans fromage ?", None)],
        [("Cherche les résultats du dernier match du PSG.", "les résultats du dernier match du PSG"),
         ("Et le prochain match ?", "les résultats du dernier match du PSG Et le prochain match")],
        [("Fais des recherches sur le crash du Learjet 60.", "le crash du Learjet 60"),
         ("Combien de personnes étaient à bord ?", "le crash du Learjet 60 Combien de personnes étaient à bord")],
        [("Ok je veux partir à Mons, en Belgique est ce que c'est une bonne idée et que puis-je faire là-bas?", S),
         ("Et tu as quoi me recommander à faire là-bas en plus de ce que tu m'as cité?", "Mons Belgique Et tu as quoi me recommander à faire là-bas en plus de ce que tu m'as cité")],
    ]


def speech_cases():
    return {"Il fera 24°C à Reims, vent à 20 km/h.": "Il fera 24 degrés à Rince, vent à 20 kilomètres heure.",
            "Le PSG a gagné 3-1 selon flashscore.fr, à 19h00.": "Le PSG a gagné 3 à 1 selon flashscore, à 19 heures.",
            "Selon eau.grandreims.fr, la Vesle.": "Selon eau grandreims, la Vèle.",
            "Murmure regarde YouTube sur ton iPhone.": "Murmure regarde Youtioube sur ton Aïfone."}


def drop_cases():
    return {"17 multiplié par 23 fait 391, selon les calculs standards.": "17 multiplié par 23 fait 391.",
            "Selon le site « Cuisine Facile », c'est une recette simple.": "C'est une recette simple.",
            "Selon un site de calcul.": "", "Tu peux faire des pâtes au pesto.": "Tu peux faire des pâtes au pesto.",
            "Une idée simple est de faire un ragoût, selon marmiton.com.": "Une idée simple est de faire un ragoût.",
            "Selon marmiton.com, c'est rapide.": "C'est rapide.", "Fais un ragoût selon www.cuisine.fr": "Fais un ragoût."}


def run() -> int:
    fails = 0

    def check(label, got, exp, ok=None):
        nonlocal fails
        good = (got is not None) if exp == S else got == exp
        if not good:
            fails += 1
            print(f"KO {label!r:70.70s} -> {got!r} (expected {exp!r})")

    for q, exp in decide_cases():
        R.last_query = ""
        check(q, R.decide(q), exp)
    for seq in follow_up_cases():
        R.last_query = ""
        for q, exp in seq:
            check(q, R.decide(q), exp)
    for a, b in speech_cases().items():
        check(a, R.to_speech(a), b)
    for a, b in drop_cases().items():
        check(a, R.drop_fake_source(a), b)
    total = len(decide_cases()) + sum(len(s) for s in follow_up_cases()) + len(speech_cases()) + len(drop_cases())
    print(f"{total - fails}/{total} tests OK")
    return fails


if __name__ == "__main__":
    raise SystemExit(run())

"""Tests of Murmure tools (time, date, weather, timers, example plugin): `python -m tests.test_tools`, no network."""
import datetime as dt, json, os, time
os.environ.update(MURMURE_CITY="Reims", MURMURE_LAT="49.2583", MURMURE_LON="4.0317",  # the test cases are written for Reims
                  MURMURE_NOTIFY_CMD="notify", MURMURE_STATUS_JSON="/x/status.json", MURMURE_CHECK_JSON="/x/check.json",
                  MURMURE_PLUGINS=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "plugins"))
from murmure import tools as T
BK = T.PLUGINS["backup_status"]  # example plugin (examples/plugins)

HERE = os.path.dirname(os.path.abspath(__file__))
METEO = json.load(open(os.path.join(HERE, "meteo_sample.json")))  # real Open-Meteo response (Reims, 3 Nov 2026)
STATUS = {"status": "ok", "finished": "2026-11-03T05:02:36+01:00", "files_new": 219, "bytes_added": 200448299, "repo_bytes": 109908659033, "snapshots": 59}
CHECK = {"status": "failed", "finished": "2026-11-01T06:00:13+01:00"}
MARDI_21H42 = time.mktime((2026, 11, 3, 21, 42, 0, 0, 0, -1))
fails = 0


def check(label, got, exp):
    global fails
    if got != exp:
        fails += 1
        print(f"KO {label}\n    obtenu : {got!r}\n    attendu: {exp!r}")


# numbers and durations
for w, n in {"10": 10, "dix": 10, "vingt-cinq": 25, "trente et une": 31, "soixante-dix": 70, "quatre-vingt-dix": 90, "quinze": 15, "blabla": None}.items():
    check(f"french_number({w!r})", T.french_number(w), n)
for q, secs in {"Mets un minuteur de 10 minutes": 600, "minuteur de dix minutes": 600, "dans une heure": 3600, "dans 1 heure 30": 5400,
                "dans 2 heures": 7200, "un quart d'heure": 900, "une demi-heure": 1800, "dans vingt-cinq minutes": 1500, "dans 45 secondes": 45,
                "rappelle-moi à 22h30": 48 * 60, "réveil à 7h15": (9 * 3600 + 33 * 60)}.items():
    d = T.parse_delay(q, MARDI_21H42)
    check(f"parse_delay({q!r})", d[0] if d else None, secs)
check("parse_delay without duration", T.parse_delay("mets un minuteur", MARDI_21H42), None)
check("parse_delay 25h", T.parse_delay("à 25h", MARDI_21H42), None)

# time, date
check("say_time", T.say_time(time.localtime(MARDI_21H42)), "Il est 21 heures 42.")
check("say_date", T.say_date(time.localtime(MARDI_21H42)), "Nous sommes le mardi 3 novembre 2026.")
check("say_date 1er", T.say_date(time.localtime(time.mktime((2026, 12, 1, 12, 0, 0, 0, 0, -1)))), "Nous sommes le mardi 1er décembre 2026.")

# weather (frozen real data)
lt = time.localtime(MARDI_21H42)
check("weather today", T.say_weather(METEO, 0, "Reims", lt), "À Reims, il fait 6 degrés en ce moment, et le ciel est couvert. Aujourd'hui, de 6 à 9 degrés. Pas de pluie prévue.")
check("weather tomorrow", T.say_weather(METEO, 1, "Reims", lt), "Demain à Reims, le temps sera couvert, de 3 à 10 degrés. Pas de pluie prévue.")
froid = json.loads(json.dumps(METEO)); froid["daily"]["temperature_2m_min"][1] = -2.4; froid["daily"]["precipitation_probability_max"][1] = 70
froid["daily"]["precipitation_sum"][1] = 4.2; froid["daily"]["wind_speed_10m_max"][1] = 45; froid["daily"]["weather_code"][1] = 63
check("weather rain/wind/negative", T.say_weather(froid, 1, "Reims", lt),
      "Demain à Reims, le temps sera pluvieux, de moins 2 à 10 degrés. Risque de pluie 70 pour cent, environ 4 millimètres. Vent fort, jusqu'à 45 kilomètres heure.")

# backups (real state of 3 November)
now = dt.datetime(2026, 11, 3, 21, 42)
check("backup", BK.say_backup(STATUS, CHECK, now),
      "La dernière sauvegarde s'est bien passée, cette nuit à 5 heures 02. 219 nouveaux fichiers, 200 mégaoctets ajoutés. 59 sauvegardes sont conservées, pour 110 gigaoctets. Et le dernier contrôle d'intégrité a échoué, le 1er novembre.")
check("backup failure", BK.say_backup({"status": "failed", "finished": "2026-11-02T05:00:00+01:00"}, None, now), "Attention, la dernière sauvegarde a échoué, hier à 5 heures.")

# full routing, side effects stubbed
calls = []
T.schedule = lambda secs, msg: calls.append((secs, msg)) or True
T.fetch_json = lambda url, timeout=15: METEO if "forecast" in url else {"results": [{"name": "Lyon", "latitude": 45.7, "longitude": 4.8}]}
BK.read_json = lambda path: STATUS if "status" in path else CHECK
check("route time", T.answer("Quelle heure est-il ?", MARDI_21H42), "Il est 21 heures 42.")
check("route time elsewhere", T.answer("Quelle heure est-il à Tokyo ?", MARDI_21H42), None)
check("route date", T.answer("On est quel jour ?", MARDI_21H42), "Nous sommes le mardi 3 novembre 2026.")
check("route weather", T.answer("Quel temps fera-t-il demain ?", MARDI_21H42).startswith("Demain à Reims"), True)
check("route weather follow-up", T.answer("Et après-demain ?", MARDI_21H42).startswith("Après-demain à Reims"), True)
check("route weather city", T.answer("Quel temps fait-il à Lyon ?", MARDI_21H42).startswith("À Lyon"), True)
check("route backup", T.answer("La sauvegarde de cette nuit s'est bien passée ?", MARDI_21H42).startswith("La dernière sauvegarde s'est bien passée"), True)
calls.clear()
check("timer", T.answer("Mets un minuteur de 10 minutes", MARDI_21H42), "C'est noté, minuteur dans 10 minutes. Je t'enverrai une notification sur ton téléphone.")
check("timer call", calls, [(600, "Minuteur terminé")]); calls.clear()
check("reminder", T.answer("Rappelle-moi dans 20 minutes de sortir le linge", MARDI_21H42), "C'est noté, rappel dans 20 minutes pour sortir le linge. Je t'enverrai une notification sur ton téléphone.")
check("reminder call", calls, [(1200, "Rappel : sortir le linge")]); calls.clear()
check("alarm + light", T.answer("Mets un réveil à 7h15 et éteins la lumière du salon.", MARDI_21H42),
      "C'est noté, réveil à 7 heures 15. Je t'enverrai une notification sur ton téléphone. Je ne peux pas encore commander les lumières.")
check("timer without duration", T.answer("Mets un minuteur", MARDI_21H42).startswith("Dis-moi dans combien de temps"), True)
for q in ["Quelle rivière traverse Reims ?", "Raconte-moi une blague", "Cherche le prix du Bitcoin", "Qu'est-ce qu'un orage ?", "La température d'ébullition de l'eau ?", "Parle-moi du moteur 1.6 THP"]:
    check(f"not a tool: {q}", T.answer(q, MARDI_21H42), None)
# exact calculation
for q, exp in {"Combien font 17 fois 23 ?": "17 fois 23 font 391.", "Combien font 144 divisé par 12 ?": "144 divisé par 12 font 12.", "Combien font 7 plus 5 ?": "7 plus 5 font 12.",
               "Combien font 10 divisé par 4 ?": "10 divisé par 4 font 2,5.", "Combien font 5 divisé par 0 ?": "On ne peut pas diviser par zéro.", "Combien font 12 moins 20 ?": "12 moins 20 font -8."}.items():
    check(f"calc {q!r}", T.answer(q, MARDI_21H42), exp)
check("calc: not an operation", T.answer("Combien de personnes étaient à bord ?", MARDI_21H42), None)
# basic conversation
for q, exp in {"Bonjour Murmure, ça va ?": "Ça va très bien, merci ! Et toi ?", "Ok merci": "Avec plaisir !", "Salut Murmure": "Bonsoir ! Que puis-je faire pour toi ?",
               "Merci beaucoup": "Avec plaisir !", "Bonne nuit": "Bonne nuit !", "Tu m'entends ?": "Oui, je suis là."}.items():
    check(f"smalltalk {q!r}", T.answer(q, MARDI_21H42), exp)
check("who are you", T.answer("Qui es-tu ?", MARDI_21H42).startswith("Je suis Murmure"), True)
check("home action", T.answer("Allume la lumière du salon", MARDI_21H42).startswith("Je ne peux pas encore commander"), True)
check("music action", T.answer("Mets de la musique", MARDI_21H42).startswith("Je ne peux pas encore commander"), True)
check("not an action", T.answer("Mets-moi une idée de dîner", MARDI_21H42), None)
# optional pieces: plugin inert without configuration, timers disabled without a notification command, capabilities list
check("capabilities list", T.capabilities(), "Je peux chercher sur Internet, te donner l'heure, la date et la météo, te dire si les sauvegardes se sont bien passées et programmer des minuteurs et des rappels.")
BK.STATUS_JSON = ""
check("plugin inert when unconfigured", BK.answer("La sauvegarde s'est bien passée ?", MARDI_21H42), None)
cmd, T.NOTIFY_CMD = T.NOTIFY_CMD, []
check("timers disabled without notification command", T.answer("Mets un minuteur de 10 minutes", MARDI_21H42), "Les minuteurs et les rappels ne sont pas configurés.")
check("capabilities without timers", "minuteurs" in T.capabilities(), False)
T.NOTIFY_CMD = cmd
check("weather URL timezone", "timezone=auto" in T.weather_url(49.2, 4.0), True)

print(f"{'tous les tests OK' if not fails else str(fails) + ' échec(s)'}")
raise SystemExit(1 if fails else 0)

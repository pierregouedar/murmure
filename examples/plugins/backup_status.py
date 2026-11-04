"""Example plugin: « La sauvegarde de cette nuit s'est bien passée ? » answered from the JSON files of a backup job.

Inert unless MURMURE_STATUS_JSON is set. Expected content (written by your backup script after each run):
    status.json  {"status": "ok" | "partial" | "failed", "finished": "2026-11-03T05:02:36+01:00",
                  "files_new": 219, "bytes_added": 200448299, "snapshots": 59, "repo_bytes": 109908659033}
    check.json   {"status": "ok" | "failed", "finished": "..."}   (optional: MURMURE_CHECK_JSON, integrity check)

A plugin is any module with `answer(question, now) -> str | None`; `ABOUT` completes « Je peux … » in the capabilities list.
"""
import datetime as dt, json, os, re

STATUS_JSON = os.environ.get("MURMURE_STATUS_JSON", "")
CHECK_JSON = os.environ.get("MURMURE_CHECK_JSON", "")
ABOUT = "te dire si les sauvegardes se sont bien passées" if STATUS_JSON else ""
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
BACKUP_Q = re.compile(r"\b(sauvegardes?|backups?)\b", re.I)
STATE_Q = re.compile(r"passé|réussi|marché|dernière|état|statut|bien|échoué|problème|fait", re.I)


def _quand(iso: str, now: dt.datetime) -> str:
    t = dt.datetime.fromisoformat(iso).replace(tzinfo=None)
    heure = f"{t.hour} heures" + (f" {t.minute:02d}" if t.minute else "")
    delta = (now.date() - t.date()).days
    if delta == 0:
        return f"cette nuit à {heure}" if t.hour < 6 else f"aujourd'hui à {heure}"
    if delta == 1:
        return f"hier à {heure}"
    return f"le {'1er' if t.day == 1 else t.day} {MOIS[t.month - 1]}"


def say_backup(status: dict, check: dict | None, now: dt.datetime) -> str:
    quand = _quand(status["finished"], now)
    if status.get("status") == "ok":
        txt = f"La dernière sauvegarde s'est bien passée, {quand}."
        if status.get("files_new") is not None:
            txt += f" {status['files_new']} nouveaux fichiers, {round(status.get('bytes_added', 0) / 1e6)} mégaoctets ajoutés."
        if status.get("snapshots"):
            txt += f" {status['snapshots']} sauvegardes sont conservées, pour {round(status.get('repo_bytes', 0) / 1e9)} gigaoctets."
    elif status.get("status") == "partial":
        txt = f"La dernière sauvegarde, {quand}, s'est terminée avec des fichiers non sauvegardés."
    else:
        txt = f"Attention, la dernière sauvegarde a échoué, {quand}."
    if check and check.get("status") not in (None, "ok"):
        txt += f" Et le dernier contrôle d'intégrité a échoué, {_quand(check['finished'], now)}."
    return txt


def read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def answer(question: str, now: float) -> str | None:
    if not STATUS_JSON or not (BACKUP_Q.search(question) and STATE_Q.search(question)):
        return None
    status = read_json(STATUS_JSON)
    if not status:
        return "Je ne trouve pas l'état des sauvegardes."
    return say_backup(status, read_json(CHECK_JSON) if CHECK_JSON else None, dt.datetime.fromtimestamp(now))

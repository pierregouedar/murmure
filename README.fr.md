<div align="center">

<img src="assets/logo.svg" width="120" alt="Logo de Murmure">

# Murmure

**Un assistant vocal auto-hébergé qui répond vite, n'invente pas, et tourne sur un petit serveur maison.**

[![Licence : GPL-3.0-or-later](https://img.shields.io/badge/licence-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![tests](https://github.com/pierregouedar/murmure/actions/workflows/ci.yml/badge.svg)](https://github.com/pierregouedar/murmure/actions/workflows/ci.yml)
![auto-hébergé](https://img.shields.io/badge/auto--h%C3%A9berg%C3%A9-oui-2ea44f)
![réglé pour le français](https://img.shields.io/badge/r%C3%A9gl%C3%A9%20pour-le%20fran%C3%A7ais-informational)

[English](README.md) · Français

</div>

On appuie sur un bouton dans le navigateur, on parle, et Murmure répond à voix haute, phrase par phrase. La reconnaissance et la
synthèse vocales tournent sur ta machine ; le modèle de langage peut être une API cloud, un PC avec GPU qui se réveille à la demande,
un petit modèle local, ou un mélange.

## Pourquoi Murmure

- **Voix locale.** Parakeet TDT 0.6B v3 (int8) transcrit, Piper parle. L'audio ne quitte jamais ta machine.
- **Outils instantanés, sans LLM.** Heure, date, météo ([Open-Meteo](https://open-meteo.com)), minuteurs et rappels, calculs exacts : moins d'une seconde, et rien ne peut être inventé.
- **Recherche par défaut.** Les questions factuelles passent par ton propre [SearXNG](https://github.com/searxng/searxng) ; le modèle ne voit que des extraits filtrés, et s'il n'y a rien Murmure le dit au lieu de répondre de mémoire.
- **Tout LLM compatible OpenAI, avec repli.** Une liste ordonnée dans `servers.json` : par exemple une API cloud d'abord (première phrase en 1 à 3 s), puis un PC GPU réveillé par Wake-on-LAN, puis un petit modèle llama.cpp qui marche hors ligne. Un serveur en échec est évité 60 s.
- **Les sujets sensibles restent à la maison.** Santé, argent, mots de passe : seuls les serveurs que tu marques `private` les reçoivent.
- **Extensible.** Dépose un fichier Python dans `plugins/` et il peut répondre avant le LLM.

```
micro (navigateur) ─► Parakeet (STT) ─► outils instantanés ─► recherche web ─► chaîne de LLM ─► Piper (TTS) ─► haut-parleur
                                        heure, météo,         SearXNG         servers.json :
                                        minuteurs, calcul,                    cloud, PC GPU,
                                        plugins                               modèle local
```

## Démarrage rapide

Prérequis : Python 3.11+ (testé en 3.13), `ffmpeg`, une instance [SearXNG](https://github.com/searxng/searxng) avec le format JSON activé,
et au moins un serveur compatible OpenAI (par exemple un `llama-server` [llama.cpp](https://github.com/ggml-org/llama.cpp) local).

```bash
git clone https://github.com/pierregouedar/murmure.git && cd murmure
python3 -m venv .venv && .venv/bin/pip install .

mkdir voices   # mettre une voix Piper ici, par ex. fr_FR-upmc-medium.onnx et son .onnx.json
               # https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR/upmc/medium

cp deploy/murmure.env.example murmure.env     # à adapter
cp deploy/servers.example.json servers.json   # à adapter : tes serveurs LLM
set -a; . ./murmure.env; set +a
.venv/bin/python -m murmure
```

Ouvrir `http://127.0.0.1:8080`. Le navigateur n'autorise le micro que sur `localhost` ou en HTTPS : pour une autre machine, définir
`MURMURE_CERT` et `MURMURE_KEY` et Murmure sert lui-même le HTTPS. Des exemples d'unités systemd sont dans `deploy/systemd/`.

Murmure se lance depuis son **dossier d'instance** (`MURMURE_HOME`, le dossier courant par défaut), qui contient `servers.json`,
`voices/`, `plugins/`, `system_prompt.txt`, `pronunciation.txt` et les fichiers de clés.

## Configuration

Tout est facultatif ; voir [`deploy/murmure.env.example`](deploy/murmure.env.example).

| Variable | Rôle | Défaut |
|---|---|---|
| `MURMURE_HOME` | dossier d'instance | dossier courant |
| `MURMURE_BIND`, `MURMURE_PORT` | adresse d'écoute | `127.0.0.1:8080` |
| `MURMURE_CERT`, `MURMURE_KEY` | certificat et clé : sert le HTTPS | HTTP |
| `MURMURE_SERVERS_FILE` | chaîne de serveurs LLM | `servers.json` |
| `MURMURE_LLM_URL` | un serveur local unique sans fichier de serveurs | `http://127.0.0.1:8080` |
| `MURMURE_SEARX_URL` | point d'accès de recherche SearXNG | `http://127.0.0.1:8888/search` |
| `MURMURE_CITY`, `MURMURE_LAT`, `MURMURE_LON`, `MURMURE_TZ` | lieu et fuseau horaire de la météo | Paris, `auto` |
| `MURMURE_VOICE`, `MURMURE_SPEAKER` | voix et locuteur Piper | `voices/fr_FR-upmc-medium.onnx`, 1 |
| `MURMURE_NOTIFY_CMD` | commande lancée à la fin d'un minuteur ou d'un rappel, sous la forme `<cmd> "<titre>" "<message>"` | aucune : minuteurs désactivés |
| `MURMURE_PLUGINS` | dossier des plugins | `plugins/` |

Deux fichiers sont rechargés à chaud, sans redémarrage : `pronunciation.txt` (comment Piper doit dire certains mots ; un défaut français
est fourni avec le paquet) et `system_prompt.txt` (remplace le prompt système intégré).

### Serveurs LLM

`servers.json` est une liste ordonnée ; le premier serveur qui répond gagne et le dernier est toujours essayé : mets-y celui qui est toujours allumé.

```json
[
  {"name": "cloud", "url": "https://openrouter.ai/api", "key_file": "openrouter.key", "timeout": 8,
   "extra": {"model": "deepseek/deepseek-v4-flash-0731"}},
  {"name": "gpu-pc", "url": "http://192.168.1.50:8091", "private": true,
   "wake": {"mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255"}},
  {"name": "local", "url": "http://127.0.0.1:8090", "private": true}
]
```

| Champ | Sens |
|---|---|
| `url` | URL de base ; `/v1/chat/completions` est ajouté (OpenRouter, OpenAI, Groq, Ollama, vLLM, llama.cpp…) |
| `key`, `key_file`, `key_env` | clé d'API, fichier du dossier d'instance, ou variable d'environnement. Un serveur dont la clé est introuvable est ignoré : un même fichier marche sur toutes les machines |
| `extra` | champs ajoutés à chaque requête (modèle, options du fournisseur…) |
| `timeout` | secondes avant d'abandonner (10 par défaut ; 180 pour les serveurs `private`, un processeur peut être lent) |
| `private` | reçoit les questions sensibles, et lui seul. Sans serveur privé, rien n'est filtré |
| `wake` | une machine qui dort : sondée sur `/health`, réveillée par Wake-on-LAN (au plus une fois par 90 s) pendant que le serveur suivant répond |

### Plugins

Un plugin est un fichier Python dans `plugins/` avec `answer(question, now) -> str | None`. Il est interrogé après les outils intégrés et avant
le LLM ; renvoie `None` pour passer. Une chaîne `ABOUT` facultative complète « Je peux… » dans la liste des capacités. [`examples/plugins/backup_status.py`](examples/plugins/backup_status.py)
répond à « la sauvegarde de cette nuit s'est bien passée ? » à partir des fichiers JSON d'une tâche de sauvegarde.

### Minuteurs et rappels

Définir `MURMURE_NOTIFY_CMD` avec une commande qui envoie une notification, par exemple [`deploy/notify-ntfy.sh`](deploy/notify-ntfy.sh). Avec systemd,
les minuteurs survivent à un redémarrage ; sinon ils vivent en mémoire.

## Langue

Murmure parle **français** aujourd'hui. Parakeet v3 est multilingue et le prompt système est rédigé en anglais (les réponses suivent la langue de
l'utilisateur, le français par défaut), mais la voix Piper, les règles de routage (`murmure/rules.py`), les phrases des outils (`murmure/tools.py`) et la
liste de mots sensibles sont en français. Ajouter une langue, c'est écrire ses règles et ses phrases et choisir une voix adaptée.

## Organisation du dépôt

```
murmure/      le paquet : serveur (HTTP, STT, TTS), règles (quoi chercher, nettoyage), outils (réponses instantanées), backend (chaîne de serveurs)
examples/     plugin d'exemple
deploy/       exemples d'env et de serveurs, unités systemd, notifieur ntfy
tests/        tests unitaires, bibliothèque standard seule : python -m tests
bench/        banc d'évaluation : contextes de recherche figés, essais par modèle, notation
assets/       logo
```

## Tests et banc d'essai

```bash
python -m tests        # sans réseau, sans modèle
```

`bench/` compare des modèles sur 17 vraies questions de recherche avec le vrai pipeline (décision, SearXNG, filtrage, prompt) :
`python bench/build_ctx.py` fige les contextes, `BENCH_URL=… BENCH_MODEL=… python bench/run_model.py <nom>` interroge un modèle,
`python bench/score_all.py [--detail]` note. Les résultats vont dans `BENCH_OUT` (`bench-results/` par défaut). La note automatique est
indulgente : relis les réponses, ça a changé le classement.

## Ce qui a été mesuré

Novembre 2026, une seule machine, ordres de grandeur uniquement. Sur un Intel N97 (4 cœurs, 16 Go, sans GPU), la lecture du prompt (~50 tokens/s)
est le goulot : 8 à 14 s avant la première phrase pour une question avec recherche, quelles que soient l'option llama.cpp, la quantification ou la
variante essayées. Sur 25 modèles locaux, Qwen3-4B-Instruct-2507 (Q4_K_M) a été le plus fiable. Par une API, la première phrase arrive en 1 à 3 s ;
sur 17 questions relues à la main, DeepSeek V4 Flash a été le plus honnête. Une question coûte environ 0,0002 $ par API. Les modèles gratuits
d'OpenRouter étaient trop saturés pour un assistant vocal.

## Limites connues

- Un seul utilisateur à la fois (verrou global).
- Si un serveur cloud lâche au milieu d'une réponse, elle s'arrête sur une erreur ; le repli n'intervient qu'avant le premier mot.
- La détection des sujets sensibles est une liste de mots : utile, pas infaillible.
- Français uniquement (voir [Langue](#langue)).

## Composants tiers

| Composant | Rôle | Licence |
|---|---|---|
| [Piper](https://github.com/OHF-Voice/piper1-gpl) (`piper-tts`) | synthèse vocale, importé par Murmure | GPL-3.0-or-later |
| [onnx-asr](https://github.com/istupakov/onnx-asr) | moteur de reconnaissance vocale | MIT |
| [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) (NVIDIA) | modèle de reconnaissance vocale | CC-BY-4.0 |
| [fr_FR-upmc-medium](https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR/upmc/medium) | voix française par défaut (non fournie) | CC BY-SA 4.0 (jeu de données) |
| [SearXNG](https://github.com/searxng/searxng), [llama.cpp](https://github.com/ggml-org/llama.cpp) | processus séparés que tu lances | AGPL-3.0, MIT |
| [Open-Meteo](https://open-meteo.com) | API météo | gratuite pour un usage non commercial, attribution requise |

## Licence

Copyright © 2026 Pierre Gouedar. Murmure est un logiciel libre sous **licence publique générale GNU v3.0 ou ultérieure** ([`LICENSE`](LICENSE)).
Il est en GPL parce qu'il importe Piper, qui est en GPL : ce qui s'appuie dessus doit rester libre de la même façon.

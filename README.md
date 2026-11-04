<div align="center">

<img src="assets/logo.svg" width="120" alt="Murmure logo">

# Murmure

**A self-hosted voice assistant that answers fast, doesn't make things up, and runs on a small home server.**

[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![tests](https://github.com/pierregouedar/murmure/actions/workflows/ci.yml/badge.svg)](https://github.com/pierregouedar/murmure/actions/workflows/ci.yml)
![self-hosted](https://img.shields.io/badge/self--hosted-yes-2ea44f)
![tuned for French](https://img.shields.io/badge/tuned%20for-French-informational)

English · [Français](README.fr.md)

</div>

Press a button in your browser, speak, and Murmure answers out loud, one sentence at a time. Speech recognition and synthesis run
on your machine; the language model can be a cloud API, a GPU box that wakes on demand, a small local model, or any mix.

## Why Murmure

- **Local voice.** Parakeet TDT 0.6B v3 (int8) transcribes, Piper speaks. Audio never leaves your machine.
- **Instant tools, no LLM.** Time, date, weather ([Open-Meteo](https://open-meteo.com)), timers and reminders, exact arithmetic: under a second, and nothing can be invented.
- **Search by default.** Factual questions go through your own [SearXNG](https://github.com/searxng/searxng); the model only sees filtered snippets, and if nothing is found Murmure says so instead of answering from memory.
- **Any OpenAI-compatible LLM, with fallback.** An ordered list in `servers.json`: for example a cloud API first (first sentence in 1 to 3 s), then a GPU PC woken up by Wake-on-LAN, then a small llama.cpp model that works offline. A failing server is skipped for 60 s.
- **Sensitive topics stay home.** Questions about health, money or passwords only reach servers you mark `private`.
- **Extensible.** Drop a Python file in `plugins/` and it can answer questions before the LLM does.

```
mic (browser) ─► Parakeet (STT) ─► instant tools ─► web search ─► LLM chain ─► Piper (TTS) ─► speaker
                                   time, weather,   SearXNG      servers.json:
                                   timers, math,                 cloud, GPU box,
                                   plugins                       local model
```

## Quick start

Requirements: Python 3.11+ (tested on 3.13), `ffmpeg`, a [SearXNG](https://github.com/searxng/searxng) instance with the JSON format enabled,
and at least one OpenAI-compatible server (for example a local [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server`).

```bash
git clone https://github.com/pierregouedar/murmure.git && cd murmure
python3 -m venv .venv && .venv/bin/pip install .

mkdir voices   # put a Piper voice here, e.g. fr_FR-upmc-medium.onnx and its .onnx.json
               # https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR/upmc/medium

cp deploy/murmure.env.example murmure.env     # adjust
cp deploy/servers.example.json servers.json   # adjust: your LLM servers
set -a; . ./murmure.env; set +a
.venv/bin/python -m murmure
```

Open `http://127.0.0.1:8080`. The browser only allows the microphone on `localhost` or over HTTPS: for another machine, set
`MURMURE_CERT` and `MURMURE_KEY` and Murmure serves HTTPS itself. Example systemd units are in `deploy/systemd/`.

Murmure runs from its **instance directory** (`MURMURE_HOME`, the current directory by default), which holds `servers.json`,
`voices/`, `plugins/`, `system_prompt.txt`, `pronunciation.txt` and key files.

## The web page

A single page, no build step, installable on a phone's home screen. Tap the orb to talk and Murmure sends your question when you stop
speaking, or hold it and release to send. The orb shows what is happening (listening with a live level, thinking, speaking). `Space`
holds to talk, `Esc` cancels, **Stop** interrupts a long answer, and the speaker icon mutes the voice. Answers stream in as they are
spoken, with the sources as links and the timing details one tap away. The conversation is kept in the browser, and the page follows
the system theme and language (French or English). It lives in `murmure/web/` and is served as plain files.

## Configuration

Everything is optional; see [`deploy/murmure.env.example`](deploy/murmure.env.example).

| Variable | Purpose | Default |
|---|---|---|
| `MURMURE_HOME` | instance directory | current directory |
| `MURMURE_BIND`, `MURMURE_PORT` | listen address | `127.0.0.1:8080` |
| `MURMURE_CERT`, `MURMURE_KEY` | certificate and key: serve HTTPS | HTTP |
| `MURMURE_SERVERS_FILE` | LLM server chain | `servers.json` |
| `MURMURE_LLM_URL` | one local server when there is no servers file | `http://127.0.0.1:8080` |
| `MURMURE_SEARX_URL` | SearXNG search endpoint | `http://127.0.0.1:8888/search` |
| `MURMURE_CITY`, `MURMURE_LAT`, `MURMURE_LON`, `MURMURE_TZ` | weather location and timezone | Paris, `auto` |
| `MURMURE_VOICE`, `MURMURE_SPEAKER` | Piper voice and speaker | `voices/fr_FR-upmc-medium.onnx`, 1 |
| `MURMURE_NOTIFY_CMD` | command run when a timer or reminder expires, as `<cmd> "<title>" "<message>"` | none: timers disabled |
| `MURMURE_PLUGINS` | plugin directory | `plugins/` |

Two files are reloaded on the fly, no restart needed: `pronunciation.txt` (how Piper should say some words; a French default ships
with the package) and `system_prompt.txt` (replaces the built-in system prompt).

### LLM servers

`servers.json` is an ordered list; the first server that answers wins and the last one is always tried, so make it the one that is always on.

```json
[
  {"name": "cloud", "url": "https://openrouter.ai/api", "key_file": "openrouter.key", "timeout": 8,
   "extra": {"model": "deepseek/deepseek-v4-flash-0731"}},
  {"name": "gpu-pc", "url": "http://192.168.1.50:8091", "private": true,
   "wake": {"mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255"}},
  {"name": "local", "url": "http://127.0.0.1:8090", "private": true}
]
```

| Field | Meaning |
|---|---|
| `url` | base URL; `/v1/chat/completions` is appended (OpenRouter, OpenAI, Groq, Ollama, vLLM, llama.cpp...) |
| `key`, `key_file`, `key_env` | API key, a file in the instance directory, or an environment variable. A server whose key cannot be found is skipped, so one file works on every machine |
| `extra` | fields merged into every request (model, provider options...) |
| `timeout` | seconds before giving up (default 10; 180 for `private` servers, since a CPU can be slow) |
| `private` | receives sensitive questions, and is the only kind that does. With no private server, nothing is filtered |
| `wake` | a machine that sleeps: probed on `/health`, woken by Wake-on-LAN (at most once every 90 s) while the next server answers |

### Plugins

A plugin is a Python file in `plugins/` with `answer(question, now) -> str | None`. It is asked after the built-in tools and before the
LLM; return `None` to pass. An optional `ABOUT` string completes "Je peux…" in the list of capabilities. [`examples/plugins/backup_status.py`](examples/plugins/backup_status.py)
answers "did last night's backup go well?" from the JSON files of a backup job.

### Alexa (optional)

An Echo can be a microphone and speaker for Murmure through a private Alexa custom skill: *"Alexa, ouvre Murmure"*, then ask. Alexa
transcribes your voice at Amazon and reads the answer in its own voice, so audio is no longer local. The skill stays in the development
stage of your Amazon developer account: it works on your own devices and is never published.

1. `pip install ".[alexa]"`, then set `MURMURE_ALEXA_SKILL_ID` and `MURMURE_ALEXA_TOKEN` (a long random string). A separate listener
   serves only `POST /alexa/<token>` on `127.0.0.1:8765` (`MURMURE_ALEXA_BIND`, `MURMURE_ALEXA_PORT`). Publish that port, for example
   with a Cloudflare tunnel, and never the whole Murmure server. Every request is verified: certificate chain, signature, age, skill id.
2. In the [Alexa developer console](https://developer.amazon.com/alexa/console/ask), create a custom skill (French, provision your own),
   paste [`deploy/alexa/interaction-model.fr.json`](deploy/alexa/interaction-model.fr.json) in the JSON editor and build it.
3. Set the endpoint to `https://<your host>/alexa/<token>` and copy the skill id from that page into `MURMURE_ALEXA_SKILL_ID`.

Alexa answers within about 8 s, so slow local models do not fit, and sensitive questions are refused there and sent to the web page.

### Timers and reminders

Set `MURMURE_NOTIFY_CMD` to a command that delivers a notification, for instance [`deploy/notify-ntfy.sh`](deploy/notify-ntfy.sh). With systemd,
timers survive a restart; otherwise they live in memory.

## Language

Murmure speaks **French** today. Parakeet v3 is multilingual and the system prompt is written in English (answers follow the user's
language, French by default), but the Piper voice, the routing rules (`murmure/rules.py`), the tools' phrases (`murmure/tools.py`) and the
sensitive-topic word list are French. Adding a language means writing its rules and phrases and picking a matching voice.

## Project layout

```
murmure/      the package: server (HTTP, STT, TTS), rules (what to search, cleaning), tools (instant answers), backend (server chain), web/ (the page)
examples/     example plugin
deploy/       env and servers examples, systemd units, ntfy notifier
tests/        unit tests, standard library only: python -m tests
bench/        evaluation bench: frozen search contexts, per-model runs, scoring
assets/       logo
```

## Tests and benchmark

```bash
python -m tests        # no network, no model
```

`bench/` compares models on 17 real search questions with the real pipeline (decision, SearXNG, filtering, prompt):
`python bench/build_ctx.py` freezes the contexts, `BENCH_URL=… BENCH_MODEL=… python bench/run_model.py <name>` queries a model,
`python bench/score_all.py [--detail]` scores. Results go to `BENCH_OUT` (`bench-results/` by default). The automatic score is lenient:
read the answers, it changed the ranking.

## What was measured

November 2026, one machine, orders of magnitude only. On an Intel N97 (4 cores, 16 GB, no GPU), prompt reading (~50 tokens/s) is the
bottleneck: 8 to 14 s before the first sentence for a question with search, whatever the llama.cpp option, quantization or fork tried.
Of 25 local models, Qwen3-4B-Instruct-2507 (Q4_K_M) was the most reliable. Through an API the first sentence arrives in 1 to 3 s; on
17 questions reviewed by hand, DeepSeek V4 Flash was the most honest. A question costs about $0.0002 through an API. Free OpenRouter
models were too congested for a voice assistant.

## Known limitations

- One user at a time (a global lock).
- If a cloud server drops in the middle of an answer, the answer stops with an error; fallback only happens before the first word.
- Sensitive-topic detection is a word list: useful, not foolproof.
- French only (see [Language](#language)).

## Third-party components

| Component | Role | License |
|---|---|---|
| [Piper](https://github.com/OHF-Voice/piper1-gpl) (`piper-tts`) | speech synthesis, imported by Murmure | GPL-3.0-or-later |
| [onnx-asr](https://github.com/istupakov/onnx-asr) | speech recognition runtime | MIT |
| [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) (NVIDIA) | speech recognition model | CC-BY-4.0 |
| [fr_FR-upmc-medium](https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR/upmc/medium) | default French voice (not bundled) | CC BY-SA 4.0 (dataset) |
| [SearXNG](https://github.com/searxng/searxng), [llama.cpp](https://github.com/ggml-org/llama.cpp) | separate processes you run | AGPL-3.0, MIT |
| [Open-Meteo](https://open-meteo.com) | weather API | free for non-commercial use, attribution required |

## License

Copyright © 2026 Pierre Gouedar. Murmure is free software under the **GNU General Public License v3.0 or later** ([`LICENSE`](LICENSE)).
It is GPL because it imports Piper, which is GPL: anything built on it has to stay free in the same way.

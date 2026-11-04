#!/usr/bin/env python3
"""Murmure: push-to-talk web page -> Parakeet (STT) -> rules and tools -> LLM (API or local) -> Piper (TTS), streamed.

Configured through environment variables (see murmure.env.example): MURMURE_BIND, MURMURE_PORT, MURMURE_CERT and MURMURE_KEY
(HTTPS if both are set, otherwise HTTP: the browser microphone requires HTTPS except on localhost), MURMURE_VOICE, MURMURE_SPEAKER.
Serves HTTPS itself: the `tailscale serve` proxy freezes streamed responses after a few seconds of silence (verified on 3 November 2026).
The response is sent as NDJSON: one line per synthesized sentence, played by the page as soon as it arrives.
"""
import base64, io, json, os, re, ssl, subprocess, threading, time, urllib.parse, urllib.request, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import onnxruntime as ort
import onnx_asr
from piper import PiperVoice, SynthesisConfig

from . import HOME, tools
from .backend import Backend, is_sensitive
from .rules import (decide, drop_fake_source, normalize_question, reset_topic, sentences, system_message,
                    to_speech, today, user_message, web_search)

BIND = os.environ.get("MURMURE_BIND", "127.0.0.1")
PORT = int(os.environ.get("MURMURE_PORT", "8080"))
CERT, CERT_KEY = os.environ.get("MURMURE_CERT"), os.environ.get("MURMURE_KEY")
MAX_BODY = 10 * 1024 * 1024
PIPER_THREADS = 1
HISTORY_TURNS = 6
# Parakeet TDT 0.6B v3 int8: 1.2 s per sentence vs 2.7 s for Whisper small, and better on commands
# (« 7h15 éteint la lumière » instead of « 7 heures qu'un étant »). No custom vocabulary list possible.
stt = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
# voice chosen on 3 November 2026: upmc, speaker "pierre" (male), out of 11 samples
VOICE = os.environ.get("MURMURE_VOICE", os.path.join(HOME, "voices", "fr_FR-upmc-medium.onnx"))
VOICE_CFG = SynthesisConfig(speaker_id=int(os.environ.get("MURMURE_SPEAKER", "1")))  # upmc: 0 = jessica, 1 = pierre
_voice = PiperVoice.load(VOICE)
# Piper runs while LFM2 generates the next sentence: on all cores, it drops the LLM
# from 19 to 9 tok/s. We limit it to share the CPU.
_so = ort.SessionOptions()
_so.intra_op_num_threads = _so.inter_op_num_threads = PIPER_THREADS
tts = PiperVoice(config=_voice.config, session=ort.InferenceSession(
    VOICE, sess_options=_so, providers=["CPUExecutionProvider"]))
history = []
# Acknowledgements spoken right away when a search starts: otherwise 10 s of silence during the search
# and while reading the results. Synthesized once at startup (fast voice).
ACKS = ["Je regarde ça.", "Je cherche.", "Un instant, je regarde."]
ack_wavs: list[str] = []
# ponytail: a single global lock, one user at a time; the N97 would not do better in parallel
lock = threading.Lock()


backend = Backend()  # servers from servers.json, in priority order; the last one is the always-on fallback


def request(path: str, payload: dict, cand):
    body = json.dumps({**payload, **cand.extra}).encode()
    return urllib.request.urlopen(urllib.request.Request(cand.url + path, body, {"Content-Type": "application/json", **cand.headers}), timeout=cand.timeout)


def decode_audio(data: bytes) -> np.ndarray:
    """Browser format (webm/opus, ogg, wav) -> float32 mono 16 kHz.
    ffmpeg: handles every format browsers send."""
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
        input=data, capture_output=True, timeout=30, check=True).stdout
    return np.frombuffer(out, np.int16).astype(np.float32) / 32768.0


def transcribe(audio: np.ndarray) -> str:
    return stt.recognize(audio, sample_rate=16000).strip() if audio.size else ""



def stream_answer(user: str):
    """Generates the answer chunk by chunk (SSE). If a server fails before the first word, move on to the next one."""
    msgs = [system_message()] + history + \
           [{"role": "user", "content": user}]
    sensitive = is_sensitive(user) or any(is_sensitive(m["content"]) for m in history if m["role"] == "user")
    payload = {"messages": msgs, "max_tokens": 250, "temperature": 0.3, "stream": True}
    error = OSError("aucun serveur LLM disponible")
    for cand in backend.candidates(sensitive):
        started = False
        try:
            with request("/v1/chat/completions", payload, cand) as r:
                for line in r:
                    line = line.strip()
                    if not line.startswith(b"data: ") or line == b"data: [DONE]":
                        continue
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0].get("delta", {}).get("content") if chunk.get("choices") else None
                    if delta:
                        started = True
                        backend.last = cand.name
                        yield delta
            return
        except OSError as e:
            backend.fail(cand.name)
            if started:
                raise  # cut off mid-sentence: too late to switch server
            error = e
    raise error


def speak(text: str, fast: bool = False) -> bytes:
    """`fast`: all cores (first chunk, we wait for it); otherwise 1 thread to leave the CPU to LFM2."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        (_voice if fast else tts).synthesize_wav(to_speech(text), wf, syn_config=VOICE_CFG)
    return buf.getvalue()


def handle(audio_bytes: bytes | None, text: str | None, emit):
    """Full pipeline; `emit(dict)` sends one NDJSON line to the browser."""
    t, t0 = {}, time.time()
    with lock:
        if audio_bytes is not None:
            text = transcribe(decode_audio(audio_bytes))
            t["stt"] = round(time.time() - t0, 2)
        text = normalize_question(text or "")
        emit({"type": "question", "texte": text})
        if not text:
            emit({"type": "phrase", "texte": "Je n'ai rien entendu.", "audio": base64.b64encode(speak("Je n'ai rien entendu.")).decode()})
            return emit({"type": "fin", "timings": t})
        spoken = tools.answer(text)
        if spoken is not None:  # time, date, weather, backups, timer: direct answer, no LLM
            reset_topic()
            wav = speak(spoken, fast=True)
            t["outil"] = True
            t["premier_son"] = t["total"] = round(time.time() - t0, 2)
            emit({"type": "phrase", "texte": spoken, "audio": base64.b64encode(wav).decode()})
            history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": spoken}])
            return emit({"type": "fin", "timings": t})
        query = decide(text)
        sources = []
        if query:
            if ack_wavs:
                emit({"type": "ack", "audio": ack_wavs[int(t0) % len(ack_wavs)]})
                t["accuse"] = round(time.time() - t0, 2)
            t2 = time.time()
            try:
                sources = web_search(query)
            except OSError:  # SearXNG unreachable or too slow
                sources = []
            t["recherche"] = round(time.time() - t2, 2)
            emit({"type": "sources", "requete": query, "sources": sources})
            if not sources:
                # engines suspended (too many requests, CAPTCHA): do not let LFM2 answer from memory, it would make things up
                msg = "Je n'ai rien trouvé de fiable sur Internet pour ça, et je préfère ne pas te répondre de mémoire. Essaie de reformuler, ou réessaie dans quelques minutes."
                emit({"type": "phrase", "texte": msg, "audio": base64.b64encode(speak(msg, fast=True)).decode()})
                t["total"] = round(time.time() - t0, 2)
                return emit({"type": "fin", "timings": t})
        user, raw = user_message(text, sources, query or ""), []
        for s in sentences(raw.append(d) or d for d in stream_answer(user)):
            if not sources:
                s = drop_fake_source(s)
                if not s:
                    continue
            wav = speak(s, fast="premier_son" not in t)
            t.setdefault("premier_son", round(time.time() - t0, 2))
            emit({"type": "phrase", "texte": s, "audio": base64.b64encode(wav).decode()})
        # history identical to the prompt sent and the text generated: otherwise the LFM2 cache no longer matches
        # and it re-reads the whole tail of the conversation (~300 tokens, ~4 s) on the next question
        history.extend([{"role": "user", "content": user}, {"role": "assistant", "content": "".join(raw)}])
        if len(history) > 4 * HISTORY_TURNS:  # trim in blocks: trimming every turn would invalidate the cache each time
            del history[:-2 * HISTORY_TURNS]
        t["total"] = round(time.time() - t0, 2)
        t["serveur"] = backend.last
        emit({"type": "fin", "timings": t})


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Murmure</title>
<style>
body{font-family:system-ui,sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}
button{width:100%;padding:2rem;font-size:1.3rem;border:0;border-radius:1rem;background:#2b6cb0;color:#fff;touch-action:none}
button.rec{background:#c53030} button:disabled{background:#555}
form{display:flex;gap:.5rem;margin-top:1rem} input{flex:1;padding:.8rem;border-radius:.5rem;border:0}
.msg{margin:.6rem 0;padding:.7rem;border-radius:.6rem} .q{background:#2d3748} .a{background:#22543d}
small{color:#aaa} a{color:#9ae6b4}
</style></head><body>
<h1>Murmure</h1>
<button id="talk">Maintenir pour parler</button>
<form id="f"><input id="txt" placeholder="…ou écris ta question" autocomplete="off"><button style="width:auto;padding:.8rem">Envoyer</button></form>
<div id="log"></div>
<script>
const btn=document.getElementById('talk'), log=document.getElementById('log');
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
let rec, chunks=[], queue=[], playing=false;
function add(cls){const d=document.createElement('div');d.className='msg '+cls;log.prepend(d);return d;}
function playNext(){ if(playing||!queue.length) return; playing=true;
  const a=new Audio('data:audio/wav;base64,'+queue.shift()); a.onended=a.onerror=()=>{playing=false;playNext();}; a.play().catch(()=>{playing=false;playNext();}); }
async function send(body, headers){
  btn.disabled=true; btn.textContent='Murmure réfléchit…'; const t0=performance.now();
  let q=null, a=null, txt='', src='', first=null;
  try{
    const r=await fetch('talk',{method:'POST',body,headers});
    if(!r.ok){ const j=await r.json().catch(()=>({})); throw new Error(j.error||r.status); }
    const rd=r.body.getReader(), dec=new TextDecoder(); let buf='';
    for(;;){ const {done,value}=await rd.read(); if(done) break; buf+=dec.decode(value,{stream:true});
      let i; while((i=buf.indexOf('\\n'))>=0){ const line=buf.slice(0,i); buf=buf.slice(i+1); if(!line) continue; const m=JSON.parse(line);
        if(m.type==='question'){ q=add('q'); q.innerHTML=esc(m.texte||'(rien entendu)'); a=add('a'); }
        else if(m.type==='ack'){ queue.push(m.audio); playNext(); }
        else if(m.type==='sources'){ src='<br><small>Recherche : '+esc(m.requete)+'<br>'+m.sources.map((s,k)=>'<a href="'+encodeURI(s.url)+'" target="_blank" rel="noopener">['+(k+1)+'] '+esc(s.titre)+'</a>').join('<br>')+'</small>'; }
        else if(m.type==='phrase'){ if(first===null) first=((performance.now()-t0)/1000).toFixed(1); txt+=(txt?' ':'')+m.texte; a.innerHTML=esc(txt)+src; queue.push(m.audio); playNext(); }
        else if(m.type==='fin'){ const tm=m.timings; a.innerHTML=esc(txt)+src+'<br><small>reconnaissance '+(tm.stt??'-')+' s'+(tm.recherche?' · recherche '+tm.recherche+' s':'')+' · premier son '+first+' s · total '+tm.total+' s</small>'; }
        else if(m.type==='erreur'){ (a||add('a')).innerHTML='Erreur : '+esc(m.message); }
      } }
  }catch(e){ add('a').innerHTML='Erreur : '+esc(e.message); }
  btn.disabled=false; btn.textContent='Maintenir pour parler';
}
btn.onpointerdown=async e=>{
  e.preventDefault();
  try{
    const s=await navigator.mediaDevices.getUserMedia({audio:true});
    chunks=[]; rec=new MediaRecorder(s);
    rec.ondataavailable=ev=>chunks.push(ev.data);
    rec.onstop=()=>{s.getTracks().forEach(t=>t.stop()); const b=new Blob(chunks,{type:rec.mimeType}); if(b.size>0) send(b,{'Content-Type':rec.mimeType});};
    rec.start(); btn.classList.add('rec'); btn.textContent='J’écoute… (relâche pour envoyer)';
  }catch(err){ add('a').innerHTML='Micro inaccessible : '+esc(err.message); }
};
const stop=()=>{ if(rec&&rec.state==='recording'){ rec.stop(); btn.classList.remove('rec'); } };
btn.onpointerup=stop; btn.onpointerleave=stop;
document.getElementById('f').onsubmit=e=>{e.preventDefault(); const t=document.getElementById('txt'); if(t.value.trim()){ send(JSON.stringify({text:t.value}),{'Content-Type':'application/json'}); t.value=''; }};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            data = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "introuvable"})

    def do_POST(self):
        if self.path != "/talk":
            return self._json(404, {"error": "introuvable"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json(400, {"error": "Content-Length invalide"})
        if not 0 < n <= MAX_BODY:
            return self._json(413, {"error": "requête vide ou trop grosse"})
        body = self.rfile.read(n)
        is_text = self.headers.get("Content-Type", "").startswith("application/json")
        try:
            text = str(json.loads(body).get("text", ""))[:2000].strip() if is_text else None
        except (json.JSONDecodeError, AttributeError):
            return self._json(400, {"error": "JSON invalide"})
        # stream without Content-Length (HTTP/1.0, closed at the end)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        wlock, done, last = threading.Lock(), threading.Event(), [time.time()]

        def emit(obj):
            with wlock:
                self.wfile.write((json.dumps(obj) + "\n").encode())
                self.wfile.flush()
                last[0] = time.time()

        def heartbeat():
            # keep-alive line every second of silence: a silent Tailscale tunnel can delay
            # the next line by more than 10 s (measured on 3 November 2026); the page ignores it
            while not done.wait(1):
                if time.time() - last[0] >= 1:
                    try:
                        emit({"type": "attente"})
                    except OSError:
                        return
        threading.Thread(target=heartbeat, daemon=True).start()
        try:
            handle(None if is_text else body, text, emit)
        except BrokenPipeError:
            pass  # page closed mid-response
        except subprocess.CalledProcessError:
            emit({"type": "erreur", "message": "audio illisible"})
        except Exception as e:  # LLM or SearXNG unreachable: tell the page rather than cutting the stream
            emit({"type": "erreur", "message": f"{type(e).__name__}: {e}"})
        finally:
            done.set()

    def log_message(self, fmt, *args):
        pass  # no per-request log (avoids keeping the questions in it)


def warm() -> None:
    """Warms up the fallback server: its cache already holds the system prompt if the others go down."""
    for _ in range(30):
        try:
            request("/v1/chat/completions", {"messages": [system_message(),
                                                          {"role": "user", "content": "Bonjour"}], "max_tokens": 1}, backend.fallback()).read()
            return
        except OSError:
            time.sleep(5)


def main() -> None:
    ack_wavs.extend(base64.b64encode(speak(a, fast=True)).decode() for a in ACKS)
    threading.Thread(target=warm, daemon=True).start()
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    if CERT and CERT_KEY:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT, CERT_KEY)
        # TLS handshake in the request thread, not in the accept loop
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    httpd.serve_forever()

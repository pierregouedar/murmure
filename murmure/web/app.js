"use strict";
// Murmure web page: tap or hold to talk, answers streamed (NDJSON) and spoken sentence by sentence.
const I18N = {
  fr: {
    hello: "Dis quelque chose", idle: "Appuie pour parler", listening: "J'écoute", thinking: "Je réfléchis", speaking: "Je parle",
    placeholder: "Écrire une question", send: "Envoyer", stop: "Stop", mute: "Couper la voix", clear: "Effacer la conversation",
    nothing: "(rien entendu)", error: "Erreur", mic: "Micro inaccessible",
    heard: "reconnaissance", search: "recherche", total: "total", server: "serveur",
    suggestions: ["Quelle heure est-il ?", "Quel temps fait-il demain ?", "Raconte-moi une blague", "Mets un minuteur de dix minutes"],
  },
  en: {
    hello: "Say something", idle: "Tap to talk", listening: "Listening", thinking: "Thinking", speaking: "Speaking",
    placeholder: "Type a question", send: "Send", stop: "Stop", mute: "Mute the voice", clear: "Clear the conversation",
    nothing: "(heard nothing)", error: "Error", mic: "Microphone unavailable",
    heard: "recognition", search: "search", total: "total", server: "server",
    suggestions: ["What time is it?", "What's the weather tomorrow?", "Tell me a joke", "Set a ten minute timer"],
  },
};
const LANG = (navigator.language || "fr").toLowerCase().startsWith("fr") ? "fr" : "en";
const T = I18N[LANG];
document.documentElement.lang = LANG;
const $ = (id) => document.getElementById(id);
for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = T[el.dataset.i18n];
for (const el of document.querySelectorAll("[data-i18n-placeholder]")) el.placeholder = T[el.dataset.i18nPlaceholder];
for (const el of document.querySelectorAll("[data-i18n-title]")) { el.title = T[el.dataset.i18nTitle]; el.setAttribute("aria-label", T[el.dataset.i18nTitle]); }

const log = $("log"), orb = $("orb"), status = $("status"), stopBtn = $("stop"), txt = $("txt");
const SILENCE_MS = 1400, MAX_MS = 30000, VOICE_LEVEL = 0.035, TAP_MS = 280;
let state = "idle", muted = localStorage.getItem("murmure.muted") === "1";
let rec = null, stream = null, ctx = null, raf = 0, controller = null;
let queue = [], current = null, fetching = false;

function setState(s) {
  state = s; document.body.dataset.state = s; document.body.dataset.lvl = 0;
  status.textContent = T[s] || T.idle;
  stopBtn.hidden = !(s === "thinking" || s === "speaking");
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---- conversation: a transcript kept in the browser (last 30 exchanges)
const saved = JSON.parse(localStorage.getItem("murmure.log2") || "[]");
const persist = () => localStorage.setItem("murmure.log2", JSON.stringify(saved.slice(-30)));
function exchange(q) {
  $("empty")?.remove();
  const el = document.createElement("article"); el.className = "ex";
  el.innerHTML = '<p class="q"></p><div class="a"><span class="text"></span></div>';
  el.querySelector(".q").textContent = q;
  log.append(el); log.scrollTop = log.scrollHeight;
  return el;
}
function info(el, { sources = [], detail = "", first = null }) {
  el.querySelector(".info")?.remove();
  const box = document.createElement("div"); box.className = "info";
  for (const s of sources) {
    let host; try { const u = new URL(s.url); if (!/^https?:$/.test(u.protocol)) continue; host = u.hostname.replace(/^www\./, ""); } catch { continue; }
    const a = document.createElement("a"); a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.title = s.titre || host; a.textContent = host; box.append(a);
  }
  if (detail) { const d = document.createElement("details"); d.innerHTML = "<summary>" + esc(first ?? "–") + " s</summary>" + esc(detail); box.append(d); }
  if (box.children.length) el.append(box);
}
for (const m of saved) { const el = exchange(m.q); el.querySelector(".text").textContent = m.a; info(el, m); }
const sg = $("suggestions");
for (const q of T.suggestions) { const li = document.createElement("li"), b = document.createElement("button"); b.type = "button"; b.textContent = q; b.onclick = () => ask(q); li.append(b); sg.append(li); }

// ---- audio playback queue (one WAV per sentence)
function playNext() {
  if (current || !queue.length) { if (!current && !queue.length && !fetching && state === "speaking") setState("idle"); return; }
  if (muted) { queue.length = 0; return playNext(); }
  const a = new Audio("data:audio/wav;base64," + queue.shift());
  current = a; setState("speaking");
  const done = () => { if (current === a) { current = null; playNext(); } };
  a.onended = a.onerror = done; a.play().catch(done);
}
function silence() { queue.length = 0; if (current) { const a = current; current = null; a.pause(); } }
function stopAll() { controller?.abort(); silence(); fetching = false; setState("idle"); }
$("mute").setAttribute("aria-pressed", muted);
$("mute").onclick = () => { muted = !muted; localStorage.setItem("murmure.muted", muted ? "1" : "0"); $("mute").setAttribute("aria-pressed", muted); if (muted) silence(); };
$("clear").onclick = () => { stopAll(); saved.length = 0; persist(); location.reload(); };
stopBtn.onclick = stopAll;

// ---- ask: POST /talk and read the NDJSON stream
async function send(body, headers) {
  controller = new AbortController(); fetching = true; setState("thinking");
  const t0 = performance.now(); let first = null, el = null, out = "", sources = [], query = "", question = "";
  const fail = (msg) => { el ??= exchange(""); el.classList.add("err"); el.querySelector(".a").textContent = T.error + " : " + msg; };
  try {
    const r = await fetch("talk", { method: "POST", body, headers, signal: controller.signal });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || r.status); }
    const rd = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { done, value } = await rd.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1); if (!line) continue;
        const m = JSON.parse(line);
        if (m.type === "question") { question = m.texte || T.nothing; el = exchange(question); }
        else if (m.type === "ack") { queue.push(m.audio); playNext(); }
        else if (m.type === "sources") { sources = m.sources || []; query = m.requete || ""; if (el) info(el, { sources }); }
        else if (m.type === "phrase") {
          first ??= ((performance.now() - t0) / 1000).toFixed(1); el ??= exchange(question);
          out += (out ? " " : "") + m.texte; el.querySelector(".text").textContent = out; log.scrollTop = log.scrollHeight;
          queue.push(m.audio); playNext();
        }
        else if (m.type === "fin") {
          const tm = m.timings || {};
          const detail = [T.heard + " " + (tm.stt ?? "–") + " s", tm.recherche ? T.search + " " + tm.recherche + " s (" + query + ")" : "", T.total + " " + tm.total + " s", tm.serveur ? T.server + " " + tm.serveur : ""].filter(Boolean).join(" · ");
          if (el) info(el, { sources, detail, first });
          saved.push({ q: question, a: out, sources, detail, first });
        }
        else if (m.type === "erreur") fail(m.message);
      }
    }
  } catch (e) { if (e.name !== "AbortError") fail(e.message); }
  fetching = false; persist();
  if (!current && !queue.length) setState("idle"); else playNext();
}
function ask(text) { if (state === "thinking" || state === "speaking") stopAll(); send(JSON.stringify({ text }), { "Content-Type": "application/json" }); }
$("composer").onsubmit = (e) => { e.preventDefault(); const t = txt.value.trim(); if (t) { txt.value = ""; ask(t); } };

// ---- microphone: hold to talk, or tap to start and let silence send; the three arcs follow your voice
async function startListening() {
  if (state === "listening") return;
  if (state === "thinking" || state === "speaking") stopAll();
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch (err) { const el = exchange(""); el.classList.add("err"); el.querySelector(".a").textContent = T.mic + " : " + err.message; return; }
  const chunks = [];
  rec = new MediaRecorder(stream);
  rec.ondataavailable = (ev) => chunks.push(ev.data);
  rec.onstop = () => {
    cancelAnimationFrame(raf); ctx?.close(); ctx = null; stream.getTracks().forEach((t) => t.stop());
    const b = new Blob(chunks, { type: rec.mimeType });
    if (b.size > 0 && state === "listening") send(b, { "Content-Type": rec.mimeType }); else setState("idle");
  };
  ctx = new AudioContext(); const analyser = ctx.createAnalyser(); analyser.fftSize = 512; ctx.createMediaStreamSource(stream).connect(analyser);
  const data = new Uint8Array(analyser.fftSize); const t0 = performance.now(); let spoke = false, quietSince = 0;
  const tick = () => {
    analyser.getByteTimeDomainData(data);
    let sum = 0; for (const v of data) { const x = (v - 128) / 128; sum += x * x; }
    const rms = Math.sqrt(sum / data.length);
    document.body.dataset.lvl = rms > 0.12 ? 3 : rms > 0.06 ? 2 : rms > 0.02 ? 1 : 0;
    const now = performance.now();
    if (rms > VOICE_LEVEL) { spoke = true; quietSince = 0; } else if (spoke && !quietSince) quietSince = now;
    if (tapMode && ((spoke && quietSince && now - quietSince > SILENCE_MS) || now - t0 > MAX_MS)) return stopListening();
    raf = requestAnimationFrame(tick);
  };
  rec.start(); setState("listening"); raf = requestAnimationFrame(tick);
}
function stopListening() { if (rec && rec.state === "recording") rec.stop(); }
function cancelListening() { if (rec && rec.state === "recording") { setState("idle"); rec.stop(); } }

let pressedAt = 0, tapMode = false, holding = false;
orb.onpointerdown = async (e) => {
  e.preventDefault();
  if (state === "listening") { stopListening(); return; }         // second tap: send now
  pressedAt = performance.now(); holding = true; tapMode = false;
  await startListening();
};
const release = () => {
  if (!holding) return; holding = false;
  if (state !== "listening") return;
  if (performance.now() - pressedAt < TAP_MS) tapMode = true;      // short press: keep listening until silence
  else stopListening();                                            // long press: release sends
};
orb.onpointerup = release; orb.onpointerleave = release; orb.onpointercancel = release;
// keyboard: hold the space bar (outside the text box) to talk, Escape cancels
addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.repeat && document.activeElement !== txt && (document.activeElement?.tagName !== "BUTTON" || document.activeElement === orb)) { e.preventDefault(); pressedAt = performance.now(); holding = true; tapMode = false; startListening(); }
  else if (e.code === "Escape") { state === "listening" ? cancelListening() : stopAll(); }
});
addEventListener("keyup", (e) => { if (e.code === "Space" && holding) release(); });
setState("idle");

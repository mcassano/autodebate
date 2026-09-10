/* autodebate café — WebSocket client. Renders the table, streams the talk. */

const chat = document.getElementById("chat");
const statusEl = document.getElementById("status");
const tokensEl = document.getElementById("tokens");
const connEl = document.getElementById("conn");
const queuedEl = document.getElementById("queued");
const seatsEl = document.getElementById("seats");
const pauseBtn = document.getElementById("pause");
const form = document.getElementById("speak");
const noteInput = document.getElementById("note");

let ws = null;
let currentBubble = null;   // the bubble currently streaming
let currentBody = null;
let backoff = 1000;
const colors = { You: "#8fbc8f" };   // speaker → color, filled from init

function scrollDown() {
  chat.scrollTop = chat.scrollHeight;
}

function fmtTokens(n) {
  return n.toLocaleString() + " tokens";
}

function addBubble(name, color, text) {
  const b = document.createElement("div");
  b.className = "bubble";
  b.style.borderLeftColor = color;
  const who = document.createElement("span");
  who.className = "who";
  who.style.color = color;
  who.textContent = name;
  const body = document.createElement("span");
  body.className = "body";
  body.textContent = text;
  b.append(who, body);
  chat.appendChild(b);
  scrollDown();
  return body;
}

function addNote(text, isError) {
  const n = document.createElement("div");
  n.className = "note" + (isError ? " error" : "");
  n.textContent = text;
  // mid-turn (tool calls): these happened before the speech they feed,
  // so they render above the in-progress bubble
  if (currentBubble) chat.insertBefore(n, currentBubble);
  else chat.appendChild(n);
  scrollDown();
}

function renderSeats(personas) {
  seatsEl.innerHTML = "";
  for (const p of personas) {
    const seat = document.createElement("div");
    seat.className = "seat";
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = p.color;
    const label = document.createElement("span");
    label.innerHTML = `<b style="color:${p.color}">${p.name}</b> · ${p.style} ` +
                      `<span class="model">${p.model}</span>`;
    seat.append(dot, label);
    seatsEl.appendChild(seat);
  }
}

function handle(ev) {
  switch (ev.type) {
    case "init":
      renderSeats(ev.personas);
      for (const p of ev.personas) colors[p.name] = p.color;
      chat.innerHTML = "";
      for (const e of ev.transcript) addBubble(e.speaker, colors[e.speaker] || "#c98a4b", e.text);
      tokensEl.textContent = fmtTokens(ev.tokens);
      statusEl.textContent = ev.paused ? "paused — the table waits for you" : "listening…";
      break;
    case "status":
      statusEl.textContent = ev.text;
      tokensEl.textContent = fmtTokens(ev.tokens);
      queuedEl.textContent = ev.queued ? `${ev.queued} note(s) queued` : "";
      break;
    case "speaker_start": {
      currentBody = addBubble(ev.name, ev.color, "");
      currentBubble = currentBody.parentElement;
      currentBubble.classList.add("thinking");
      break;
    }
    case "token":
      if (currentBody) {
        currentBody.textContent += ev.text;
        scrollDown();
      }
      break;
    case "speaker_end":
      if (currentBubble) {
        if (ev.text && ev.text.trim()) {
          currentBody.textContent = ev.text.trim();
        } else if (!currentBody.textContent.trim()) {
          currentBubble.remove();  // a pass leaves no empty cup
        }
        currentBubble.classList.remove("thinking");
        currentBubble = null;
        currentBody = null;
      }
      break;
    case "dim":
      addNote(ev.text, false);
      break;
    case "user":
      addBubble("You", "#8fbc8f", ev.text);
      break;
    case "error":
      addNote("⚠ " + ev.text, true);
      break;
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    backoff = 1000;
    connEl.textContent = "in the café";
    connEl.classList.add("live");
  };
  ws.onmessage = (m) => handle(JSON.parse(m.data));
  ws.onclose = () => {
    connEl.textContent = "reconnecting…";
    connEl.classList.remove("live");
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 8000);
  };
}

form.onsubmit = (e) => {
  e.preventDefault();
  const text = noteInput.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "speak", text }));
  noteInput.value = "";
};

pauseBtn.onclick = () => {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "pause" }));
};

connect();

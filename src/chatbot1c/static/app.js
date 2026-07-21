(() => {
  "use strict";

  const api = "/api/v1";
  const messages = document.querySelector("#messages");
  const composer = document.querySelector("#composer");
  const input = document.querySelector("#message");
  const progress = document.querySelector("#progress");
  const contextLabel = document.querySelector("#context-version");
  const title = document.querySelector("#chat-title");
  let sessionId = null;
  let contextVersion = 1;

  async function json(method, path, body) {
    const response = await fetch(`${api}${path}`, {
      method,
      headers: body === undefined ? {} : {"Content-Type": "application/json"},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = response.status === 204 ? {} : await response.json();
    if (!response.ok) throw new Error(value.error?.message || "Ошибка запроса");
    return value;
  }

  function appendMessage(role, text, createdAt, citations = []) {
    const article = document.createElement("article");
    article.className = `message ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    article.appendChild(bubble);
    if (citations.length) {
      const links = document.createElement("div");
      links.className = "citations";
      citations.forEach((citation) => {
        const link = document.createElement("a");
        link.href = citation.source_uri;
        link.textContent = citation.title;
        links.appendChild(link);
      });
      article.appendChild(links);
    }
    const time = document.createElement("time");
    time.textContent = new Date(createdAt).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"});
    article.appendChild(time);
    messages.appendChild(article);
    messages.scrollTop = messages.scrollHeight;
  }

  async function createSession() {
    const session = await json("POST", "/sessions", {});
    sessionId = session.session_id;
    contextVersion = session.context_version;
    contextLabel.textContent = `Контекст ${contextVersion}`;
    title.textContent = "Новый диалог";
    messages.replaceChildren();
  }

  async function loadSession(id) {
    const session = await json("GET", `/sessions/${id}`);
    sessionId = id;
    contextVersion = session.context_version;
    contextLabel.textContent = `Контекст ${contextVersion}`;
    title.textContent = session.title;
    messages.replaceChildren();
    session.messages.forEach((message) => appendMessage(message.role, message.text, message.created_at, message.citations || []));
  }

  function watchTurn(turnId) {
    const stream = new EventSource(`${api}/turns/${turnId}/events`);
    stream.onmessage = (event) => {
      const value = JSON.parse(event.data);
      progress.textContent = value.stage.replaceAll(".", " · ");
      if (["request.completed", "request.failed"].includes(value.stage)) {
        stream.close();
        refreshTurn(turnId);
      }
    };
    stream.onerror = () => {
      stream.close();
      refreshTurn(turnId);
    };
  }

  async function refreshTurn(turnId) {
    const turn = await json("GET", `/turns/${turnId}`);
    if (!["completed", "failed", "interrupted"].includes(turn.status)) {
      window.setTimeout(() => refreshTurn(turnId), 250);
      return;
    }
    appendMessage("assistant", turn.assistant_message.text, turn.completed_at || new Date().toISOString(), turn.assistant_message.citations);
    progress.textContent = "";
    const session = await json("GET", `/sessions/${sessionId}`);
    contextVersion = session.context_version;
    contextLabel.textContent = `Контекст ${contextVersion}`;
  }

  composer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    if (!sessionId) await createSession();
    appendMessage("user", text, new Date().toISOString());
    input.value = "";
    progress.textContent = "Принято";
    try {
      const turn = await json("POST", `/sessions/${sessionId}/messages`, {
        text,
        client_message_id: crypto.randomUUID(),
        expected_context_version: contextVersion,
      });
      watchTurn(turn.turn_id);
    } catch (error) {
      progress.textContent = error.message;
    }
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });

  document.querySelector("#new-session").addEventListener("click", createSession);
  document.querySelectorAll(".session-link").forEach((button) => {
    button.addEventListener("click", () => loadSession(button.dataset.sessionId));
  });

  const firstSession = document.querySelector(".session-link");
  if (firstSession) loadSession(firstSession.dataset.sessionId);
  else createSession();
})();

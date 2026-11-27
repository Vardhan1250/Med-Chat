const sessionId = "med-session-" + Math.random().toString(36).substring(2, 10);
let selectedScannerFile = null;

// Simple safe Markdown-to-HTML formatter
function formatMarkdown(text) {
  if (!text) return "";
  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  html = html.replace(/^### (.*$)/gim, "<h4>$1</h4>");
  html = html.replace(/^## (.*$)/gim, "<h3>$1</h3>");
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");
  html = html.replace(/^\s*[-*]\s+(.*)$/gim, "<li>$1</li>");
  html = html.replace(/(<li>.*<\/li>)/s, "<ul>$1</ul>");
  html = html.replace(/\n{2,}/g, "</p><p>");
  html = html.replace(/\n/g, "<br>");
  return `<p>${html}</p>`;
}

function switchView(viewName) {
  document.querySelectorAll(".view-section").forEach((sec) => {
    sec.classList.remove("active");
  });
  document.querySelectorAll(".nav-link").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === viewName);
  });

  const target = document.getElementById(`view-${viewName}`);
  if (target) target.classList.add("active");

  if (viewName === "about") {
    loadApiStatus();
  }
}

function quickAsk(disease) {
  switchView("chat");
  sendChatQuery(disease);
}

function resetChat() {
  const container = document.getElementById("chatMessages");
  container.innerHTML = `
    <div class="message-row bot">
      <div class="avatar bot-avatar">+</div>
      <div class="bubble bot-bubble">
        <p>New clinical session started. Say <strong>"Hi"</strong>, type a <strong>disease name</strong>, or upload a prescription image/PDF.</p>
      </div>
    </div>
  `;
  document.getElementById("evidenceList").innerHTML =
    '<p class="empty-evidence">Ask about any disease or medication to view verified sources here.</p>';
  document.getElementById("sourceCount").textContent = "0";
}

function toggleEvidenceDrawer() {
  document.getElementById("evidenceDrawer").classList.toggle("open");
}

function appendMessage(role, htmlContent, followUps = []) {
  const container = document.getElementById("chatMessages");
  const row = document.createElement("div");
  row.className = `message-row ${role}`;

  const avatar = document.createElement("div");
  avatar.className = `avatar ${role}-avatar`;
  avatar.textContent = role === "bot" ? "+" : "U";

  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}-bubble`;
  bubble.innerHTML = htmlContent;

  if (role === "bot" && followUps.length > 0) {
    const chipsDiv = document.createElement("div");
    chipsDiv.className = "inline-followups";
    followUps.forEach((q) => {
      const btn = document.createElement("button");
      btn.className = "followup-chip";
      btn.textContent = q;
      btn.onclick = () => sendChatQuery(q);
      chipsDiv.appendChild(btn);
    });
    bubble.appendChild(chipsDiv);
  }

  row.appendChild(avatar);
  row.appendChild(bubble);
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
  return row;
}

function renderSources(sources) {
  const list = document.getElementById("evidenceList");
  const countEl = document.getElementById("sourceCount");
  if (!sources || sources.length === 0) return;

  countEl.textContent = String(sources.length);
  list.innerHTML = "";

  sources.forEach((src) => {
    const card = document.createElement("div");
    card.className = "evidence-card";
    const title = src.source || "Biomedical Registry";
    const detail =
      src.brand_name ||
      src.condition ||
      src.query ||
      (src.topics && src.topics[0] && src.topics[0].title) ||
      "Verified clinical record";
    const url =
      src.url ||
      (src.trials && src.trials[0] && src.trials[0].url) ||
      (src.topics && src.topics[0] && src.topics[0].url) ||
      "";

    card.innerHTML = `
      <h4>${title}</h4>
      <p>${detail}</p>
      ${url ? `<a href="${url}" target="_blank" rel="noopener">View Official Record &rarr;</a>` : ""}
    `;
    list.appendChild(card);
  });
}

async function handleChatSubmit(event) {
  event.preventDefault();
  const input = document.getElementById("chatInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  await sendChatQuery(text);
}

async function sendChatQuery(queryText) {
  appendMessage("user", `<p>${queryText}</p>`);
  const loadingRow = appendMessage("bot", "<p><em>Thinking & checking clinical sources...</em></p>");

  try {
    const resp = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: queryText, session_id: sessionId }),
    });
    const data = await resp.json();
    loadingRow.remove();

    if (!resp.ok) {
      appendMessage("bot", `<p><strong>Error:</strong> ${data.detail || "Unable to fetch response."}</p>`);
      return;
    }

    appendMessage("bot", formatMarkdown(data.reply), data.follow_ups || []);
    renderSources(data.sources || []);
  } catch (err) {
    loadingRow.remove();
    appendMessage("bot", `<p><strong>Network Error:</strong> ${err.message}</p>`);
  }
}

// Inline attachment inside Chat Bar
async function handleInlineFileUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  appendMessage("user", `<p>📎 Uploaded prescription/document: <strong>${file.name}</strong></p>`);
  const loadingRow = appendMessage("bot", "<p><em>Scanning document to detect condition & list all tablets...</em></p>");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const resp = await fetch("/analyze-document", {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();
    loadingRow.remove();

    if (!resp.ok) {
      appendMessage("bot", `<p><strong>Scan Error:</strong> ${data.detail || "Could not analyze file."}</p>`);
      return;
    }

    appendMessage("bot", formatMarkdown(data.analysis), data.follow_ups || []);
  } catch (err) {
    loadingRow.remove();
    appendMessage("bot", `<p><strong>Upload Error:</strong> ${err.message}</p>`);
  } finally {
    event.target.value = "";
  }
}

// Hero CTA direct upload -> jumps to Scanner view
function triggerHeroUpload() {
  const heroInput = document.getElementById("heroFileInput");
  heroInput.onchange = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    switchView("scanner");
    setScannerFile(file);
  };
  heroInput.click();
}

function handleScannerFileSelect(event) {
  const file = event.target.files[0];
  if (file) setScannerFile(file);
}

function setScannerFile(file) {
  selectedScannerFile = file;
  document.getElementById("uploadFileName").textContent = `Selected: ${file.name}`;
  const preview = document.getElementById("imagePreview");
  if (file.type.startsWith("image/")) {
    preview.src = URL.createObjectURL(file);
    preview.classList.remove("hidden");
  } else {
    preview.classList.add("hidden");
  }
}

async function submitDocumentAnalysis() {
  if (!selectedScannerFile) {
    alert("Please select a prescription image or PDF first.");
    return;
  }

  const btn = document.getElementById("analyzeDocBtn");
  const notes = document.getElementById("scannerNotes").value;
  const resultCard = document.getElementById("scannerResultCard");
  const resultBody = document.getElementById("scannerResultBody");

  btn.disabled = true;
  btn.textContent = "Scanning Prescription & Extracting Tablets...";

  const formData = new FormData();
  formData.append("file", selectedScannerFile);
  formData.append("notes", notes);

  try {
    const resp = await fetch("/analyze-document", {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();
    if (!resp.ok) {
      alert(data.detail || "Document analysis failed.");
      return;
    }

    resultBody.innerHTML = formatMarkdown(data.analysis);
    resultCard.classList.remove("hidden");
    resultCard.scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    alert("Error analyzing document: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Identify Disease & List Tablets";
  }
}

// Load About & API Stack status
async function loadApiStatus() {
  const container = document.getElementById("apiStatusContainer");
  try {
    const resp = await fetch("/api/status");
    const data = await resp.json();

    let html = "";
    (data.env_keys || []).forEach((k) => {
      html += `
        <div class="api-item">
          <div class="api-item-top">
            <span>🔑 Environment Key: ${k.name}</span>
            <span class="api-badge">${k.configured ? "Configured (" + k.preview + ")" : "Missing"}</span>
          </div>
          <div class="api-meta">${k.provider}</div>
        </div>
      `;
    });

    (data.integrations || []).forEach((api) => {
      html += `
        <div class="api-item">
          <div class="api-item-top">
            <span>${api.name}</span>
            <span class="api-badge">${api.status}</span>
          </div>
          <div class="api-meta">${api.type} &bull; <em>${api.auth}</em></div>
        </div>
      `;
    });

    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<p>Unable to load API status: ${err.message}</p>`;
  }
}
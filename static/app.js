const feed = document.getElementById("feed");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const evidenceList = document.getElementById("evidence-list");
const evidenceCount = document.getElementById("evidence-count");

let sessionId = "research_" + Math.random().toString(36).substring(2, 10);

function startNewSession() {
  sessionId = "research_" + Math.random().toString(36).substring(2, 10);
  feed.innerHTML = `
    <div class="welcome-card">
      <h2>New Clinical Research Thread</h2>
      <p>Ask about medications, live recruiting clinical trials, or medical conditions.</p>
    </div>
  `;
  evidenceList.innerHTML = `<div class="empty-evidence">Ask a question to inspect live FDA labels, clinical trials, and MedlinePlus sources.</div>`;
  evidenceCount.textContent = "0 sources";
}

function renderEvidence(evidenceItems) {
  if (!evidenceItems || evidenceItems.length === 0) return;
  evidenceList.innerHTML = "";
  let totalCards = 0;

  evidenceItems.forEach((item) => {
    if (item.indications || item.warnings) {
      totalCards++;
      const card = document.createElement("div");
      card.className = "evidence-card";
      card.innerHTML = `
        <span class="evidence-source-tag">${item.source || "openFDA"}</span>
        <h4>${item.title || "FDA Drug Monograph"}</h4>
        <p><strong>Warnings:</strong> ${(item.warnings || "").slice(0, 220)}...</p>
        <p><strong>Indications:</strong> ${(item.indications || "").slice(0, 180)}...</p>
        ${item.url ? `<a href="${item.url}" target="_blank">View Official DailyMed Label &nearr;</a>` : ""}
      `;
      evidenceList.appendChild(card);
    }

    if (item.trials && Array.isArray(item.trials)) {
      item.trials.forEach((trial) => {
        totalCards++;
        const card = document.createElement("div");
        card.className = "evidence-card";
        card.innerHTML = `
          <span class="evidence-source-tag">ClinicalTrials.gov • ${trial.nct_id}</span>
          <h4>${trial.title}</h4>
          <p>${trial.summary}...</p>
          <a href="${trial.url}" target="_blank">Open Study Record (${trial.nct_id}) &nearr;</a>
        `;
        evidenceList.appendChild(card);
      });
    }

    if (item.results && Array.isArray(item.results)) {
      item.results.forEach((topic) => {
        totalCards++;
        const card = document.createElement("div");
        card.className = "evidence-card";
        card.innerHTML = `
          <span class="evidence-source-tag">${item.source || "MedlinePlus"}</span>
          <h4>${topic.title}</h4>
          <p>${topic.summary.slice(0, 260)}...</p>
          <a href="${topic.url}" target="_blank">Read Full MedlinePlus Topic &nearr;</a>
        `;
        evidenceList.appendChild(card);
      });
    }
  });

  evidenceCount.textContent = `${totalCards} live source${totalCards === 1 ? "" : "s"}`;
}

async function askQuestion(questionText) {
  const welcome = feed.querySelector(".welcome-card");
  if (welcome) welcome.remove();

  const userHeading = document.createElement("div");
  userHeading.className = "turn-user";
  userHeading.textContent = questionText;
  feed.appendChild(userHeading);

  const aiCard = document.createElement("div");
  aiCard.className = "turn-ai";
  aiCard.innerHTML = "<em>Querying live openFDA, ClinicalTrials.gov, and MedlinePlus databases...</em>";
  feed.appendChild(aiCard);
  feed.scrollTop = feed.scrollHeight;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: questionText, session_id: sessionId }),
    });
    const data = await res.json();

    aiCard.innerHTML = marked.parse(data.reply || "No response returned.");

    if (data.followups && data.followups.length > 0) {
      const chipRow = document.createElement("div");
      chipRow.className = "followup-chips";
      data.followups.forEach((fq) => {
        const btn = document.createElement("button");
        btn.className = "followup-chip";
        btn.textContent = "+ " + fq;
        btn.onclick = () => askQuestion(fq);
        chipRow.appendChild(btn);
      });
      aiCard.appendChild(chipRow);
    }

    renderEvidence(data.evidence);
    feed.scrollTop = feed.scrollHeight;
  } catch (err) {
    aiCard.innerHTML = "<strong>Connection error:</strong> Make sure your FastAPI server is running on port 8000.";
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = chatInput.value.trim();
  if (!q) return;
  chatInput.value = "";
  askQuestion(q);
});

function runPreset(promptText) {
  askQuestion(promptText);
}
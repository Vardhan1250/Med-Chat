import os
import json
import base64
import xml.etree.ElementTree as ET
from typing import List, Dict, Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

load_dotenv()

app = FastAPI(title="MedEvidence Clinical AI Workspace", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Active 2026 free-tier Gemini models (each maintains a separate daily quota pool)
MODEL_CANDIDATES = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-flash-latest",
]

GREETING_WORDS = {
    "hi", "hello", "hey", "hii", "hiii", "yo", "hola", "namaste",
    "good morning", "good afternoon", "good evening", "how are you",
    "who are you", "what can you do", "help", "thanks", "thank you"
}


# ---------------------------------------------------------
# Biomedical Tools (openFDA, ClinicalTrials.gov, MedlinePlus)
# ---------------------------------------------------------
@tool
def search_openfda_drug(drug_name: str) -> str:
    """Search official openFDA drug labeling for indications, boxed warnings, adverse reactions, and drug interactions."""
    try:
        url = "https://api.fda.gov/drug/label.json"
        params = {
            "search": f'openfda.brand_name:"{drug_name}" OR openfda.generic_name:"{drug_name}"',
            "limit": 1,
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return json.dumps({"source": "openFDA", "drug": drug_name, "status": "No FDA label record found."})
        data = resp.json().get("results", [{}])[0]
        openfda = data.get("openfda", {})
        result = {
            "source": "openFDA Drug Labeling API",
            "brand_name": openfda.get("brand_name", [drug_name])[0],
            "generic_name": openfda.get("generic_name", ["N/A"])[0],
            "indications_and_usage": (data.get("indications_and_usage") or ["Not specified"])[0][:700],
            "boxed_warning": (data.get("boxed_warning") or ["None listed"])[0][:500],
            "adverse_reactions": (data.get("adverse_reactions") or ["Not specified"])[0][:600],
            "drug_interactions": (data.get("drug_interactions") or ["Not specified"])[0][:500],
            "url": f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?query={drug_name}"
        }
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"source": "openFDA", "error": str(e)})


@tool
def search_clinical_trials(condition: str) -> str:
    """Search active and recruiting clinical trials on ClinicalTrials.gov v2 for a given condition or treatment."""
    try:
        url = "https://clinicaltrials.gov/api/v2/studies"
        params = {
            "query.cond": condition,
            "filter.overallStatus": "RECRUITING",
            "pageSize": 3,
            "format": "json",
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return json.dumps({"source": "ClinicalTrials.gov", "status": "No active trials found."})
        studies = resp.json().get("studies", [])
        parsed = []
        for s in studies:
            proto = s.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            nct_id = ident.get("nctId", "")
            parsed.append({
                "nct_id": nct_id,
                "title": ident.get("briefTitle", "Untitled Study"),
                "status": status_mod.get("overallStatus", "RECRUITING"),
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else "https://clinicaltrials.gov"
            })
        return json.dumps({"source": "ClinicalTrials.gov v2", "condition": condition, "trials": parsed})
    except Exception as e:
        return json.dumps({"source": "ClinicalTrials.gov", "error": str(e)})


@tool
def search_medlineplus_topic(topic: str) -> str:
    """Fetch verified disease overview, symptoms, and management guidelines from NLM MedlinePlus."""
    try:
        url = "https://wsearch.nlm.nih.gov/ws/query"
        params = {"db": "healthTopics", "term": topic, "retmax": 2}
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return json.dumps({"source": "NLM MedlinePlus", "status": "No topic found."})
        root = ET.fromstring(resp.text)
        docs = []
        for doc in root.findall(".//document"):
            title = ""
            summary = ""
            doc_url = doc.attrib.get("url", "https://medlineplus.gov")
            for content in doc.findall("content"):
                name = content.attrib.get("name", "")
                text = "".join(content.itertext()).strip()
                if name == "title":
                    title = text
                elif name == "FullSummary":
                    summary = text[:900]
            if title or summary:
                docs.append({"title": title, "summary": summary, "url": doc_url})
        return json.dumps({"source": "NLM MedlinePlus Health Topics", "query": topic, "topics": docs})
    except Exception as e:
        return json.dumps({"source": "NLM MedlinePlus", "error": str(e)})


TOOLS = [search_openfda_drug, search_clinical_trials, search_medlineplus_topic]
MEMORY = MemorySaver()

SYSTEM_PROMPT = """You are MedEvidence, a warm, intelligent, and modern Clinical AI Assistant.

BEHAVIOR RULES:
1. Natural Conversation & Greetings: If the user says "Hi", "Hello", "Hey", asks who you are, or makes casual conversation, respond warmly and naturally like a helpful modern chatbot. Do NOT call medical search tools for simple greetings.
2. Single-Word Disease or Drug Queries: Users often have limited time and type only a disease or drug name (e.g., "Asthma", "Dengue", "Metformin"). Automatically treat single-word or short medical terms as a request for a complete, structured clinical briefing:
   - **Overview & Key Symptoms** (grounded via `search_medlineplus_topic`)
   - **Standard Medications & Safety Notes** (grounded via `search_openfda_drug` when a drug is relevant)
   - **Active Clinical Research** (grounded via `search_clinical_trials`)
3. Formatting: Use clean Markdown headings, bullet points, and bold text for scannability.
4. Follow-Up Prompts: At the very end of clinical responses, add a line `FOLLOW_UPS:` followed by 3 short JSON array questions, e.g.:
   FOLLOW_UPS: ["What are the warning signs?", "Commonly prescribed medications?", "Are there active clinical trials?"]
5. Always include a brief note that information is for educational and clinical research purposes.
"""


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default-session"


def get_llm(model_name: str):
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is missing in environment variables.")
    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=api_key,
        temperature=0.3,
    )


def extract_follow_ups(text: str):
    follow_ups = []
    clean_text = text
    if "FOLLOW_UPS:" in text:
        parts = text.split("FOLLOW_UPS:", 1)
        clean_text = parts[0].strip()
        raw_json = parts[1].strip()
        try:
            start = raw_json.find("[")
            end = raw_json.rfind("]") + 1
            if start != -1 and end > start:
                follow_ups = json.loads(raw_json[start:end])
        except Exception:
            follow_ups = []
    return clean_text, follow_ups[:3]


def build_direct_evidence_fallback(query_text: str) -> Dict[str, Any]:
    """Direct federal registry synthesis when Gemini free-tier daily quota is temporarily exhausted."""
    sources: List[Dict[str, Any]] = []
    sections: List[str] = [
        f"### Clinical Evidence Brief: {query_text.title()}",
        "*Synthesized directly from live NLM MedlinePlus, openFDA, and ClinicalTrials.gov registries.*"
    ]

    try:
        med_raw = json.loads(search_medlineplus_topic.invoke({"topic": query_text}))
        topics = med_raw.get("topics", [])
        if topics:
            sources.append(med_raw)
            sections.append(
                f"**Overview & Clinical Summary ({topics[0].get('title', query_text)}):**\n{topics[0].get('summary', '')}"
            )
    except Exception:
        pass

    try:
        fda_raw = json.loads(search_openfda_drug.invoke({"drug_name": query_text}))
        if fda_raw.get("indications_and_usage"):
            sources.append(fda_raw)
            sections.append(
                f"**FDA Drug Labeling ({fda_raw.get('brand_name', query_text)}):**\n"
                f"- **Indications:** {fda_raw.get('indications_and_usage')[:400]}...\n"
                f"- **Boxed Warning:** {fda_raw.get('boxed_warning')[:300]}"
            )
    except Exception:
        pass

    try:
        trials_raw = json.loads(search_clinical_trials.invoke({"condition": query_text}))
        trials = trials_raw.get("trials", [])
        if trials:
            sources.append(trials_raw)
            trial_lines = "\n".join(
                f"- **{t.get('nct_id')}**: {t.get('title')} (*{t.get('status')}*)"
                for t in trials
            )
            sections.append(f"**Active Recruiting Clinical Trials:**\n{trial_lines}")
    except Exception:
        pass

    if len(sources) == 0:
        sections.append(
            "No exact registry match was returned for that phrase. Try searching a standard condition or generic medication name (e.g., *Asthma*, *Diabetes*, *Metformin*)."
        )

    sections.append(
        "*Disclaimer: For educational and clinical research reference only. Consult a healthcare professional for medical advice.*"
    )

    return {
        "reply": "\n\n".join(sections),
        "sources": sources,
        "follow_ups": [
            f"What medications treat {query_text}?",
            f"Clinical trials for {query_text}",
            "Hi"
        ],
        "model_used": "direct-biomedical-fallback",
    }


@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")


@app.get("/api/status")
async def api_status():
    """Returns safe status metadata about configured API keys and biomedical integrations."""
    google_key = os.getenv("GOOGLE_API_KEY", "")
    masked_key = f"{google_key[:6]}••••••••{google_key[-4:]}" if len(google_key) > 10 else "Not Configured"
    return {
        "app_name": "MedEvidence Clinical AI Workspace",
        "version": "2.1.0",
        "env_keys": [
            {
                "name": "GOOGLE_API_KEY",
                "provider": "Google Gemini AI (LLM + Multimodal Prescription Vision)",
                "configured": bool(google_key),
                "preview": masked_key,
            }
        ],
        "integrations": [
            {
                "name": "Google Gemini 3.5 Flash-Lite / 3.8 Flash",
                "type": "Primary Reasoning & Vision Engine (with Direct Registry Fallback)",
                "auth": "GOOGLE_API_KEY (Environment Secret)",
                "status": "Active" if google_key else "Missing Key",
            },
            {
                "name": "openFDA Drug Labeling API",
                "type": "Official FDA Warnings, Indications & Interactions",
                "auth": "Public Federal REST API (No Key Required)",
                "status": "Connected",
            },
            {
                "name": "ClinicalTrials.gov API v2",
                "type": "Live Recruiting Clinical Studies Registry",
                "auth": "Public NIH REST API (No Key Required)",
                "status": "Connected",
            },
            {
                "name": "NLM MedlinePlus XML API",
                "type": "Verified Disease & Symptom Guidelines",
                "auth": "Public National Library of Medicine API",
                "status": "Connected",
            },
        ],
    }


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    normalized = user_msg.lower().rstrip("!.?,")
    if normalized in GREETING_WORDS:
        return {
            "reply": (
                "Hello! 👋 I'm **MedEvidence**, your Clinical AI Assistant.\n\n"
                "You can interact with me in whatever way saves you the most time:\n"
                "- **Type just a disease or drug name** (e.g., *Asthma*, *Dengue*, *Metformin*) for an instant clinical summary.\n"
                "- **Upload a prescription image or medical PDF** using the attachment button to detect the condition and list every prescribed tablet.\n"
                "- **Ask any clinical question** backed by live openFDA, MedlinePlus, and ClinicalTrials.gov data."
            ),
            "sources": [],
            "follow_ups": [
                "Asthma",
                "Type 2 Diabetes",
                "Metformin side effects"
            ],
        }

    for model_name in MODEL_CANDIDATES:
        try:
            llm = get_llm(model_name)
            agent = create_react_agent(llm, TOOLS, checkpointer=MEMORY)
            config = {"configurable": {"thread_id": req.session_id}}

            result = agent.invoke(
                {
                    "messages": [
                        SystemMessage(content=SYSTEM_PROMPT),
                        HumanMessage(content=user_msg),
                    ]
                },
                config=config,
            )

            messages = result.get("messages", [])
            final_text = ""
            sources: List[Dict[str, Any]] = []

            for msg in messages:
                if getattr(msg, "type", "") == "tool":
                    try:
                        parsed = json.loads(msg.content)
                        sources.append(parsed)
                    except Exception:
                        sources.append({"source": "Clinical Tool", "raw": str(msg.content)[:400]})

            for msg in reversed(messages):
                if getattr(msg, "type", "") == "ai" and msg.content:
                    if isinstance(msg.content, list):
                        final_text = " ".join(
                            part.get("text", "") for part in msg.content if isinstance(part, dict)
                        )
                    else:
                        final_text = str(msg.content)
                    break

            clean_reply, follow_ups = extract_follow_ups(final_text)
            if not follow_ups:
                follow_ups = [
                    f"What are common medications for {user_msg}?",
                    f"Are there active clinical trials for {user_msg}?",
                    f"What are the warning signs of {user_msg}?"
                ]

            return {
                "reply": clean_reply,
                "sources": sources[-4:],
                "follow_ups": follow_ups,
                "model_used": model_name,
            }
        except Exception:
            continue

    # Automatic zero-downtime fallback using live NLM MedlinePlus, openFDA & ClinicalTrials.gov APIs
    return build_direct_evidence_fallback(user_msg)


@app.post("/analyze-document")
async def analyze_document(
    file: UploadFile = File(...),
    notes: str = Form(default="")
):
    """Analyzes an uploaded prescription image or medical PDF to identify the disease and list all tablets."""
    allowed_types = {
        "image/jpeg", "image/jpg", "image/png", "image/webp", "application/pdf"
    }
    content_type = file.content_type or ""
    if content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload a JPG, PNG, WEBP image, or a PDF document."
        )

    file_bytes = await file.read()
    if len(file_bytes) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds 12 MB limit.")

    b64_data = base64.b64encode(file_bytes).decode("utf-8")

    vision_prompt = f"""You are a clinical prescription and medical document analyzer.
Carefully inspect this uploaded prescription image or medical PDF ({file.filename}).
User notes: {notes if notes else "None"}

Extract and present the findings clearly in Markdown with these exact sections:
1. **Detected Condition / Probable Disease:** Identify the diagnosed disease, clinical indication, or what condition these medicines treat (if diagnosis isn't explicitly written, infer the primary condition from the prescribed medications and state clearly that it is inferred).
2. **Prescribed Tablets & Medications:** List every tablet, capsule, syrup, or medicine visible in a clean bullet list or table with:
   - **Tablet / Medicine Name** (and generic ingredient if known)
   - **Strength / Dosage** (e.g., 500 mg)
   - **Frequency & Timing** (e.g., 1-0-1 after food, twice daily)
   - **What It Treats** (simple explanation)
3. **Important Safety & Food/Drug Notes:** Highlight key precautions (e.g., take after meals, drowsiness, finish antibiotic course).
4. Add a brief disclaimer reminding the patient to confirm dosages with their prescribing doctor or pharmacist.
"""

    last_error = None
    for model_name in MODEL_CANDIDATES:
        try:
            llm = get_llm(model_name)
            message = HumanMessage(
                content=[
                    {"type": "text", "text": vision_prompt},
                    {
                        "type": "media",
                        "mime_type": content_type,
                        "data": b64_data,
                    },
                ]
            )
            response = llm.invoke([message])
            analysis_text = response.content if isinstance(response.content, str) else str(response.content)

            return {
                "filename": file.filename,
                "analysis": analysis_text,
                "model_used": model_name,
                "follow_ups": [
                    "Check drug interactions between these tablets",
                    "Explain side effects of these medications",
                    "What lifestyle precautions should I take?"
                ]
            }
        except Exception as e:
            last_error = str(e)
            continue

    raise HTTPException(
        status_code=429 if "429" in str(last_error) else 500,
        detail=f"Document vision quota temporarily exhausted or failed: {last_error}"
    )
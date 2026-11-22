import os
import re
import json
import traceback
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

load_dotenv()

app = FastAPI(title="MedEvidence — Clinical AI Workspace")

@tool
def lookup_fda_drug_info(drug_name: str) -> str:
    """Look up live official FDA drug label data (indications, boxed warnings, side effects, drug interactions) for any generic or brand medication."""
    url = "https://api.fda.gov/drug/label.json"
    params = {
        "search": f'openfda.brand_name:"{drug_name}" openfda.generic_name:"{drug_name}"',
        "limit": 1,
    }
    try:
        res = requests.get(url, params=params, timeout=6)
        if res.status_code != 200:
            return json.dumps({
                "source": "openFDA Drug Labeling API",
                "title": f"No FDA label found for {drug_name}",
                "summary": "Verify generic or brand spelling."
            })

        data = res.json()["results"][0]
        openfda = data.get("openfda", {})
        brand = openfda.get("brand_name", [drug_name.title()])[0]
        generic = openfda.get("generic_name", ["Unknown"])[0]
        indications = data.get("indications_and_usage", ["Not listed"])[0][:650]
        warnings = data.get("boxed_warning", data.get("warnings", ["Not listed"]))[0][:650]
        adverse = data.get("adverse_reactions", ["Not listed"])[0][:650]
        interactions = data.get("drug_interactions", ["Not listed"])[0][:500]

        return json.dumps({
            "source": "openFDA Drug Labeling API",
            "url": f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=all&query={drug_name}",
            "title": f"{brand} ({generic})",
            "indications": indications,
            "warnings": warnings,
            "adverse_reactions": adverse,
            "interactions": interactions
        })
    except Exception as e:
        return json.dumps({"source": "openFDA", "error": str(e)})

@tool
def search_live_clinical_trials(condition_or_drug: str) -> str:
    """Search ClinicalTrials.gov in real time for active, recruiting clinical trials for a medical condition or treatment."""
    url = "https://clinicaltrials.gov/api/v2/studies"
    params = {
        "query.term": condition_or_drug,
        "filter.overallStatus": "RECRUITING",
        "pageSize": 3,
    }
    try:
        res = requests.get(url, params=params, timeout=6)
        if res.status_code != 200:
            return json.dumps({"source": "ClinicalTrials.gov v2 API", "trials": []})

        studies = res.json().get("studies", [])
        trials = []
        for s in studies:
            proto = s.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            nct_id = ident.get("nctId", "")
            title = ident.get("briefTitle", "Untitled Study")
            summary = proto.get("descriptionModule", {}).get("briefSummary", "")[:350]
            trials.append({
                "nct_id": nct_id,
                "title": title,
                "summary": summary,
                "url": f"https://clinicaltrials.gov/study/{nct_id}"
            })
        return json.dumps({
            "source": "ClinicalTrials.gov Live Registry",
            "query": condition_or_drug,
            "trials": trials
        })
    except Exception as e:
        return json.dumps({"source": "ClinicalTrials.gov", "error": str(e)})

@tool
def search_medical_conditions(condition_name: str) -> str:
    """Look up verified patient and clinical summaries for diseases, symptoms, or health conditions from NLM MedlinePlus."""
    url = "https://wsearch.nlm.nih.gov/ws/query"
    params = {"db": "healthTopics", "term": condition_name, "retmax": 2}
    try:
        res = requests.get(url, params=params, timeout=6)
        if res.status_code != 200:
            return json.dumps({"source": "NLM MedlinePlus", "results": []})

        root = ET.fromstring(res.text)
        topics = []
        for doc in root.findall(".//document"):
            doc_url = doc.attrib.get("url", "https://medlineplus.gov")
            title_el = doc.find("./content[@name='title']")
            snippet_el = doc.find("./content[@name='FullSummary']")
            clean_title = re.sub(r"<[^>]+>", "", title_el.text or "") if title_el is not None else condition_name
            clean_summary = re.sub(r"<[^>]+>", "", snippet_el.text or "")[:700] if snippet_el is not None else ""
            topics.append({
                "title": clean_title,
                "summary": clean_summary,
                "url": doc_url
            })
        return json.dumps({
            "source": "NLM MedlinePlus Health Topics",
            "results": topics
        })
    except Exception as e:
        return json.dumps({"source": "MedlinePlus", "error": str(e)})

SYSTEM_PROMPT = """You are MedEvidence AI, a clinical research workspace assistant.
Rules:
1. ALWAYS call the appropriate live clinical tool(s) before answering:
   - Use `lookup_fda_drug_info` for medications, side effects, warnings, or interactions.
   - Use `search_live_clinical_trials` when asked about active studies, trials, or new treatments.
   - Use `search_medical_conditions` for diseases, symptoms, or medical conditions.
2. Structure your answer clearly using Markdown headings (`###`), bold key terms, and clean bullet points.
3. Never provide personal medical diagnoses or recommend changing prescribed doses. Include a brief clinical disclaimer.
4. At the very end of your response, include a section formatted EXACTLY like this with 3 smart follow-up questions:
FOLLOWUPS:
- First follow-up question?
- Second follow-up question?
- Third follow-up question?"""

tools = [lookup_fda_drug_info, search_live_clinical_trials, search_medical_conditions]
memory = MemorySaver()
MODEL_CANDIDATES = ["gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-3.8-flash-lite"]

class ChatRequest(BaseModel):
    message: str
    session_id: str

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    last_error = None
    for model_name in MODEL_CANDIDATES:
        try:
            print(f"[MedEvidence] Trying model: {model_name} for query: {req.message}")
            llm = ChatGoogleGenerativeAI(model=model_name, max_retries=0)
            agent_executor = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT, checkpointer=memory)

            config = {"configurable": {"thread_id": req.session_id}}
            prev_state = agent_executor.get_state(config)
            prev_len = len(prev_state.values.get("messages", [])) if prev_state and prev_state.values else 0

            result = agent_executor.invoke(
                {"messages": [HumanMessage(content=req.message)]},
                config=config,
            )

            new_messages = result["messages"][prev_len:]
            evidence_items = []
            for msg in new_messages:
                if isinstance(msg, ToolMessage):
                    try:
                        evidence_items.append(json.loads(msg.content))
                    except Exception:
                        evidence_items.append({"source": "Clinical Tool", "raw": msg.content})

            raw_reply = result["messages"][-1].content
            if isinstance(raw_reply, list):
                raw_reply = "".join(part.get("text", "") for part in raw_reply if isinstance(part, dict))

            followups = []
            main_answer = raw_reply
            if "FOLLOWUPS:" in raw_reply:
                parts = raw_reply.split("FOLLOWUPS:", 1)
                main_answer = parts[0].strip()
                for line in parts[1].strip().splitlines():
                    cleaned = line.lstrip("-•*0123456789. ").strip()
                    if cleaned:
                        followups.append(cleaned)

            return {
                "reply": main_answer,
                "evidence": evidence_items,
                "followups": followups[:3]
            }
        except Exception as e:
            last_error = e
            print(f"[MedEvidence] {model_name} failed: {type(e).__name__}: {e}")
            continue

    traceback.print_exc()
    return {
        "reply": f"### Backend Error\n\nAll models failed. Last error:\n\n`{type(last_error).__name__}: {str(last_error)}`",
        "evidence": [],
        "followups": []
    }

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_homepage():
    return FileResponse("static/index.html")
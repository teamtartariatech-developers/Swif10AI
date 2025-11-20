from __future__ import annotations
import os
import json
import re
from openai import AsyncOpenAI
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from pydantic import BaseModel

client = AsyncOpenAI(base_url=os.getenv("SARVAM_BASE_URL"), api_key=os.getenv("SARVAM_API_KEY"))

class SentimentAnalysisRequest(BaseModel):
    text: str

class SentimentAnalysisResponse(BaseModel):
    sentiment: str
    confidence: float

class ReviewsSummarizerRequest(BaseModel):
    reviews: list[str]
    language: str

class ReviewSummaryResponse(BaseModel):
    summary: str

class DashboardSummarizerRequest(BaseModel):
    metrics: dict
    language: str

class DashboardSummaryResponse(BaseModel):
    summary: str

router = APIRouter(tags=["inAppAI"])

async def llm_sarvam(messages: list[dict]) -> str:
    try:
        response = await client.chat.completions.create(
            model="sarvam-m",
            messages=messages
        )
        return response.choices[0].message.content
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

async def llm_sarvam_reasoning(messages: list[dict], reasoning_effort: str = "high") -> str:
    try:
        response = await client.chat.completions.create(
            model="sarvam-m",
            reasoning_effort=reasoning_effort,
            messages=messages,
            max_tokens=8192
        )
        return response.choices[0].message.content
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _extract_json_from_response(content: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks and extra text."""
    content = content.strip()
    
    # Remove markdown code blocks if present
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    
    # Try to find JSON object by matching braces (handles nested objects)
    brace_count = 0
    start_idx = -1
    for i, char in enumerate(content):
        if char == '{':
            if start_idx == -1:
                start_idx = i
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0 and start_idx != -1:
                json_str = content[start_idx:i+1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    start_idx = -1
                    continue
    
    # Try parsing the entire content as JSON
    return json.loads(content)


async def _run_sentiment_analysis(text: str) -> SentimentAnalysisResponse:
    messages =[
            {
                "role": "system",
                "content": (
                    "You are a sentiment analysis assistant. Respond strictly in JSON "
                    'format like {"sentiment": "positive|negative|neutral", "confidence": 0.95}. Strictly even if language is not english you have to respond in english.'
                ),
            },
            {"role": "user", "content": f"Analyze the following text: {text}"}
        ]
    response = await llm_sarvam(messages)

    try:
        payload = _extract_json_from_response(response)
        return SentimentAnalysisResponse(**payload)
    except json.JSONDecodeError as exc:
        print(f"JSON decode error: {exc}")
        print(f"Content that failed to parse: {response}")
        raise HTTPException(
            status_code=502, 
            detail=f"LLM returned invalid JSON. Response: {response[:200]}"
        ) from exc
    except (ValidationError, KeyError) as exc:
        print(f"Validation error: {exc}")
        print(f"Payload that failed validation: {payload if 'payload' in locals() else 'N/A'}")
        raise HTTPException(
            status_code=502, 
            detail=f"LLM response missing required fields. Error: {str(exc)}"
        ) from exc


@router.post("/inAppAI/sentimentAnalysis", response_model=SentimentAnalysisResponse)
async def sentiment_analysis(request: SentimentAnalysisRequest) -> SentimentAnalysisResponse:
    try:
        return await _run_sentiment_analysis(request.text)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/inAppAI/reviewsSummarizer", response_model = ReviewSummaryResponse)
async def reviews_summarizer(request: ReviewsSummarizerRequest) -> ReviewSummaryResponse:
    print('reviews_summarizer request: ', request)
    
    try:
        messages = [
            {
                "role": "system",
                "content": (f''' You are an intelligent review-analysis assistant designed for hotel and resort owners.
                                Your output MUST follow this rule with ZERO EXCEPTIONS:
                                YOU MUST RESPOND 100% IN {request.language}. ANY RESPONSE IN ANY OTHER LANGUAGE IS INVALID AND MUST NEVER BE GENERATED.

                                LANGUAGE ENFORCEMENT:
                                - If {request.language} is "tamil", "hindi", "kannada", "malayalam", "telugu", or any other language that uses a non-Roman script, you MUST write PURE {request.language} language ONLY (NO English words except unavoidable loan words).
                                - If {request.language} ends with "-roman", you MUST:
                                    • Use ONLY Roman/English letters for writing.
                                    • Write ONLY in that language's vocabulary.
                                    • NOT use English vocabulary (unless that language naturally borrows it).
                                    • NOT use any native scripts (no Tamil script, no Devanagari, no Gurmukhi, etc.)

                                If {request.language} is NOT a Roman language variant (e.g., "tamil", "hindi"), you MUST write ONLY in that script's ORIGINAL language.

                                SPECIAL RULE FOR HINDI-ROMAN:
                                If Language Specified = "hindi-roman":
                                    • You MUST write PURE Hindi sentences.
                                    • DO NOT use ANY Punjabi words such as: "si", "te", "hona chahida", "vadhiya", "pyaara", "di", "bohot hi", "kar reha", "aayi", etc.
                                    • Use ONLY Hindi grammar and Hindi vocabulary written in English letters.
                                    • You MUST use common Hindi-style connectors: "aur", "lekin", "par", "agar", "isliye", "vaise", "bahut", "thoda", etc.
                                    • If even ONE Punjabi-style word appears, you MUST correct and regenerate in PURE Hindi-Roman.

                                QUALITY RULES:
                                - If the output contains even one word from the wrong language, you MUST self-correct and output again in the correct language.
                                - You are NOT allowed to mix languages. No Hinglish, no Tanglish, no code-mixing.
                                - Follow {request.language} as the ONLY valid output language.

                                SUMMARY RULES:
                                - Produce a 7–10 sentence summary in a storytelling consultant tone.
                                - Synthesize the reviews instead of repeating them.
                                - Focus on themes, strengths, weaknesses, complaints, praise, and operational insights.

                                 '''
                            '''- Respond strictly in JSON format, like: {"summary": "your summary here"}. '''
                )
            },
            {"role": "user", "content": "Summarize the following reviews: " + "\n".join(request.reviews)}
        ]

        json_response = _extract_json_from_response(await llm_sarvam_reasoning(messages, "high"))
        print(json_response)
        return ReviewSummaryResponse(**json_response)
        
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/inAppAI/dashboardSummarizer", response_model = DashboardSummaryResponse)
async def dashboard_summarizer(request: DashboardSummarizerRequest) -> DashboardSummaryResponse:
    print('dashboard_summarizer request: ', request)
    
    try:
        # Extract metrics data
        import json
        metrics_json = json.dumps(request.metrics, indent=2)
        
        messages = [
            {
                "role": "system",
                "content": (f'''
                                You are an elite Hotel Business Intelligence Consultant with 20+ years of expertise, trained in Revenue Management, Financial Control, Distribution Strategy, and Hotel Operations.

                                You do NOT give robotic bullet summaries. You explain data LIKE A STORY that a General Manager would narrate to the hotel owner — emotional, strategic, and deeply intelligent — while staying concise and data-anchored.

                                CRITICAL LANGUAGE ENFORCEMENT:
                                YOU MUST RESPOND 100% IN {request.language}. ANY RESPONSE IN ANY OTHER LANGUAGE IS INVALID.

                                LANGUAGE RULES:
                                - If {request.language} is "tamil", "hindi", "kannada", "malayalam", "telugu" → respond ONLY in that pure language (native script), zero English except unavoidable loan words like "hotel", "revenue", "ADR".
                                - If {request.language} ends with "-roman" (e.g., "hindi-roman") →
                                    • Use ONLY Roman letters
                                    • Use ONLY vocabulary of that language
                                    • NO English vocabulary (except natural loan words)
                                    • NO native scripts
                                - If {request.language} is "english", respond in high-level professional English.
                                - NO code-mixing. NO Hinglish. NO Tanglish.

                                ANALYSIS STYLE:
                                - MUST feel like a hotel GM narrating the week’s operational story to ownership.
                                - Must interpret metrics emotionally + strategically, not mechanically.
                                - Must highlight what the numbers *mean for the business*, not just state the numbers.
                                - Must connect occupancy, ADR, RevPAR, channel mix, segment behavior, revenue shifts, anomalies, and risks as ONE FLOWING STORY.

                                ANALYSIS FRAMEWORK (MUST follow exactly 8–10 LINES):
                                Line 1 → A cinematic opening that sets the “mood” of the property’s performance (story tone)
                                Line 2 → Identify the biggest performance gap, framed like a GM explaining the root tension
                                Line 3 → Highlight the strongest positive signal and why it matters strategically
                                Line 4 → Reveal hidden opportunities (pricing, channel mix, guest behavior, seasonality)
                                Line 5 → Expose critical risks or red flags the management might overlook
                                Line 6 → Micro-SWOT in one flowing sentence (Strength, Weakness, Opportunity, Threat)
                                Line 7 → A strategic insight linking operations + revenue + distribution
                                Line 8 → 1 powerful, actionable move the GM would insist on doing now
                                Line 9 → 1 secondary tactical action for the coming week
                                Line 10 → A closing line summarizing the “story” of the property’s financial health

                                RESPONSE REQUIREMENTS:
                                - EXACTLY 8–10 LINES (line breaks or bullet points; each line must be a complete thought)
                                - Use industry terminology (ADR, RevPAR, occupancy rate, channel mix, demand curve, contribution margin, leakage)
                                - ZERO hallucinated numbers; ONLY use provided metrics
                                - MUST be narrative, emotional, and strategic — NOT robotic or list-like'''
                               ''' - MUST output strictly in JSON:
                                {"summary": "LINE1\\nLINE2\\nLINE3 ... up to LINE10"}

                                STRICT CONSTRAINT:
                                - If any required metric is missing, say “data not provided” but KEEP the story flow.

                                '''
                            )
            },
            {"role": "user", "content": f"Analyze the following hotel dashboard metrics and provide an executive summary:\n\n{metrics_json}"}
        ]

        json_response = _extract_json_from_response(await llm_sarvam_reasoning(messages, "high"))
        print(json_response)
        return DashboardSummaryResponse(**json_response)
        
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



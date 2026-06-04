"""
MetaHuman Digital Human MCP Server.

Provides tools for TTS voice selection, WebRTC connection management,
and knowledge base querying via WebSocket to the MetaHuman service at localhost:8080.
"""

from typing import Optional
from enum import Enum
import json
import asyncio
import httpx
from pydantic import BaseModel, Field, field_validator, ConfigDict
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("metahuman_mcp")

import os as _os
API_BASE_URL = _os.environ.get("METAHUMAN_API_URL", "http://localhost:8080")
WS_URL = _os.environ.get("METAHUMAN_WS_URL", "ws://localhost:8080/ws")


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


async def _api_request(endpoint: str, method: str = "GET", **kwargs) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method,
            f"{API_BASE_URL}/{endpoint}",
            timeout=30.0,
            **kwargs
        )
        response.raise_for_status()
        return response.json()


def _handle_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 404:
            return "Error: Resource not found. Please check the endpoint."
        elif e.response.status_code == 403:
            return "Error: Permission denied."
        elif e.response.status_code == 429:
            return "Error: Rate limit exceeded. Please wait."
        return f"Error: API request failed with status {e.response.status_code}"
    elif isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. Please try again."
    elif isinstance(e, (httpx.ConnectError, httpx.RemoteProtocolError)):
        return f"Error: Cannot connect to MetaHuman service at {API_BASE_URL}"
    elif isinstance(e, json.JSONDecodeError):
        return "Error: Received invalid response from MetaHuman service."
    return f"Error: {type(e).__name__}: {str(e)}"


class GetTTSVoicesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="mh_get_tts_voices",
    annotations={
        "title": "Get TTS Voices",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def mh_get_tts_voices(params: GetTTSVoicesInput) -> str:
    """
    Get the list of available TTS (Text-to-Speech) voices from the MetaHuman digital human service.

    The MetaHuman service (localhost:8080) exposes built-in voice profiles used for
    speech synthesis during digital human interactions. Each voice has a unique name
    (e.g., "Man", "Dz") that can be passed to mh_knowledge_query's voice_name parameter.

    This is a read-only call that does not modify any state. Use it to discover which
    voice names are valid before querying.

    Args:
        params (GetTTSVoicesInput): Validated input parameters containing:
            - response_format: "markdown" (default) or "json"

    Returns:
        str: List of available voices.
        Markdown format: Each voice as "## {voice_name}" with Description, Gender, Language,
                         ReferenceAudio (sample .wav path), ReferenceText.
        JSON format: {"type": 0, "data": {"voices": {"Man": {"Description": str, "Gender": str,
                     "Language": str, "ReferenceAudio": str, "ReferenceText": str}, ...}}, "status": bool}

    Error:
        "Error: API request failed with status ..." if MetaHuman service is unreachable.
        "Error: Request timed out." if the TTS service is slow to respond.

    Use when:
        - Discovering available voice names before calling mh_knowledge_query
        - Showing the user which voices exist and their characteristics
        - Verifying that a preferred voice (e.g., a female voice) is available
    Don't use when:
        - You want to query knowledge (use mh_knowledge_query)
        - You want to establish a video/audio session (use mh_webrtc_offer)
    """
    try:
        data = await _api_request("tts/voices")

        if params.response_format == ResponseFormat.MARKDOWN:
            voices = data.get("data", {}).get("voices", {})
            if not voices:
                return "# TTS Voices\n\nNo voices available."
            
            lines = [f"# TTS Voices (Total: {len(voices)})", ""]
            for voice_name, voice_info in voices.items():
                lines.append(f"## {voice_name}")
                lines.append(f"- **Description**: {voice_info.get('Description', 'N/A')}")
                lines.append(f"- **Gender**: {voice_info.get('Gender', 'N/A')}")
                lines.append(f"- **Language**: {voice_info.get('Language', 'N/A')}")
                lines.append(f"- **Reference Audio**: {voice_info.get('ReferenceAudio', 'N/A')}")
                lines.append(f"- **Reference Text**: {voice_info.get('ReferenceText', 'N/A')}")
                lines.append("")
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class WebRTCOfferInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    sdp: str = Field(..., description="WebRTC SDP offer string", min_length=10)
    gender: Optional[str] = Field(default="male", description="Gender for WebRTC routing: 'male' or 'female'")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")

    @field_validator('sdp')
    @classmethod
    def validate_sdp(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("SDP cannot be empty")
        return v.strip()


@mcp.tool(
    name="mh_webrtc_offer",
    annotations={
        "title": "WebRTC Offer",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def mh_webrtc_offer(params: WebRTCOfferInput) -> str:
    """
    Establish a WebRTC peer-to-peer connection with the MetaHuman digital human service.

    Sends an SDP offer to the MetaHuman service and receives an SDP answer plus a session ID.
    The session ID is required to later interrupt the session via mh_webrtc_interrupt.

    The service supports gender-based routing: setting gender="male" or "female" connects
    to different backend WebRTC media servers (each provides a different digital human avatar).

    This is a low-level tool primarily useful for building video/audio interactions with
    the digital human. The actual media streams are established after the SDP exchange.

    Args:
        params (WebRTCOfferInput): Validated input parameters containing:
            - sdp (str, required): SDP offer string from the WebRTC client (must be valid SDP)
            - gender (str, default="male"): Backend routing — "male" or "female" avatar
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Connection result.
        Markdown: "# WebRTC Connection Established" with Session ID, SDP type, SDP preview.
        JSON: {"data": {"type": "answer", "sdp": str, ...}, "session_id": str}

        The session_id is returned both in the response body AND the X-Session-ID header.
        Keep it for subsequent mh_webrtc_interrupt calls.

    Error:
        "Error: API request failed with status 500" if WebRTC backend service is down.
        "Error: API request failed with status 422" if SDP format is invalid.

    Use when:
        - Building a video/audio session with the digital human
        - Starting a real-time interactive session that needs media streams
    Don't use when:
        - You just want to query knowledge text (use mh_knowledge_query)
        - You want to list available voices (use mh_get_tts_voices)
        - You want to stop an existing session (use mh_webrtc_interrupt)
    """
    try:
        body = {
            "type": "offer",
            "sdp": params.sdp,
        }
        if params.gender:
            body["gender"] = params.gender

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_BASE_URL}/offer",
                json=body,
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()
            session_id = response.headers.get("X-Session-ID", "")

        if params.response_format == ResponseFormat.MARKDOWN:
            lines = [
                "# WebRTC Connection Established",
                f"- **Session ID**: {session_id or data.get('sessionid', 'N/A')}",
                f"- **SDP Type**: {data.get('type', 'N/A')}",
                f"- **SDP**: {data.get('sdp', 'N/A')[:100]}...",
            ]
            return "\n".join(lines)
        else:
            return json.dumps({"data": data, "session_id": session_id}, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class WebRTCInterruptInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    session_id: str = Field(..., description="WebRTC session ID to interrupt", min_length=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")

    @field_validator('session_id')
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Session ID cannot be empty")
        return v.strip()


@mcp.tool(
    name="mh_webrtc_interrupt",
    annotations={
        "title": "WebRTC Interrupt",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def mh_webrtc_interrupt(params: WebRTCInterruptInput) -> str:
    """
    Interrupt (stop) an active WebRTC digital human session.

    Sends an interrupt command to the MetaHuman service for the given session ID.
    The service locates the session across male/female WebRTC backends and stops the
    digital human's current speech/action. Useful when the user wants to cut off the
    digital human mid-response.

    Args:
        params (WebRTCInterruptInput): Validated input parameters containing:
            - session_id (str, required): Session ID returned by mh_webrtc_offer (e.g., "1234567890")
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Interruption result.
        Markdown: "# WebRTC Interrupted" with Session ID and success status.
        JSON: {"session_id": str, "status": "interrupted"}

    Error:
        "Error: API request failed with status 404" if the session ID is not found.
        "Error: API request failed with status 500" if the interrupt service fails.

    Use when:
        - User wants to stop the digital human from speaking
        - Terminating a session that was started with mh_webrtc_offer
    Don't use when:
        - There is no active WebRTC session (nothing to interrupt)
        - You want to query knowledge (use mh_knowledge_query)
    """
    try:
        body = {"sessionid": params.session_id}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_BASE_URL}/interrupt",
                json=body,
                timeout=30.0
            )
            response.raise_for_status()

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"# WebRTC Interrupted\n- **Session ID**: {params.session_id}\n- **Status**: Successfully interrupted"
        else:
            return json.dumps({"session_id": params.session_id, "status": "interrupted"}, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class KnowledgeQueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    query: str = Field(..., description="Knowledge base query text", min_length=1, max_length=2000)
    voice_name: Optional[str] = Field(default="Man", description="Voice name for TTS (optional, default: Man)")
    timeout: Optional[int] = Field(default=30, description="Total timeout in seconds", ge=5, le=120)
    chunk_timeout: Optional[int] = Field(default=3, description="Per-chunk timeout in seconds", ge=1, le=30)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")

    @field_validator('query')
    @classmethod
    def validate_query(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Query cannot be empty")
        return v.strip()


@mcp.tool(
    name="mh_knowledge_query",
    annotations={
        "title": "Knowledge Query",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def mh_knowledge_query(params: KnowledgeQueryInput) -> str:
    """
    Query the MetaHuman knowledge base and return an AI-generated natural-language answer.

    This is the PRIMARY knowledge retrieval tool. It connects to the MetaHuman service via
    WebSocket (ws://localhost:8080/ws), sends the query to a remote LLM-enhanced knowledge
    service (backed by http://34.63.149.24:8081), and collects streaming text chunks into
    a complete answer. The answer is generated in Chinese by default.

    This replaces the built-in KnowledgeSearch tool — use this instead for domain knowledge.
    The knowledge must have been previously indexed via kb_upload_document + kb_process_document.

    Args:
        params (KnowledgeQueryInput): Validated input parameters containing:
            - query (str, required): The question to ask, in natural language (e.g., "防洪预案的响应等级分几级？")
            - voice_name (str, default="Man"): TTS voice for the streaming response (see mh_get_tts_voices for options)
            - timeout (int, default=30): Max total wait time in seconds (5-120) before giving up
            - chunk_timeout (int, default=3): Max wait between consecutive chunks (1-30).
              If no new chunk arrives within this time, collection stops.
            - response_format: "markdown" (default) or "json"

    Returns:
        str: The AI-generated answer.
        Markdown: "# Knowledge Query Result" with query echo, voice name, chunk count, then "## Answer"
                  with the full concatenated text.
        JSON: {"query": str, "voice_name": str, "chunks_count": int, "response": str}

    Error:
        "No response received from knowledge base." if the service returns zero chunks
            (the KB may be empty, or the query did not match any content).
        "Error: WebSocket connection failed: ..." if the MetaHuman service (localhost:8080) is unreachable.
        "Error: <msg>" if the service returns an error notification (type=4, status=false).

    Behavior notes:
        - Streaming collection stops when: chunk_timeout exceeded, total timeout reached,
          or WebSocket connection closes. Whichever happens first.
        - voice_name only affects TTS playback; the text answer is the same regardless.

    Use when:
        - Answering user domain questions using business documents in the MetaHuman KB
        - Needing LLM-summarized answers (not raw document chunks)
        - Replacing the old built-in KnowledgeSearch tool
    Don't use when:
        - Searching the web for external information (use WebSearch instead)
        - Browsing KB metadata (use kb_list_knowledge_bases or kb_get_knowledge_base)
        - You need to inspect the raw chunks themselves (use kb_list_chunks)

    Example:
        params = KnowledgeQueryInput(query="水库汛限水位是多少？", voice_name="Man", timeout=45)
    """
    try:
        import websockets
        from websockets.exceptions import ConnectionClosed, TimeoutError as WSTimeout
        
        message = {
            "type": 0,
            "data": {
                "voice_name": params.voice_name or "Man",
                "messages": [
                    {
                        "role": "user",
                        "content": params.query
                    }
                ]
            }
        }

        chunks = []
        total_timeout = params.timeout or 30
        chunk_timeout = params.chunk_timeout or 3

        async with websockets.connect(
            WS_URL,
            ping_interval=5,
            ping_timeout=10,
            close_timeout=5
        ) as ws:
            await ws.send(json.dumps(message))
            
            start_time = asyncio.get_event_loop().time()
            
            while True:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > total_timeout:
                    break
                
                try:
                    response = await asyncio.wait_for(
                        ws.recv(),
                        timeout=chunk_timeout
                    )
                    
                    resp_data = json.loads(response)

                    if resp_data.get("type") == 0:
                        if resp_data.get("status") is not False:
                            chunk = resp_data.get("data", "")
                            if isinstance(chunk, str) and chunk:
                                chunks.append(chunk)

                    if resp_data.get("type") == 4 and not resp_data.get("status"):
                        error_msg = resp_data.get("data", "Unknown error")
                        return f"Error: {error_msg}"

                except asyncio.TimeoutError:
                    break
                except ConnectionClosed:
                    break
                except json.JSONDecodeError as e:
                    import logging
                    logging.getLogger(__name__).warning("WebSocket JSON decode error: %s", e)
                    continue
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("WebSocket recv error: %s", e)
                    break

        full_response = "".join(chunks)

        if not full_response:
            return "No response received from knowledge base."

        if params.response_format == ResponseFormat.MARKDOWN:
            lines = [
                "# Knowledge Query Result",
                f"- **Query**: {params.query}",
                f"- **Voice**: {params.voice_name or 'Man'}",
                f"- **Chunks**: {len(chunks)}",
                "",
                "## Answer",
                full_response
            ]
            return "\n".join(lines)
        else:
            return json.dumps({
                "query": params.query,
                "voice_name": params.voice_name or "Man",
                "chunks_count": len(chunks),
                "response": full_response
            }, ensure_ascii=False, indent=2)

    except ImportError:
        return "Error: websockets library not installed. Run: pip install websockets"
    except Exception as e:
        return f"Error: WebSocket connection failed: {type(e).__name__}: {str(e)}"


if __name__ == "__main__":
    mcp.run()

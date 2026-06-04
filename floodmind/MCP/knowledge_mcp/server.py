"""
Knowledge Base MCP Server.

Provides tools to manage knowledge bases, documents, and document chunks
through the HTTP API at localhost:8000.
"""

from typing import Optional, List
from enum import Enum
import json
import httpx
from pydantic import BaseModel, Field, field_validator, ConfigDict
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("knowledge_mcp")

import os as _os
API_BASE_URL = _os.environ.get("KNOWLEDGE_API_URL", "http://localhost:8000")


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
            return "Error: Resource not found. Please check the ID is correct."
        elif e.response.status_code == 403:
            return "Error: Permission denied."
        elif e.response.status_code == 429:
            return "Error: Rate limit exceeded. Please wait."
        return f"Error: API request failed with status {e.response.status_code}"
    elif isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. Please try again."
    elif isinstance(e, (httpx.ConnectError, httpx.RemoteProtocolError)):
        return f"Error: Cannot connect to knowledge service. Ensure it is running at {API_BASE_URL}"
    elif isinstance(e, json.JSONDecodeError):
        return "Error: Received invalid response from knowledge service."
    return f"Error: {type(e).__name__}: {str(e)}"


class ListKnowledgeBasesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    page: Optional[int] = Field(default=1, description="Page number", ge=1)
    per_page: Optional[int] = Field(default=20, description="Items per page", ge=1, le=100)
    type_id: Optional[int] = Field(default=None, description="Filter by type ID")
    name_filter: Optional[str] = Field(default=None, description="Filter by name")
    order_by: Optional[str] = Field(default=None, description="Field to order by")
    desc: Optional[bool] = Field(default=None, description="Descending order")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_list_knowledge_bases",
    annotations={
        "title": "List Knowledge Bases",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def kb_list_knowledge_bases(params: ListKnowledgeBasesInput) -> str:
    """
    List knowledge bases in the MetaHuman knowledge system with pagination.

    Returns all knowledge bases stored in the MetaHuman service (localhost:8000).
    Each knowledge base is a container for documents (PDF, DOCX, etc.) that have
    been uploaded and parsed into chunks for AI retrieval. Use this to find an
    existing KB ID before uploading documents or querying knowledge.

    This tool does NOT retrieve knowledge content — it only lists metadata.
    For querying knowledge, use mh_knowledge_query via mcp:metahuman.

    Args:
        params (ListKnowledgeBasesInput): Validated input parameters containing:
            - page (int, default=1): Page number (ge=1)
            - per_page (int, default=20): Results per page, 1-100
            - type_id (Optional[int]): Filter by KB type ID
            - name_filter (Optional[str]): Partial name match filter (e.g., "洪水", "水利")
            - order_by (Optional[str]): Sort field name
            - desc (Optional[bool]): Sort descending (default=False)
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Formatted list of knowledge bases.
        Markdown format returns headers with:
            {name} (ID: {id}), Description, Type, Function, document_count, token_count
        JSON format returns:
            {"is_success": bool, "data": {"items": [{"id": int, "name": str, "type": int, "description": str, "document_count": int, "token_count": int, "embedding_model": str, ...}], "total": int}, "error_msg": str}

    Error:
        "Error: API request failed with status ..." if MetaHuman service is unreachable.
        "Error: Request timed out." if the service takes too long.

    Use when:
        - Finding an existing KB ID before uploading a document
        - Checking available knowledge bases and their stats
        - Browsing KB names, document counts, and token counts
    Don't use when:
        - You need to retrieve knowledge content (use mcp:metahuman:mh_knowledge_query)
        - You need KB details with documents list (use kb_get_knowledge_base instead)
        - You want to create a new KB (use kb_create_knowledge_base instead)
    """
    try:
        query_params = {
            "page": params.page,
            "per_page": params.per_page,
        }
        if params.type_id is not None:
            query_params["type_id"] = params.type_id
        if params.name_filter:
            query_params["name_filter"] = params.name_filter
        if params.order_by:
            query_params["order_by"] = params.order_by
        if params.desc is not None:
            query_params["desc"] = params.desc

        data = await _api_request("api/knowledge-bases/", params=query_params)

        if params.response_format == ResponseFormat.MARKDOWN:
            items = data.get("data", {}).get("items", [])
            total = data.get("data", {}).get("total", 0)
            lines = [f"# Knowledge Bases (Total: {total})", ""]
            for kb in items:
                lines.append(f"## {kb.get('name', 'N/A')} (ID: {kb.get('id', 'N/A')})")
                lines.append(f"- **Description**: {kb.get('description', 'N/A')}")
                lines.append(f"- **Type**: {kb.get('type', 'N/A')}, **Function**: {kb.get('function', 'N/A')}")
                lines.append(f"- **Documents**: {kb.get('document_count', 0)}, **Tokens**: {kb.get('token_count', 0)}")
                lines.append("")
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class CreateKnowledgeBaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    name: str = Field(..., description="Knowledge base name", min_length=1, max_length=200)
    description: Optional[str] = Field(default="", description="Description")
    type: Optional[int] = Field(default=1, description="Type ID", ge=1)
    function: Optional[int] = Field(default=1, description="Function ID", ge=1)
    avatar_url: Optional[str] = Field(default=None, description="Avatar URL")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v.strip()


@mcp.tool(
    name="kb_create_knowledge_base",
    annotations={
        "title": "Create Knowledge Base",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def kb_create_knowledge_base(params: CreateKnowledgeBaseInput) -> str:
    """
    Create a new knowledge base in the MetaHuman knowledge system.

    A knowledge base is a container for documents. After creating one, you should
    upload documents via kb_upload_document and then trigger parsing via
    kb_process_document so the knowledge becomes queryable via mh_knowledge_query.

    Args:
        params (CreateKnowledgeBaseInput): Validated input parameters containing:
            - name (str, required): KB name, 1-200 chars (e.g., "防洪预案库", "水文资料")
            - description (Optional[str]): Human-readable description of this KB's purpose
            - type (int, default=1): Type ID; 1 = general document KB
            - function (int, default=1): Function ID; 1 = standard RAG function
            - avatar_url (Optional[str]): URL to a KB avatar image
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Created KB details.
        Markdown: KB name, ID, description, type, function, embedding_model.
        JSON: {"is_success": bool, "data": {"id": int, "name": str, "type": int, "description": str, "function": int, "is_kg": int, "token_count": int, "document_count": int, "embedding_model": str, "create_time": str, "update_time": str}, "error_msg": str}

    Error:
        "Error: API request failed with status 422" if name is invalid or duplicated.

    Workflow after create:
        1. kb_create_knowledge_base  → get kb_id
        2. kb_upload_document        → upload files to this kb_id
        3. kb_process_document       → trigger parsing for each uploaded doc
        4. (knowledge is now queryable via mh_knowledge_query)

    Use when:
        - Setting up a new topic area of documents
        - Organizing documents into separate collections
    Don't use when:
        - You just need to add documents to an existing KB (use kb_upload_document)
        - You just need to list existing KBs (use kb_list_knowledge_bases)
    """
    try:
        body = {
            "name": params.name,
            "description": params.description or "",
            "type": params.type,
            "function": params.function,
        }
        if params.avatar_url:
            body["avatar_url"] = params.avatar_url

        data = await _api_request("api/knowledge-bases/", method="POST", json=body)

        if params.response_format == ResponseFormat.MARKDOWN:
            kb = data.get("data", {})
            lines = [
                f"# Knowledge Base Created: {kb.get('name', 'N/A')}",
                f"- **ID**: {kb.get('id', 'N/A')}",
                f"- **Description**: {kb.get('description', 'N/A')}",
                f"- **Type**: {kb.get('type', 'N/A')}, **Function**: {kb.get('function', 'N/A')}",
                f"- **Embedding Model**: {kb.get('embedding_model', 'N/A')}",
            ]
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class GetKnowledgeBaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    kb_id: int = Field(..., description="Knowledge base ID", ge=1)
    include_documents: Optional[bool] = Field(default=False, description="Include documents list")
    include_chunks: Optional[bool] = Field(default=False, description="Include chunks list")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_get_knowledge_base",
    annotations={
        "title": "Get Knowledge Base",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def kb_get_knowledge_base(params: GetKnowledgeBaseInput) -> str:
    """
    Get detailed information of a specific knowledge base, including its documents and chunks.

    Unlike kb_list_knowledge_bases which returns a paginated list of KB metadata,
    this returns the full details of ONE KB, and can optionally include the list
    of all documents and all chunks inside it.

    Args:
        params (GetKnowledgeBaseInput): Validated input parameters containing:
            - kb_id (int, required): Knowledge base ID (e.g., 135)
            - include_documents (bool, default=False): Include full documents list in response
            - include_chunks (bool, default=False): Include chunks list (can be very large)
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Complete KB details.
        Markdown: Name, ID, description, type, function, document_count, token_count,
                  embedding_model, create_time, update_time (plus documents/chunks if requested).
        JSON: {"is_success": bool, "data": {"id": int, "name": str, "type": int, "description": str,
               "function": int, "is_kg": int, "token_count": int, "document_count": int,
               "embedding_model": str, "create_time": str, "update_time": str,
               "documents": [...], "chunks": [...]}, "error_msg": str}

    Error:
        "Error: Resource not found. Please check the ID is correct." if kb_id does not exist (404).

    Use when:
        - Needing full details of a specific KB you already know the ID of
        - Inspecting all documents in a KB (set include_documents=True)
        - Inspecting all chunks in a document (set include_chunks=True)
    Don't use when:
        - You don't know the KB ID (use kb_list_knowledge_bases first)
        - You want to modify the KB (use kb_update_knowledge_base)
        - You want to retrieve knowledge content (use mcp:metahuman:mh_knowledge_query)
    """
    try:
        query_params = {
            "include_documents": params.include_documents,
            "include_chunks": params.include_chunks
        }
        data = await _api_request(f"api/knowledge-bases/{params.kb_id}", params=query_params)

        if params.response_format == ResponseFormat.MARKDOWN:
            kb = data.get("data", {})
            lines = [
                f"# {kb.get('name', 'N/A')} (ID: {kb.get('id', 'N/A')})",
                f"- **Description**: {kb.get('description', 'N/A')}",
                f"- **Type**: {kb.get('type', 'N/A')}, **Function**: {kb.get('function', 'N/A')}",
                f"- **Documents**: {kb.get('document_count', 0)}, **Tokens**: {kb.get('token_count', 0)}",
                f"- **Embedding Model**: {kb.get('embedding_model', 'N/A')}",
                f"- **Created**: {kb.get('create_time', 'N/A')}",
                f"- **Updated**: {kb.get('update_time', 'N/A')}",
            ]
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class UpdateKnowledgeBaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    kb_id: int = Field(..., description="Knowledge base ID to update", ge=1)
    name: Optional[str] = Field(default=None, description="New name", min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, description="New description")
    avatar_url: Optional[str] = Field(default=None, description="New avatar URL")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_update_knowledge_base",
    annotations={
        "title": "Update Knowledge Base",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def kb_update_knowledge_base(params: UpdateKnowledgeBaseInput) -> str:
    """
    Update metadata of an existing knowledge base (name, description, avatar).

    Does NOT affect documents or chunks — only updates the KB's display information.
    Cannot change type, function, or embedding model after creation.

    Args:
        params (UpdateKnowledgeBaseInput): Validated input parameters containing:
            - kb_id (int, required): ID of the KB to update (e.g., 135)
            - name (Optional[str]): New name, 1-200 chars (e.g., "防洪预案库 v2")
            - description (Optional[str]): New description text
            - avatar_url (Optional[str]): New avatar image URL
            - response_format: "markdown" (default) or "json"
            (At least one of name/description/avatar_url must be provided.)

    Returns:
        str: Updated KB details.
        Markdown: KB name, ID, description, update_time.
        JSON: {"is_success": bool, "data": {"id": int, "name": str, ..., "update_time": str}, "error_msg": str}

    Error:
        "Error: No fields to update." if no name/description/avatar_url provided.
        "Error: Resource not found." if kb_id does not exist (404).

    Use when:
        - Renaming a KB or updating its description/avatar
    Don't use when:
        - You want to add documents to the KB (use kb_upload_document)
        - You want to change the KB's type or function (recreate the KB instead)
    """
    try:
        body = {}
        if params.name:
            body["name"] = params.name
        if params.description is not None:
            body["description"] = params.description
        if params.avatar_url:
            body["avatar_url"] = params.avatar_url

        if not body:
            return "Error: No fields to update. Provide at least one of: name, description, avatar_url."

        data = await _api_request(f"api/knowledge-bases/{params.kb_id}", method="PUT", json=body)

        if params.response_format == ResponseFormat.MARKDOWN:
            kb = data.get("data", {})
            lines = [
                f"# Knowledge Base Updated: {kb.get('name', 'N/A')}",
                f"- **ID**: {kb.get('id', 'N/A')}",
                f"- **Description**: {kb.get('description', 'N/A')}",
                f"- **Updated**: {kb.get('update_time', 'N/A')}",
            ]
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class DeleteKnowledgeBaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    kb_id: int = Field(..., description="Knowledge base ID to delete", ge=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_delete_knowledge_base",
    annotations={
        "title": "Delete Knowledge Base",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def kb_delete_knowledge_base(params: DeleteKnowledgeBaseInput) -> str:
    """
    Permanently delete a knowledge base and all its data. DESTRUCTIVE — cannot be undone.

    Deleting a KB removes all documents, chunks, and embeddings stored in it.
    This data cannot be recovered. Confirm with the user before calling this tool.

    Args:
        params (DeleteKnowledgeBaseInput): Validated input parameters containing:
            - kb_id (int, required): ID of the KB to delete (e.g., 135)
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Deletion result.
        Markdown: "# Knowledge Base Deleted" with ID and success status.
        JSON: {"is_success": bool, "data": true, "error_msg": str}

    Error:
        "Error: Resource not found." if kb_id does not exist (404).

    Use when:
        - User explicitly requests deleting a KB and all its content
    Don't use when:
        - You just want to remove one document (use kb_delete_document)
        - You want to clear a KB but keep it (delete documents individually instead)

    WARNING: This operation is irreversible. Always confirm the KB name with the user first.
    """
    try:
        data = await _api_request(f"api/knowledge-bases/{params.kb_id}", method="DELETE")

        if params.response_format == ResponseFormat.MARKDOWN:
            if data.get("is_success"):
                return f"# Knowledge Base Deleted\n- **ID**: {params.kb_id}\n- **Status**: Successfully deleted"
            else:
                return f"Error: {data.get('error_msg', 'Unknown error')}"
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class ListDocumentsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    kb_id: int = Field(..., description="Knowledge base ID", ge=1)
    page: Optional[int] = Field(default=1, description="Page number", ge=1)
    per_page: Optional[int] = Field(default=20, description="Items per page", ge=1, le=100)
    parse_status: Optional[int] = Field(default=None, description="Filter by parse status")
    is_enable: Optional[int] = Field(default=None, description="Filter by enable status")
    name_filter: Optional[str] = Field(default=None, description="Filter by document name")
    order_by: Optional[str] = Field(default=None, description="Field to order by")
    desc: Optional[bool] = Field(default=None, description="Descending order")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_list_documents",
    annotations={
        "title": "List Documents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def kb_list_documents(params: ListDocumentsInput) -> str:
    """
    List all documents in a specific knowledge base with pagination and filters.

    Returns metadata for each document: file name, type, parse status, enable status,
    chunk count. Use this to see what documents have been uploaded to a KB and their
    processing state.

    Parse status values: 0 = pending, 1 = processing, 2 = completed, 3 = failed.
    Enable status: 1 = enabled (included in retrieval), 2 = disabled (ignored).

    Args:
        params (ListDocumentsInput): Validated input parameters containing:
            - kb_id (int, required): Knowledge base ID to list documents from (e.g., 135)
            - page (int, default=1): Page number (ge=1)
            - per_page (int, default=20): Results per page, 1-100
            - parse_status (Optional[int]): Filter by parse status (0/1/2/3)
            - is_enable (Optional[int]): Filter by enable status (1=enabled, 2=disabled)
            - name_filter (Optional[str]): Partial file name match
            - order_by (Optional[str]): Sort field ("file_name", "create_time", etc.)
            - desc (Optional[bool]): Sort descending
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Document list.
        Markdown: Each document shown as "## {file_name} (ID: {id})" with type, parse_status,
                  is_enable, chunk_count.
        JSON: {"is_success": bool, "data": {"items": [{"id": int, "kb_id": int, "file_name": str,
               "file_type": str, "file_url": str, "chunk_count": int, "function": int,
               "is_enable": int, "parse_status": int, "update_time": str, "create_time": str}],
               "total": int}, "error_msg": str}

    Error:
        "Error: API request failed with status ..." if MetaHuman service is unreachable.

    Use when:
        - Checking what's in a KB before uploading or deleting
        - Finding a specific document ID to process, enable/disable, or delete
        - Verifying a document's parse status after calling kb_process_document
    Don't use when:
        - You need chunk-level detail (use kb_list_chunks with the doc_id)
        - You want to add a document (use kb_upload_document)
    """
    try:
        query_params = {
            "kb_id": params.kb_id,
            "page": params.page,
            "per_page": params.per_page,
        }
        if params.parse_status is not None:
            query_params["parse_status"] = params.parse_status
        if params.is_enable is not None:
            query_params["is_enable"] = params.is_enable
        if params.name_filter:
            query_params["name_filter"] = params.name_filter
        if params.order_by:
            query_params["order_by"] = params.order_by
        if params.desc is not None:
            query_params["desc"] = params.desc

        data = await _api_request("api/documents/", params=query_params)

        if params.response_format == ResponseFormat.MARKDOWN:
            items = data.get("data", {}).get("items", [])
            total = data.get("data", {}).get("total", 0)
            lines = [f"# Documents in KB {params.kb_id} (Total: {total})", ""]
            for doc in items:
                lines.append(f"## {doc.get('file_name', 'N/A')} (ID: {doc.get('id', 'N/A')})")
                lines.append(f"- **Type**: {doc.get('file_type', 'N/A')}")
                lines.append(f"- **Parse Status**: {doc.get('parse_status', 'N/A')}, **Enabled**: {doc.get('is_enable', 'N/A')}")
                lines.append(f"- **Chunks**: {doc.get('chunk_count', 0)}")
                lines.append("")
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class UploadDocumentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    kb_id: int = Field(..., description="Knowledge base ID to upload to", ge=1)
    file_path: str = Field(..., description="Absolute path to local file to upload", min_length=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")

    @field_validator('file_path')
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        import os
        v = v.strip()
        if not v:
            raise ValueError("File path cannot be empty")
        resolved = os.path.realpath(v)
        forbidden = {os.path.sep + "etc", os.path.sep + "proc", os.path.sep + "sys",
                     os.path.sep + "dev", os.path.sep + "root", os.path.sep + "boot",
                     os.path.sep + "Windows", os.path.sep + "Program Files",
                     os.path.sep + "System32", os.path.sep + "AppData"}
        if any(resolved.lower().startswith(p.lower()) for p in forbidden):
            raise ValueError(f"Access denied: cannot upload files from system directory")
        if not os.path.isfile(resolved):
            raise ValueError(f"File not found: {v}")
        return resolved


@mcp.tool(
    name="kb_upload_document",
    annotations={
        "title": "Upload Document",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def kb_upload_document(params: UploadDocumentInput) -> str:
    """
    Upload a local file to a knowledge base. The file is read from disk and sent as multipart form data.

    This is the primary way to add documents to a knowledge base. After uploading,
    you MUST call kb_process_document to trigger parsing and chunking — otherwise
    the document will not be included in knowledge retrieval.

    Supported formats depend on the MetaHuman service; typically PDF, DOCX, TXT, XLSX, etc.

    Args:
        params (UploadDocumentInput): Validated input parameters containing:
            - kb_id (int, required): Target knowledge base ID (e.g., 135)
            - file_path (str, required): Absolute path to local file (e.g., "D:/docs/防洪预案.pdf")
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Uploaded document details.
        Markdown: File name, ID, kb_id, file_type, parse_status (0=pending), chunk_count (0 until processed), file_url.
        JSON: {"is_success": bool, "data": {"id": int, "kb_id": int, "file_name": str, "file_type": str,
               "file_url": str, "chunk_count": 0, "function": int, "is_enable": int,
               "parse_status": 0, "create_time": str, "update_time": str}, "error_msg": str}

    Error:
        "Error: File not found: {path}" if the local file does not exist.
        "Error: API request failed with status 422" if kb_id is invalid or file format unsupported.

    Full workflow to add a document:
        1. kb_upload_document(kb_id=135, file_path="D:/docs/预案.pdf")  → get doc_id
        2. kb_process_document(doc_id=doc_id)                           → triggers parsing
        3. kb_get_document(doc_id=doc_id)                               → verify parse_status=2 (completed)
        4. (knowledge is now queryable via mh_knowledge_query)

    Use when:
        - User provides a file path and wants to add it to a KB
        - Adding PDF/DOCX/TXT/XLSX documents to an existing KB
    Don't use when:
        - You want to add text content directly (not a file) — save it to a temp file first
        - The document already exists and you want to reprocess (use kb_process_document)
    """
    try:
        import os
        if not os.path.exists(params.file_path):
            return f"Error: File not found: {params.file_path}"

        file_name = os.path.basename(params.file_path)
        with open(params.file_path, "rb") as f:
            file_content = f.read()

        files = {"file": (file_name, file_content)}
        data_form = {"kb_id": params.kb_id}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_BASE_URL}/api/documents/upload",
                files=files,
                data=data_form,
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()

        if params.response_format == ResponseFormat.MARKDOWN:
            doc = data.get("data", {})
            lines = [
                f"# Document Uploaded: {doc.get('file_name', 'N/A')}",
                f"- **ID**: {doc.get('id', 'N/A')}",
                f"- **KB ID**: {doc.get('kb_id', 'N/A')}",
                f"- **Type**: {doc.get('file_type', 'N/A')}",
                f"- **Parse Status**: {doc.get('parse_status', 'N/A')}",
                f"- **Chunks**: {doc.get('chunk_count', 0)}",
                f"- **URL**: {doc.get('file_url', 'N/A')}",
            ]
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except FileNotFoundError:
        return f"Error: File not found: {params.file_path}"
    except Exception as e:
        return _handle_error(e)


class GetDocumentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    doc_id: int = Field(..., description="Document ID", ge=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_get_document",
    annotations={
        "title": "Get Document",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def kb_get_document(params: GetDocumentInput) -> str:
    """
    Get full details of a specific document including its processing state and storage info.

    Returns metadata for one document by its ID. Useful for checking parse status after
    uploading, or inspecting a document's properties.

    Parse status values: 0 = pending, 1 = processing, 2 = completed, 3 = failed.
    Enable status: 1 = enabled (included in kb retrieval), 2 = disabled.

    Args:
        params (GetDocumentInput): Validated input parameters containing:
            - doc_id (int, required): Document ID (e.g., 42)
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Document details.
        Markdown: File name, ID, kb_id, file_type, parse_status, is_enable, function,
                  chunk_count, file_url, create_time, update_time.
        JSON: {"is_success": bool, "data": {"id": int, "kb_id": int, "file_name": str,
               "file_type": str, "file_url": str, "chunk_count": int, "function": int,
               "is_enable": int, "parse_status": int, "create_time": str, "update_time": str},
               "error_msg": str}

    Error:
        "Error: Resource not found." if doc_id does not exist (404).

    Use when:
        - Checking if a document finished parsing (parse_status)
        - Getting the file_url or chunk_count of a known document
        - Verifying a document's enable/disable state
    Don't use when:
        - You need to see the actual text content (use kb_list_chunks on this doc_id)
        - You don't know the doc_id (use kb_list_documents first)
    """
    try:
        data = await _api_request(f"api/documents/{params.doc_id}")

        if params.response_format == ResponseFormat.MARKDOWN:
            doc = data.get("data", {})
            lines = [
                f"# {doc.get('file_name', 'N/A')} (ID: {doc.get('id', 'N/A')})",
                f"- **KB ID**: {doc.get('kb_id', 'N/A')}",
                f"- **Type**: {doc.get('file_type', 'N/A')}",
                f"- **Parse Status**: {doc.get('parse_status', 'N/A')}",
                f"- **Enabled**: {doc.get('is_enable', 'N/A')}, **Function**: {doc.get('function', 'N/A')}",
                f"- **Chunks**: {doc.get('chunk_count', 0)}",
                f"- **URL**: {doc.get('file_url', 'N/A')}",
                f"- **Created**: {doc.get('create_time', 'N/A')}",
                f"- **Updated**: {doc.get('update_time', 'N/A')}",
            ]
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class SetDocumentFunctionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    doc_id: int = Field(..., description="Document ID", ge=1)
    function: int = Field(..., description="Function ID: 1 or 2", ge=1, le=2)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_set_document_function",
    annotations={
        "title": "Set Document Function",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def kb_set_document_function(params: SetDocumentFunctionInput) -> str:
    """
    Set the processing function type for a document.

    Controls how the document is processed during parsing. Use this to switch between
    different chunking strategies after uploading.

    Function values:
        1 = standard chunking (default) — splits text into semantic chunks
        2 = alternative processing — may use different chunk boundaries or strategies

    Args:
        params (SetDocumentFunctionInput): Validated input parameters containing:
            - doc_id (int, required): Document ID (e.g., 42)
            - function (int, required): 1 = standard chunking, 2 = alternative processing
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Updated document details.
        Markdown: Document ID, function value, success status.
        JSON: {"is_success": bool, "data": {"id": int, ..., "function": int, ...}, "error_msg": str}

    Error:
        "Error: Resource not found." if doc_id does not exist (404).
        "Error: API request failed with status 422" if function value is invalid.

    Use when:
        - Switching a document to a different processing strategy before parsing
        - Fixing a document that parsed poorly with the default function
    Don't use when:
        - You just want to reprocess a document (use kb_process_document instead)
        - You want to enable/disable the document (use kb_set_document_enable)
    """
    try:
        data = await _api_request(
            f"api/documents/{params.doc_id}/function",
            method="PUT",
            params={"function": params.function}
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            doc = data.get("data", {})
            return f"# Document Function Set\n- **ID**: {doc.get('id', 'N/A')}\n- **Function**: {doc.get('function', 'N/A')}\n- **Status**: Success"
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class SetDocumentEnableInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    doc_id: int = Field(..., description="Document ID", ge=1)
    is_enable: int = Field(..., description="Enable status: 1 (enabled) or 2 (disabled)", ge=1, le=2)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_set_document_enable",
    annotations={
        "title": "Set Document Enable",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def kb_set_document_enable(params: SetDocumentEnableInput) -> str:
    """
    Enable or disable a document for knowledge retrieval.

    A disabled document remains stored in the KB but its chunks are excluded from
    knowledge query results. Use this to temporarily remove a document's content
    from retrieval without deleting it.

    Enable values: 1 = enabled (chunks included in retrieval), 2 = disabled (chunks excluded).

    Args:
        params (SetDocumentEnableInput): Validated input parameters containing:
            - doc_id (int, required): Document ID (e.g., 42)
            - is_enable (int, required): 1 = enable, 2 = disable
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Updated document details.
        Markdown: "# Document Enabled/Disabled" with ID, file_name, status.
        JSON: {"is_success": bool, "data": {"id": int, ..., "is_enable": int, ...}, "error_msg": str}

    Error:
        "Error: Resource not found." if doc_id does not exist (404).

    Use when:
        - Temporarily hiding a document's content without deleting it
        - Re-enabling a previously disabled document
    Don't use when:
        - You want to permanently delete the document (use kb_delete_document)
        - You want to change the processing function (use kb_set_document_function)
    """
    try:
        data = await _api_request(
            f"api/documents/{params.doc_id}/enable",
            method="PUT",
            params={"is_enable": params.is_enable}
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            doc = data.get("data", {})
            status = "Enabled" if params.is_enable == 1 else "Disabled"
            return f"# Document {status}\n- **ID**: {doc.get('id', 'N/A')}\n- **Name**: {doc.get('file_name', 'N/A')}\n- **Status**: {status}"
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class ProcessDocumentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    doc_id: int = Field(..., description="Document ID to process", ge=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_process_document",
    annotations={
        "title": "Process Document",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def kb_process_document(params: ProcessDocumentInput) -> str:
    """
    Trigger parsing and chunking for an uploaded document.

    After kb_upload_document, the raw file sits in the KB with parse_status=0 (pending).
    This call initiates the parsing pipeline: text extraction → chunking → embedding.
    Processing is asynchronous — call kb_get_document afterwards to check progress
    (parse_status: 0=pending, 1=processing, 2=completed, 3=failed).

    Args:
        params (ProcessDocumentInput): Validated input parameters containing:
            - doc_id (int, required): Document ID to process (e.g., 42)
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Processing initiation result.
        Markdown: "# Document Processing Started" with file_name, ID, current parse_status,
                  chunk_count, and "Processing initiated" status.
        JSON: {"is_success": bool, "data": {"id": int, "file_name": str, "parse_status": int,
               "chunk_count": int, ...}, "error_msg": str}

    Error:
        "Error: Resource not found." if doc_id does not exist (404).

    After calling this tool:
        - Wait a few seconds, then call kb_get_document(doc_id) to check parse_status
        - parse_status=2 means chunks are ready for retrieval via mh_knowledge_query
        - parse_status=3 means parsing failed (check document details for error message)

    Use when:
        - A document was just uploaded and needs to be parsed for retrieval
        - Reprocessing a document after changing its function type
    Don't use when:
        - You want to delete the document (use kb_delete_document)
        - The document is already parsed (parse_status=2) and nothing changed
    """
    try:
        data = await _api_request(f"api/documents/{params.doc_id}/process", method="POST")

        if params.response_format == ResponseFormat.MARKDOWN:
            doc = data.get("data", {})
            lines = [
                f"# Document Processing Started: {doc.get('file_name', 'N/A')}",
                f"- **ID**: {doc.get('id', 'N/A')}",
                f"- **Parse Status**: {doc.get('parse_status', 'N/A')}",
                f"- **Chunks**: {doc.get('chunk_count', 0)}",
                f"- **Status**: Processing initiated",
            ]
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class DeleteDocumentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    doc_id: int = Field(..., description="Document ID to delete", ge=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_delete_document",
    annotations={
        "title": "Delete Document",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def kb_delete_document(params: DeleteDocumentInput) -> str:
    """
    Permanently delete a document and all its chunks. DESTRUCTIVE — cannot be undone.

    Removes the document file, all its chunks, and their embeddings from the knowledge base.
    This data cannot be recovered after deletion.

    Args:
        params (DeleteDocumentInput): Validated input parameters containing:
            - doc_id (int, required): Document ID to delete (e.g., 42)
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Deletion result.
        Markdown: "# Document Deleted" with ID and success status.
        JSON: {"is_success": bool, "data": true, "error_msg": str}

    Error:
        "Error: Resource not found." if doc_id does not exist (404).

    Use when:
        - Removing a document that is no longer needed
        - Replacing a document (delete old, then upload new and reprocess)
    Don't use when:
        - You want to temporarily exclude the document (use kb_set_document_enable with is_enable=2)
        - You want to delete just one chunk (use kb_delete_chunk instead)
        - You want to delete the entire KB (use kb_delete_knowledge_base)
    """
    try:
        data = await _api_request(f"api/documents/{params.doc_id}", method="DELETE")

        if params.response_format == ResponseFormat.MARKDOWN:
            if data.get("is_success"):
                return f"# Document Deleted\n- **ID**: {params.doc_id}\n- **Status**: Successfully deleted"
            else:
                return f"Error: {data.get('error_msg', 'Unknown error')}"
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class ListChunksInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    doc_id: int = Field(..., description="Document ID", ge=1)
    page: Optional[int] = Field(default=1, description="Page number", ge=1)
    per_page: Optional[int] = Field(default=20, description="Items per page", ge=1, le=100)
    text_filter: Optional[str] = Field(default=None, description="Filter by chunk text")
    order_by: Optional[str] = Field(default=None, description="Field to order by")
    desc: Optional[bool] = Field(default=None, description="Descending order")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_list_chunks",
    annotations={
        "title": "List Document Chunks",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def kb_list_chunks(params: ListChunksInput) -> str:
    """
    List all chunks of a specific document with pagination and text filtering.

    Chunks are the atomic units of knowledge retrieval — each document is split into
    chunks during parsing (kb_process_document). Each chunk contains a text segment,
    an optional image URL, and an enable flag.

    Chunk text is truncated to 200 chars in markdown output. Use JSON format or
    increase chunk page size for full text.

    Args:
        params (ListChunksInput): Validated input parameters containing:
            - doc_id (int, required): Document ID to list chunks from (e.g., 42)
            - page (int, default=1): Page number (ge=1)
            - per_page (int, default=20): Results per page, 1-100
            - text_filter (Optional[str]): Partial text match filter within chunks
            - order_by (Optional[str]): Sort field
            - desc (Optional[bool]): Sort descending
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Chunk list.
        Markdown: Each chunk as "## Chunk ID: {id}" with type, is_enable, text (truncated),
                  optional img_url.
        JSON: {"is_success": bool, "data": {"items": [{"id": int, "doc_id": int, "kb_id": int,
               "text": str, "type": int, "img_url": str, "is_enable": int, "create_time": str}],
               "total": int}, "error_msg": str}

    Error:
        "Error: API request failed with status ..." if MetaHuman service is unreachable.

    Use when:
        - Inspecting the actual text content of chunks for quality review
        - Finding a specific chunk by text_filter to delete or disable
        - Debugging why a document's knowledge retrieval results are poor
    Don't use when:
        - You want to retrieve knowledge answers (use mcp:metahuman:mh_knowledge_query)
        - You just want to list documents (use kb_list_documents)
    """
    try:
        query_params = {
            "doc_id": params.doc_id,
            "page": params.page,
            "per_page": params.per_page,
        }
        if params.text_filter:
            query_params["text_filter"] = params.text_filter
        if params.order_by:
            query_params["order_by"] = params.order_by
        if params.desc is not None:
            query_params["desc"] = params.desc

        data = await _api_request("api/document-chunks/", params=query_params)

        if params.response_format == ResponseFormat.MARKDOWN:
            items = data.get("data", {}).get("items", [])
            total = data.get("data", {}).get("total", 0)
            lines = [f"# Chunks in Document {params.doc_id} (Total: {total})", ""]
            for chunk in items:
                lines.append(f"## Chunk ID: {chunk.get('id', 'N/A')}")
                lines.append(f"- **Type**: {chunk.get('type', 'N/A')}, **Enabled**: {chunk.get('is_enable', 'N/A')}")
                text = chunk.get('text', 'N/A')
                if len(text) > 200:
                    text = text[:200] + "..."
                lines.append(f"- **Text**: {text}")
                if chunk.get('img_url'):
                    lines.append(f"- **Image**: {chunk.get('img_url')}")
                lines.append("")
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class DeleteChunkInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    chunk_id: int = Field(..., description="Chunk ID to delete", ge=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_delete_chunk",
    annotations={
        "title": "Delete Chunk",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def kb_delete_chunk(params: DeleteChunkInput) -> str:
    """
    Permanently delete a single document chunk. DESTRUCTIVE — cannot be undone.

    Removes one chunk and its embedding. Useful when a specific chunk contains
    incorrect or irrelevant information that you want to exclude from retrieval,
    without deleting the entire document.

    Args:
        params (DeleteChunkInput): Validated input parameters containing:
            - chunk_id (int, required): Chunk ID to delete (e.g., 1024)
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Deletion result.
        Markdown: "# Chunk Deleted" with ID and success status.
        JSON: {"is_success": bool, "data": true, "error_msg": str}

    Error:
        "Error: Resource not found." if chunk_id does not exist (404).

    Use when:
        - Removing a specific bad chunk found during quality review
        - Cleaning up incorrectly parsed chunks
    Don't use when:
        - You want to temporarily hide a chunk (use kb_set_chunk_enable with is_enable=2)
        - You want to delete all chunks of a document (use kb_delete_document)
    """
    try:
        data = await _api_request(f"api/document-chunks/{params.chunk_id}", method="DELETE")

        if params.response_format == ResponseFormat.MARKDOWN:
            if data.get("is_success"):
                return f"# Chunk Deleted\n- **ID**: {params.chunk_id}\n- **Status**: Successfully deleted"
            else:
                return f"Error: {data.get('error_msg', 'Unknown error')}"
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


class SetChunkEnableInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    chunk_id: int = Field(..., description="Chunk ID", ge=1)
    is_enable: int = Field(..., description="Enable status: 1 (enabled) or 2 (disabled)", ge=1, le=2)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


@mcp.tool(
    name="kb_set_chunk_enable",
    annotations={
        "title": "Set Chunk Enable",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def kb_set_chunk_enable(params: SetChunkEnableInput) -> str:
    """
    Enable or disable a single document chunk for knowledge retrieval.

    A disabled chunk remains stored but is excluded from knowledge query results.
    Use this to temporarily hide a specific problematic chunk without deleting it,
    or to disable chunks that contain confidential/incorrect information.

    Enable values: 1 = enabled (included in retrieval), 2 = disabled (excluded).

    Args:
        params (SetChunkEnableInput): Validated input parameters containing:
            - chunk_id (int, required): Chunk ID to modify (e.g., 1024)
            - is_enable (int, required): 1 = enable, 2 = disable
            - response_format: "markdown" (default) or "json"

    Returns:
        str: Updated chunk details.
        Markdown: "# Chunk Enabled/Disabled" with chunk ID, doc_id, status.
        JSON: {"is_success": bool, "data": {"id": int, "doc_id": int, "kb_id": int, "text": str,
               "type": int, "img_url": str, "is_enable": int, "create_time": str}, "error_msg": str}

    Error:
        "Error: Resource not found." if chunk_id does not exist (404).

    Use when:
        - Temporarily hiding a problematic chunk during quality review
        - Re-enabling a chunk that was previously disabled
    Don't use when:
        - You want to permanently remove the chunk (use kb_delete_chunk)
        - You want to enable/disable the entire document (use kb_set_document_enable)
    """
    try:
        data = await _api_request(
            f"api/document-chunks/{params.chunk_id}/enable",
            method="PUT",
            params={"is_enable": params.is_enable}
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            chunk = data.get("data", {})
            status = "Enabled" if params.is_enable == 1 else "Disabled"
            lines = [
                f"# Chunk {status}",
                f"- **ID**: {chunk.get('id', 'N/A')}",
                f"- **Doc ID**: {chunk.get('doc_id', 'N/A')}",
                f"- **Status**: {status}",
            ]
            return "\n".join(lines)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


if __name__ == "__main__":
    mcp.run()

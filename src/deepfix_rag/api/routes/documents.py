"""Upload, list, and soft-delete shared corpus documents."""

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import BAD_REQUEST, FORBIDDEN, NOT_FOUND, UNAUTHORIZED, error_responses
from deepfix_rag.api.schemas import DocumentResponse, TextIssueResponse
from deepfix_rag.documents import (
    DeleteForbiddenError,
    DocumentRecord,
    UnknownDocumentError,
    UploadRejected,
    add_document,
    delete_document,
    list_documents,
)

router = APIRouter(tags=["documents"])


def _response(document: DocumentRecord) -> DocumentResponse:
    return DocumentResponse(
        doc_id=document.doc_id,
        filename=document.filename,
        media_type=document.media_type,
        status=document.status,
        index_error=document.index_error,
        issues=[TextIssueResponse(page=issue.page, kind=issue.kind) for issue in document.issues],
        uploader_email=document.uploader_email,
        created_at=document.created_at,
        can_delete=document.can_delete,
    )


@router.post(
    "/documents",
    operation_id="create_document",
    response_model=DocumentResponse,
    status_code=201,
    responses=error_responses(BAD_REQUEST, UNAUTHORIZED),
    summary="Upload a document",
)
def create_document(
    request: Request,
    file: UploadFile = File(...),
    reading: str | None = Form(default=None),
) -> DocumentResponse:
    """Store one text, Markdown, or PDF file. PDF text is read in the browser."""
    user = current_user(request)
    settings = get_settings(request)
    try:
        document = add_document(
            settings.product_db_path,
            settings.workspace_dir,
            user_id=user.id,
            filename=file.filename,
            content=file.file.read(),
            reading=reading,
        )
    except UploadRejected as exc:
        raise HTTPException(status_code=BAD_REQUEST, detail=str(exc)) from exc
    if document.status != "failed":
        request.app.state.index.on_document_added(document.doc_id)
    return _response(document)


@router.get(
    "/documents",
    operation_id="list_documents",
    response_model=list[DocumentResponse],
    responses=error_responses(UNAUTHORIZED),
    summary="List documents",
)
def list_document_route(request: Request) -> list[DocumentResponse]:
    """Return non-deleted documents for any signed-in user, newest first."""
    user = current_user(request)
    settings = get_settings(request)
    documents = list_documents(
        settings.product_db_path,
        settings.workspace_dir,
        user.id,
        user.role,
    )
    return [_response(document) for document in documents]


@router.delete(
    "/documents/{doc_id}",
    operation_id="delete_document",
    status_code=204,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, NOT_FOUND),
    summary="Delete a document",
)
def delete_document_route(doc_id: str, request: Request) -> Response:
    """Soft-delete a file. The uploader or an admin may delete it."""
    user = current_user(request)
    settings = get_settings(request)
    try:
        delete_document(
            settings.product_db_path,
            settings.workspace_dir,
            doc_id,
            user.id,
            user.role,
        )
    except UnknownDocumentError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail=str(exc)) from exc
    except DeleteForbiddenError as exc:
        raise HTTPException(status_code=FORBIDDEN, detail=str(exc)) from exc
    request.app.state.index.on_document_removed(doc_id)
    return Response(status_code=204)

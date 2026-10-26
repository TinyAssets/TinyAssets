"""C5: a credential-free progress page, authorized by its job capability."""
import re

PREFIX = "/app/account/deletion/"
PATH = re.compile(r"/app/account/deletion/[0-9a-f]{64}/[A-Za-z0-9_-]{43}\Z")


def status_path(path):
    return PATH.fullmatch(path) is not None


async def handle(request):
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import HTMLResponse, JSONResponse

    from tinyassets.broker.deletion_jobs import request as job_request
    from tinyassets.storage import data_dir

    headers = {"cache-control": "no-store", "referrer-policy": "no-referrer",
               "x-robots-tag": "noindex", "content-security-policy": "default-src 'none'"}
    if not status_path(request.url.path):
        return JSONResponse({"error": "not_found"}, status_code=404, headers=headers)
    key, token = request.url.path[len(PREFIX):].split("/")
    try:
        result = await run_in_threadpool(job_request, data_dir(), "status", key=key, token=token)
    except Exception:
        return JSONResponse({"error": "deletion_status_unavailable"}, status_code=503,
                            headers=headers)
    if request.query_params.get("format") == "json":
        return JSONResponse(result, headers=headers)
    complete = result["status"] == "deleted"
    title = "Account deleted" if complete else "Deletion in progress"
    detail = ("Your account deletion is complete." if complete else
              "Your deletion request is recorded. This page updates as each step finishes.")
    progress = f'{len(result["completed_steps"])} of 8 steps complete.'
    refresh = "" if complete else '<meta http-equiv="refresh" content="5">'
    return HTMLResponse(f'<!doctype html><html lang="en"><meta charset="utf-8">'
                        f'<meta name="viewport" content="width=device-width">{refresh}'
                        f'<title>{title}</title><main><h1>{title}</h1><p>{detail}</p>'
                        f'<p>{progress}</p></main></html>', headers=headers)

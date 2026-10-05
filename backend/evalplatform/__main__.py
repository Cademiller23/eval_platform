from __future__ import annotations

import argparse

import uvicorn

from .config import get_settings


def main() -> None:
    s = get_settings()
    ap = argparse.ArgumentParser(prog="evalplatform", description="Model evaluation platform server")
    ap.add_argument("--host", default=s.host)
    ap.add_argument("--port", type=int, default=s.port)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    uvicorn.run("evalplatform.api:app", host=args.host, port=args.port, reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()

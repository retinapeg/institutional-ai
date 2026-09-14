"""Command-line entry points for the demo and local dashboard."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from .api import DEFAULT_MISSION, create_app
from .engine import InstitutionalEngine
from .store import ProjectStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="institutional-ai")
    parser.add_argument("--data-dir", type=Path, default=Path(".institutional-data"))
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run the deterministic institutional mission")
    demo.add_argument("--mission", default=DEFAULT_MISSION)
    inspect = commands.add_parser("inspect", help="print a persisted project snapshot")
    inspect.add_argument("project_id")
    serve = commands.add_parser("serve", help="serve the dashboard and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "demo":
        engine = InstitutionalEngine(ProjectStore(args.data_dir))
        project = engine.create_project(args.mission)
        completed = engine.run(project.id)
        print(
            json.dumps(
                {
                    "project_id": completed.id,
                    "status": completed.status,
                    "workers": len(completed.workers),
                    "reports": len(completed.reports),
                    "reviews": len(completed.reviews),
                    "disagreements": len(completed.director_report.disagreements)
                    if completed.director_report
                    else 0,
                    "path": str(args.data_dir.resolve() / completed.id),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "inspect":
        print(ProjectStore(args.data_dir).load(args.project_id).model_dump_json(indent=2))
        return 0
    if args.command == "serve":
        uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)
        return 0
    raise AssertionError("unreachable")

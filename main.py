from __future__ import annotations

import argparse
import logging

import uvicorn

from ceod.app import create_app
from ceod.container import build_container


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="CEO Delegation Python service")
  subparsers = parser.add_subparsers(dest="command", required=True)

  serve = subparsers.add_parser("serve", help="Run the FastAPI webhook server")
  serve.add_argument("--host", default="0.0.0.0")
  serve.add_argument("--port", type=int, default=8000)

  subparsers.add_parser("daily-followup", help="Run the daily follow-up job once")

  seed = subparsers.add_parser("seed-profiles", help="Create random test members in the Config sheet")
  seed.add_argument("--count", type=int, default=10)

  return parser


def main() -> None:
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
  )

  args = build_parser().parse_args()
  if args.command == "serve":
    uvicorn.run(create_app(), host=args.host, port=args.port)
    return

  container = build_container()
  try:
    if args.command == "daily-followup":
      container.service.daily_follow_up_check()
      logging.getLogger(__name__).info("Daily follow-up run completed")
      return

    if args.command == "seed-profiles":
      profiles = container.service.seed_random_test_profiles(args.count)
      logging.getLogger(__name__).info("Created %s test profiles", len(profiles))
      for profile in profiles:
        logging.getLogger(__name__).info("%s | %s | %s", profile.name, profile.number, profile.sheet_name)
      return
  finally:
    container.close()


if __name__ == "__main__":
  main()


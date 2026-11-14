#!/usr/bin/env python3
"""Run one sample clip from the CLI (image -> video), or verify a Drive reference.

Usage:
    python run_sample.py --dry-run
    python run_sample.py                                  # Gemini image -> a2e video
    python run_sample.py --reference-url https://...       # reuse an existing still
    python run_sample.py --check-url https://drive.google.com/uc?id=...&export=download
    python run_sample.py --quote                          # exact coin cost, free
    python run_sample.py --upload series/frames/x.png     # host an image on a2e
    python run_sample.py --reference-file-id <DRIVE_ID>    # Drive -> a2e reference

The key is read from server/.env. Nothing is spent unless the URL check passes.
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from tools.image_host import verify_image_url  # noqa: E402
from tools.drive_host import build_reference_url  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description="MicroDrama single-clip sample runner")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, spend nothing")
    ap.add_argument("--check-url", help="only verify an image URL is fetchable")
    ap.add_argument("--reference-url", help="existing still URL to animate")
    ap.add_argument("--reference-file-id", help="Google Drive file id to use as reference")
    ap.add_argument("--reference-mode", action="store_true",
                    help="use a2e reference-to-video instead of image-to-video")
    ap.add_argument("--duration", type=int, default=15)
    ap.add_argument("--aspect-ratio", default=os.environ.get("VIDEO_ASPECT_RATIO", "9:16"))
    ap.add_argument("--characters", default="lena",
                    help="comma-separated canon characters to lock identity (default: lena; "
                         "use 'none' to disable)")
    ap.add_argument("--episode", type=int, default=1,
                    help="episode number; selects the costume (Lena: maid Ep 1-4, suit Ep 2+)")
    ap.add_argument("--prompt-file", help="file containing the video prompt")
    ap.add_argument("--quote", action="store_true",
                    help="ask a2e what the render costs (free) and exit")
    ap.add_argument("--upload", metavar="IMAGE",
                    help="host a local image on a2e storage and print the CDN URL")
    args = ap.parse_args()

    ref_url = args.reference_url
    if args.reference_file_id:
        ref_url = build_reference_url(args.reference_file_id)

    if args.upload:
        from tools.a2e_video_generator import A2EVideoGenerator

        result = await A2EVideoGenerator().upload_file(args.upload)
        print(json.dumps(result, indent=2))
        return 0 if result["verified"]["ok"] else 2

    if args.quote:
        from tools.a2e_video_generator import A2EVideoGenerator
        from tools.character_refs import get as get_character

        urls = [ref_url] if ref_url else []
        names = [] if (args.characters or "").lower() == "none" else [
            n.strip().lower() for n in args.characters.split(",") if n.strip()
        ]
        for name in names:
            resolved = get_character(name, episode=args.episode)
            if resolved and resolved.url:
                urls.append(resolved.url)
        gen = A2EVideoGenerator()
        payload = gen.quote_payload(
            prompt="pricing placeholder", video_time=args.duration,
            aspect_ratio=args.aspect_ratio, reference_urls=urls, auto_add_audio=True,
        )
        quote = await gen.quote(payload)
        quote["reference_urls"] = urls
        quote["duration"] = args.duration
        print(json.dumps(quote, indent=2))
        return 0 if quote.get("available") else 1

    if args.check_url or ref_url:
        target = args.check_url or ref_url
        result = await verify_image_url(target)
        print(json.dumps(result, indent=2))
        if not result["ok"]:
            print("\nFAILED: the video provider could not fetch this image. Not spending coins.")
            return 2
        if args.check_url and not args.reference_url and not args.reference_file_id:
            return 0

    prompt = None
    if args.prompt_file:
        prompt = open(args.prompt_file, encoding="utf-8").read()

    from fastapi.testclient import TestClient  # local run only
    import api

    client = TestClient(api.app)
    body = {
        "dry_run": args.dry_run,
        "aspect_ratio": args.aspect_ratio,
        "duration": args.duration,
        "episode": args.episode,
        "model_mode": "reference"
        if (args.reference_mode or ref_url or (args.characters and args.characters.lower() != "none"))
        else "image",
        "reference_image_urls": [ref_url] if ref_url else [],
        "skip_image": bool(ref_url),
        "image_url": ref_url or "",
    }
    if args.characters and args.characters.lower() != "none":
        body["characters"] = [n.strip().lower() for n in args.characters.split(",") if n.strip()]
    if prompt:
        body["prompt"] = prompt

    job_id = client.post("/api/sample", json=body).json()["job_id"]
    print(f"\njob {job_id}")
    seen = -1
    for _ in range(1200):
        events = [json.loads(line[6:]) for line in
                  client.get(f"/api/status/{job_id}").text.splitlines()
                  if line.startswith("data: ")]
        for event in events:
            pct = event.get("progress", -1)
            if pct != seen:
                seen = pct
                msg = event.get("message") or event.get("type")
                print(f"  [{pct:>3}] {event.get('stage', '-'):10s} {msg}")
        if events and events[-1].get("type") in ("complete", "error"):
            last = events[-1]
            print("\n" + json.dumps(last, indent=2))
            return 0 if last.get("type") == "complete" else 1
        await asyncio.sleep(2)
    print("timed out waiting for the job")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

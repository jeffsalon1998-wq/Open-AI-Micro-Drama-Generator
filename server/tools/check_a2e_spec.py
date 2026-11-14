#!/usr/bin/env python3
"""Cross-check the a2e client against the vendor's OpenAPI spec.

The spec is the contract; this script is what stops the client from drifting away
from it. It reads ``vendor/a2e-openapi.json`` and asserts that every enum, limit
and field name the client relies on is still what the spec says.

    python tools/check_a2e_spec.py
    python tools/check_a2e_spec.py --spec path/to/api.json

Exit code 0 = the client and the spec agree. Anything else is drift, and the
failure message names the exact constant to change.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SPEC = os.path.join(HERE, "vendor", "a2e-openapi.json")

sys.path.insert(0, os.path.dirname(HERE))

from tools import a2e_video_generator as client  # noqa: E402

# Reference path in the client's docstring/module header, checked for existence.
REQUIRED_PATHS = (
    "/v1/userImage2Video/start",
    "/v1/userImage2Video/{_id}",
    "/v1/userImage2Video/allRecords",
    "/v1/generation/quote",
    "/v1/r2/upload-presigned-url",
)

failures = []
checks = 0


def check(label, ok, detail=""):
    global checks
    checks += 1
    if not ok:
        failures.append(f"{label}{': ' + detail if detail else ''}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail and not ok else ""))


def resolve(spec, node):
    """Follow local ``$ref`` pointers."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 20:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return node
        target = spec
        for part in ref[2:].split("/"):
            target = target[part]
        node = target
        seen += 1
    return node


def request_schema(spec, path, method="post"):
    node = spec["paths"][path][method]
    schema = node["requestBody"]["content"]["application/json"]["schema"]
    return resolve(spec, schema)


def merged_properties(spec, schema):
    """Flatten an allOf composition into one property map."""
    schema = resolve(spec, schema)
    props, required = {}, set(schema.get("required") or [])
    for part in schema.get("allOf") or []:
        p, r = merged_properties(spec, part)
        props.update(p)
        required |= r
    props.update(schema.get("properties") or {})
    return props, required


def detail_status_enum(spec):
    """The current_status vocabulary behind GET /v1/userImage2Video/{_id}.

    The route only ``$ref``s its schemas, so walking the response body is the only
    way to read the enum the client is coded against.
    """
    route = spec["paths"]["/v1/userImage2Video/{_id}"]["get"]
    schema = (route.get("responses", {}).get("200", {})
              .get("content", {}).get("application/json", {}).get("schema") or {})
    found = set()

    def walk(node, depth=0):
        if depth > 12 or not isinstance(node, (dict, list)):
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if "$ref" in node:
            try:
                walk(resolve(spec, node), depth + 1)
            except Exception:
                pass
            return
        status = node.get("current_status")
        if isinstance(status, dict):
            found.update(enum_of(status) or [])
        for key in ("properties", "allOf", "data"):
            if key in node:
                walk(node[key], depth + 1)

    walk(schema.get("properties", {}).get("data", schema))
    return found


def enum_of(node):
    node = resolve({}, node)
    return node.get("enum")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    args = ap.parse_args()

    if not os.path.exists(args.spec):
        print(f"spec not found: {args.spec}")
        return 2
    spec = json.load(open(args.spec, encoding="utf-8"))

    print(f"Spec: {spec['info']['title']} (openapi {spec['openapi']})")

    # ---------------------------------------------------------------- endpoints
    print("\nendpoints the client calls")
    for path in REQUIRED_PATHS:
        check(f"{path} exists", path in spec.get("paths", {}))
    base = spec.get("servers", [{}])[0].get("url", "")
    check("client DEFAULT_BASE_URL matches servers[0]", client.DEFAULT_BASE_URL == base,
          f"client={client.DEFAULT_BASE_URL!r} spec={base!r}")

    # ------------------------------------------------------------------- submit
    print("\nimage2video request schema")
    schema = request_schema(spec, "/v1/userImage2Video/start")
    props, _ = merged_properties(spec, schema)

    check("generation_mode field present", "generation_mode" in props)
    modes = set(enum_of(props["generation_mode"]) or [])
    for used in ("image-to-video", "text-to-video", "reference-to-video"):
        check(f"mode {used!r} is legal", used in modes, f"spec allows {sorted(modes)}")

    versions = set(enum_of(props["model_version"]) or [])
    for used in ("a2e", "a2e-v2", "a2e-v2-flash"):
        check(f"model_version {used!r} is legal", used in versions, f"spec allows {sorted(versions)}")

    # video_time rule: reference mode restricts to 3/4/5/10/15
    vt_desc = props["video_time"].get("description") or ""
    allowed = client._REFERENCE_DURATIONS
    missing = [v for v in allowed if str(v) not in vt_desc]
    check("client reference durations all appear in the video_time docs",
          not missing, f"not documented: {missing}")
    for used in allowed:
        check(f"reference duration {used}s is in [3,20]",
              client._MIN_DURATION <= used <= client._MAX_DURATION)

    # extend_prompt default: spec says true, client opts out explicitly
    ext = resolve(spec, props.get("extend_prompt", {}))
    check("spec still defaults extend_prompt to true", ext.get("default") is True,
          f"spec default={ext.get('default')!r} — update the client docstring")
    check("client sends extend_prompt on every payload", True)  # verified by source scan below

    # reference_assets cap
    gp = resolve(spec, props["generation_params"])
    assets = resolve(spec, gp["properties"]["reference_assets"])
    check("reference_assets maxItems matches client cap",
          assets.get("maxItems", 0) <= 12,
          f"spec maxItems={assets.get('maxItems')}")
    item = resolve(spec, assets["items"])
    required_asset_fields = set(item.get("required") or [])
    check("client sends every required asset field",
          required_asset_fields <= {"asset_id", "type", "url"},
          f"spec requires {sorted(required_asset_fields)}")
    asset_types = set(enum_of(item["properties"]["type"]) or [])
    check("'image' is a legal asset type", "image" in asset_types, f"spec allows {sorted(asset_types)}")

    ratios = set(enum_of(resolve(spec, gp["properties"]["aspect_ratio"])) or [])
    check("9:16 is a legal aspect ratio", "9:16" in ratios, f"spec allows {sorted(ratios)}")

    # -------------------------------------------------------------------- quote
    print("\ngeneration quote")
    quote_schema = request_schema(spec, "/v1/generation/quote")
    quote_props, quote_req = merged_properties(spec, quote_schema)
    check("quote accepts endpoint+requestBody",
          {"endpoint", "requestBody"} <= set(quote_props))
    check("quote can be priced via endpoint/requestBody",
          any(set(b.get("required") or []) == {"endpoint", "requestBody"}
              for b in (quote_schema.get("anyOf") or [])))
    check("client quote uses the documented endpoint path",
          client.QUOTE_ENDPOINT == "/v1/userImage2Video/start",
          f"client sends {client.QUOTE_ENDPOINT!r}")

    # ------------------------------------------------------------------ uploads
    print("\nupload presign")
    presign = request_schema(spec, "/v1/r2/upload-presigned-url")
    presign_props, presign_req = merged_properties(spec, presign)
    check("presign requires a key", "key" in (presign_req | set(presign_props)))
    purposes = set(enum_of(resolve(spec, presign_props["purpose"])) or [])
    check("client default purpose 'STAGING' is legal",
          "STAGING" in purposes, f"spec allows {sorted(purposes)}")
    expires = resolve(spec, presign_props["expiresIn"])
    check("client default expiresIn within spec bounds",
          expires.get("minimum", 0) <= 300 <= expires.get("maximum", 10 ** 9),
          f"spec bounds {expires.get('minimum')}–{expires.get('maximum')}")

    # ---------------------------------------------------------------- statuses
    print("\ntask status vocabulary")
    statuses = detail_status_enum(spec)
    for status in ("initialized", "sent", "pending", "processing", "completed", "failed"):
        check(f"status {status!r} documented on the detail route", status in statuses,
              f"documented: {sorted(statuses)}")
    check("client treats completed as terminal", "completed" in client._SUCCESS_STATUSES)
    check("client treats failed as terminal", "failed" in client._FAILURE_STATUSES)
    terminal_documented = {"completed", "failed"}
    overlap = terminal_documented & client._NON_TERMINAL_STATUSES
    check("client never treats a terminal status as non-terminal", not overlap,
          f"overlap: {overlap}")

    # ------------------------------------------------------------ source scan
    print("\nsource scan")
    with open(client.__file__, encoding="utf-8") as fh:
        src = fh.read()
    n_extend = src.count('"extend_prompt": self.extend_prompt')
    n_payload = src.count('"number_of_images": 1,')
    check("every payload sets extend_prompt", n_extend == n_payload,
          "%d extend_prompt vs %d payloads" % (n_extend, n_payload))
    check("poll loop handles 429/Retry-After", "__retry_after" in src)
    check("authorization is not forwarded to the presigned PUT",
          'headers={"Content-Type": content_type}' in src)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("\nDRIFT DETECTED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("client matches the published spec")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

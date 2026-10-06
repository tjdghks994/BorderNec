"""A deterministic example of the stdin/stdout local generator interface.

This heuristic demonstrates integration. It is not a language model and
provides no evidence about search performance.
"""
import json
import sys

prompt = sys.stdin.read()
marker = next(marker for marker in ("Domain:\n", "Problem:\n", "State:\n") if marker in prompt)
payload = json.loads(prompt.split(marker, 1)[1])
actions = payload.get("actions", payload.get("currently_applicable_actions"))
refining = "incumbent_foreign_observations" in payload
selected = min(actions, key=lambda item: (
    len(item["data_accesses"]) if refining else -len(item["data_accesses"]),
    item["action_id"],
))
if "currently_applicable_actions" in payload:
    print(json.dumps({"action_id": selected["action_id"]}))
else:
    print(json.dumps({"steps": [selected["action_id"]]}))

"""Does the us. vs global. inference profile prefix matter for turn latency?

Also isolates how much of the turn is Bedrock versus everything else, so the Lambda
question can be answered with a number instead of an opinion.
"""
import importlib.util
import json
import statistics
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue", ROOT / "lambda" / "mantle_dialogue.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")
RUNS = 14

state = M._initial_state("bank")
state["stage"] = "payment_type"
PROMPT = M._classifier_prompt(
    "bank", state, "ชำระเต็มจำนวนค่ะ",
    {"customerName": "สมชาย", "amount": "15,500", "dueDate": "15 สิงหาคม 2569"},
)
SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "intent": {"type": "string"}, "message": {"type": "string"},
        "rawValue": {"type": "string"}, "confidence": {"type": "number"},
    },
    "required": ["intent", "message", "rawValue", "confidence"],
    "additionalProperties": False,
})
OUT = {"textFormat": {"type": "json_schema",
                      "structure": {"jsonSchema": {"name": "c", "schema": SCHEMA}}}}


def measure(model_id, runs=RUNS, **extra):
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        try:
            bedrock.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": PROMPT}]}],
                inferenceConfig={"maxTokens": 220}, **extra,
            )
        except Exception as error:  # noqa: BLE001
            return None, str(error)[:70]
        times.append((time.perf_counter() - started) * 1000)
    return times, None


print(f"{RUNS} runs each, us-west-2, identical prompt and schema\n")
print(f"{'profile':<34} {'p50':>7} {'p90':>7} {'min':>7} {'stdev':>7}")
summary = {}
for model in (
    "us.openai.gpt-5.6-terra", "global.openai.gpt-5.6-terra",
    "us.openai.gpt-5.6-luna", "global.openai.gpt-5.6-luna",
    "us.openai.gpt-5.6-sol", "global.openai.gpt-5.6-sol",
):
    times, error = measure(model, **{"outputConfig": OUT})
    if error:
        print(f"{model:<34} unavailable: {error}")
        continue
    summary[model] = statistics.median(times)
    print(
        f"{model:<34} {statistics.median(times):7.0f} "
        f"{sorted(times)[int(len(times) * 0.9) - 1]:7.0f} {min(times):7.0f} "
        f"{statistics.stdev(times):7.0f}"
    )

if summary:
    best = min(summary, key=summary.get)
    print(f"\nfastest: {best} at {summary[best]:.0f} ms p50")
    for prefix in ("terra", "luna", "sol"):
        u, g = f"us.openai.gpt-5.6-{prefix}", f"global.openai.gpt-5.6-{prefix}"
        if u in summary and g in summary:
            print(f"  {prefix}: us {summary[u]:.0f} vs global {summary[g]:.0f} "
                  f"-> delta {summary[g] - summary[u]:+.0f} ms")

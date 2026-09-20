"""Measure the deployed dialogue Lambda end to end, after the latency changes.

Invokes the live function in us-west-2 and reports which model answered, the model's own
reported latency, and the full round trip. Also confirms the pacing attributes and the
sympathy tag arrive, since those are the contract with the contact flow.
"""
import json
import statistics
import time

import boto3

lam = boto3.client("lambda", region_name="us-west-2")
FUNCTION = "fsi-mantle-dialogue"

BASE = {
    "scenario": "bank",
    "customerName": "สมชาย",
    "amount": "15,500",
    "dueDate": "15 สิงหาคม 2569",
}

# Utterances that do not match a deterministic regex, so the model is actually consulted.
TURNS = [
    "ตกงานเลยยังไม่มีเงินจ่ายเลยค่ะ",
    "ช่วงนี้รายได้ลดลงเยอะ ไม่รู้จะทำยังไงดีค่ะ",
    "สามีเพิ่งเสีย ตอนนี้ยังทำใจไม่ได้ค่ะ",
    "บริการแย่มาก จะร้องเรียนค่ะ",
]


def invoke(transcript, state="{}"):
    payload = json.dumps({
        "sessionState": {
            "sessionAttributes": {**BASE, "mantleState": state},
            "intent": {"name": "FallbackIntent"},
        },
        "inputTranscript": transcript,
    })
    started = time.perf_counter()
    response = lam.invoke(FunctionName=FUNCTION, Payload=payload)
    elapsed = (time.perf_counter() - started) * 1000
    body = json.loads(response["Payload"].read())
    return elapsed, body.get("sessionState", {}).get("sessionAttributes", {})


print("warming the environment first so cold start is not counted as steady state")
invoke("สวัสดีค่ะ")

print(f"\n{'utterance':<40} {'model':<26} {'model ms':>9} {'trip ms':>8}  tag")
trips, model_ms, models = [], [], []
for transcript in TURNS:
    for _ in range(3):
        elapsed, attrs = invoke(transcript)
        trips.append(elapsed)
        reported = attrs.get("modelLatencyMs", "0")
        used = attrs.get("modelUsed", "?")
        models.append(used)
        if reported.isdigit() and int(reported) > 0:
            model_ms.append(int(reported))
    tag = "yes" if "<emotion" in attrs.get("nextPrompt", "") else "no"
    short = used.replace("us.openai.gpt-5.6-", "")
    print(f"{transcript[:38]:<40} {short:<26} {reported:>9} {elapsed:>8.0f}  {tag}")

print()
print(f"round trip   p50 {statistics.median(trips):.0f} ms   min {min(trips):.0f}   max {max(trips):.0f}")
if model_ms:
    print(f"model only   p50 {statistics.median(model_ms):.0f} ms   min {min(model_ms)}   max {max(model_ms)}")
lead = sum(1 for m in models if "terra" in m)
print(f"answered by terra: {lead}/{len(models)}   (terra leading is the point of the reorder)")

_, attrs = invoke("ชำระเต็มจำนวนค่ะ")
print(f"\npacing attributes on the wire: "
      f"{ {k: attrs.get(k) for k in ('eotThreshold', 'eotTimeoutMs', 'allowInterrupt')} }")

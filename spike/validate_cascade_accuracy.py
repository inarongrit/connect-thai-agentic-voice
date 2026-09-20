"""Does leading with TERRA cost classification accuracy?

The reorder was justified on latency and JSON validity. Neither says anything about
whether TERRA picks the right intent as often as LUNA, and a faster wrong answer is worse
than a slower right one. This scores both models on the same labelled Thai turns.

Read-only: invokes models, writes nothing.
"""
import importlib.util
import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue", ROOT / "lambda" / "mantle_dialogue.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

FACTS = {"customerName": "สมชาย", "amount": "15,500", "dueDate": "15 สิงหาคม 2569"}

# (scenario, stage, utterance, acceptable intents). More than one intent is allowed where
# a Thai turn genuinely carries two readings -- scoring a defensible answer as wrong would
# make the comparison useless.
CASES = [
    ("bank", "payment_type", "ตกงานเลยยังไม่มีเงินจ่ายเลยค่ะ", {"hardship", "vulnerability"}),
    ("bank", "payment_type", "ขอลดค่างวดหน่อยได้ไหมคะ", {"hardship"}),
    ("bank", "payment_type", "ไม่สะดวกคุยตอนนี้ ขอให้โทรมาใหม่ค่ะ", {"callback"}),
    ("bank", "payment_type", "ขอคุยกับเจ้าหน้าที่ค่ะ", {"human"}),
    ("bank", "payment_type", "ไม่เคยเป็นหนี้ ยอดนี้ไม่ใช่ของฉันค่ะ", {"dispute"}),
    ("bank", "payment_type", "ไม่สนใจ อย่าโทรมาอีก", {"do_not_contact", "declined"}),
    ("bank", "payment_type", "ไม่ต้องการคุยเรื่องนี้ค่ะ", {"declined", "do_not_contact"}),
    ("bank", "payment_type", "บริการแย่มาก จะร้องเรียนค่ะ", {"complaint"}),
    ("bank", "payment_type", "สามีเพิ่งเสีย ตอนนี้ยังทำใจไม่ได้ค่ะ", {"vulnerability", "hardship"}),
    ("insurance", "qualify_need", "สนใจประกันสุขภาพค่ะ", {"need_health"}),
    ("insurance", "qualify_need", "อยากทำประกันชีวิตให้ลูกค่ะ", {"need_life"}),
    ("insurance", "qualify_need", "สนใจแบบออมเงินค่ะ", {"need_savings"}),
    ("insurance", "qualify_need", "ขอนัดคุยวันเสาร์ตอนบ่ายค่ะ", {"appointment", "callback"}),
    ("broker", "choose_action", "สนใจสัมมนาค่ะ", {"seminar"}),
    ("broker", "choose_action", "อยากคุยกับผู้แนะนำการลงทุนค่ะ", {"consultation"}),
    ("broker", "choose_action", "ควรซื้อหุ้นตัวไหนดีคะ", {"advice_request"}),
]


def run(model_id):
    correct, latencies, parse_failures, details = 0, [], 0, []
    for scenario, stage, utterance, acceptable in CASES:
        state = M._initial_state(scenario)
        state["stage"] = stage
        prompt = M._classifier_prompt(scenario, state, utterance, FACTS)
        try:
            result, elapsed = M._invoke(model_id, prompt)
        except Exception as error:  # noqa: BLE001
            parse_failures += 1
            details.append((utterance, f"ERROR {type(error).__name__}", 0.0))
            continue
        latencies.append(elapsed)
        intent = str(result.get("intent", "")).strip()
        confidence = float(result.get("confidence", 0) or 0)
        hit = intent in acceptable
        correct += hit
        if not hit:
            details.append((utterance, f"{intent} (wanted {'/'.join(sorted(acceptable))})", confidence))
    return correct, latencies, parse_failures, details


print(f"{len(CASES)} labelled Thai turns, one pass each\n")
scores = {}
for label, model_id in (("TERRA (new lead)", M.TERRA_MODEL_ID), ("LUNA (new reviewer)", M.LUNA_MODEL_ID)):
    correct, latencies, failures, details = run(model_id)
    p50 = statistics.median(latencies) if latencies else 0
    scores[label] = (correct, p50)
    print(f"{label:<22} {correct}/{len(CASES)} correct   p50 {p50:6.0f} ms   errors {failures}")
    for utterance, got, confidence in details:
        print(f"    miss: {utterance[:40]:<42} -> {got}  conf {confidence:.2f}")

print()
(terra_correct, terra_p50) = scores["TERRA (new lead)"]
(luna_correct, luna_p50) = scores["LUNA (new reviewer)"]
print(f"accuracy delta (terra - luna): {terra_correct - luna_correct:+d} of {len(CASES)}")
print(f"latency delta  (terra - luna): {terra_p50 - luna_p50:+.0f} ms")
if terra_correct < luna_correct:
    print("\nWARNING: leading with TERRA loses accuracy. The confidence gate still escalates")
    print("to LUNA, but review whether the reorder is worth it.")
else:
    print("\nTERRA leads on latency without losing accuracy on this set.")

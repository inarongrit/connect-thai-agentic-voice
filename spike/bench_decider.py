"""Can Strands Decider replace the LLM intent classifier on Thai turns?

The dialogue engine already answers ~13 of 14 turns with deterministic Thai regexes. The
remainder go to _classify, which asks GPT-5.6 Terra to pick one intent from a closed list
and report a confidence. That is exactly a decision-model task -- pick one of N, with a
score -- so Strands Decider (a 1.9B "system one" model that cannot generate text) is a
plausible drop-in for the intent and confidence half of it.

Three things decide whether it is usable here, and this script measures all three on the
same labelled Thai turns for both models:

  accuracy     does it pick the right intent on Thai input it was not trained on
  calibration  when it is wrong, is its confidence low? Terra reports ~0.99 even when wrong
               (it once mapped an adviser request to "human" at 0.99), so a calibrated
               score is the real prize: it would make the "escalate when unsure" gate work
  latency      measured on whatever hardware runs this, which matters for where it lives

Requires the optional venv with strands-decider and CPU torch; not part of the test suite.
    python3 -m venv ../.decider-venv && ../.decider-venv/bin/pip install strands-decider
    HF_HOME=<cache> ../.decider-venv/bin/python spike/bench_decider.py
"""
import importlib.util
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("mantle_dialogue", ROOT / "lambda" / "mantle_dialogue.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

from strands_decider.infer import load_engine  # noqa: E402
from strands_decider.schema import ChoiceQuestion  # noqa: E402

CHECKPOINT = "StrandsAgents/strands-decider-2B-hobson-v21"

# Option descriptions, in English: the decider reads the option text to score it, and this is
# the same meaning the classifier prompt gives the LLM.
DESCRIBE = {
    "hardship": "cannot afford to pay, lost income or job, asks for relief or more time",
    "vulnerability": "bereavement, serious illness or another personal crisis",
    "complaint": "unhappy with the service or how they were treated",
    "do_not_contact": "asks never to be called or contacted again",
    "callback": "busy now and asks to be called back later",
    "human": "asks to speak to a live staff member or agent",
    "declined": "refuses or is not interested, without asking to stop contact",
    "dispute": "says the debt or the amount is not theirs or is wrong",
    "unknown": "none of the other options; off topic, unclear, or a question not covered",
    "need_health": "interested in health insurance or medical cover",
    "need_life": "interested in life insurance or cover for family",
    "need_savings": "interested in savings or endowment insurance",
    "appointment": "wants to book a time with a licensed agent",
    "product_question": "asks about premiums, terms or what a policy covers",
    "seminar": "interested in the investment seminar",
    "consultation": "wants to talk to or book time with an investment adviser",
    "advice_request": "asks which specific stock or fund to buy",
    "reschedule": "wants to change the delivery date or time",
    "track_order": "asks where the parcel is or when it arrives, without changing it",
    "return_request": "wants to return an item or get a refund",
}

# (scenario, stage, utterance, acceptable intents). The first 16 are the labelled set the
# Terra-first reorder was validated on; the rest are verbatim turns from real calls, which
# are the cases that actually reached the model.
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
    # verbatim from real calls, including ASR's decomposed SARA AM
    ("bank", "payment_type", "ถ้าไม่จ่ายเป็นอะไรไหมครับ", {"unknown"}),
    ("bank", "payment_type", "พอดีตกงานครับไม่มีเงินจ่ายครับต้องทํายังไงได้บ้างครับ", {"hardship"}),
    ("bank", "payment_type", "อ่านผิดไหม มันอ่านโซเชียลไม่ถูก", {"unknown", "complaint"}),
    ("bank", "payment_type", "เดือนนี้คงต้องขอคิดดูก่อนนะคะ", {"unknown", "hardship", "callback"}),
    ("retail", "choose_journey", "คือว่าเมื่อวานกล่องมาบุบนิดหน่อยค่ะ", {"return_request", "complaint"}),
    ("retail", "choose_journey", "ส่งช้ามาก บริการแย่ จะร้องเรียนค่ะ", {"complaint"}),
]


def state_text(scenario, stage, utterance):
    return (f"A {scenario} customer-service phone call in Thai. "
            f"The agent is at the '{stage}' step. The customer said: {utterance}")


def decider_ask(engine, scenario, stage, utterance):
    options = {intent: DESCRIBE.get(intent, intent)
               for intent in sorted(M.ALLOWED_INTENTS[scenario])}
    question = ChoiceQuestion(
        instructions="Which intent best describes what the customer said?",
        criteria=options,
    )
    started = time.perf_counter()
    answer = engine.ask(state_text(scenario, stage, utterance), {"intent": question}).answers["intent"]
    return answer.choice, answer.confidence, (time.perf_counter() - started) * 1000


def terra_ask(scenario, stage, utterance):
    state = M._initial_state(scenario)
    state["stage"] = stage
    prompt = M._classifier_prompt(scenario, state, utterance, {"customerName": "สมชาย"})
    started = time.perf_counter()
    try:
        result, _ = M._invoke(M.TERRA_MODEL_ID, prompt, client=M.bedrock_lead)
        intent, confidence = result.get("intent"), float(result.get("confidence", 0))
    except Exception as error:  # noqa: BLE001
        intent, confidence = f"ERROR:{type(error).__name__}", 0.0
    return intent, confidence, (time.perf_counter() - started) * 1000


def main():
    print(f"loading {CHECKPOINT} on cpu ...", flush=True)
    t0 = time.perf_counter()
    engine = load_engine(CHECKPOINT, device="cpu")
    print(f"loaded in {time.perf_counter() - t0:.0f}s", flush=True)
    decider_ask(engine, "bank", "payment_type", "สวัสดีค่ะ")  # warm-up, not timed

    rows = []
    for scenario, stage, utterance, want in CASES:
        d = decider_ask(engine, scenario, stage, utterance)
        t = terra_ask(scenario, stage, utterance)
        rows.append((utterance, want, d, t))
        print(f"  {utterance[:30]:<32} want={'/'.join(sorted(want)):<24} "
              f"decider={d[0]:<15}{d[1]:.2f} {'ok ' if d[0] in want else 'MISS'}  "
              f"terra={t[0]:<15}{t[1]:.2f} {'ok ' if t[0] in want else 'MISS'}", flush=True)

    print()
    for name, idx in (("decider", 2), ("terra", 3)):
        hits = [r[idx][0] in r[1] for r in rows]
        conf_right = [r[idx][1] for r, h in zip(rows, hits) if h]
        conf_wrong = [r[idx][1] for r, h in zip(rows, hits) if not h]
        lat = [r[idx][2] for r in rows]
        print(f"{name:<8} accuracy {sum(hits)}/{len(rows)}   "
              f"latency p50 {statistics.median(lat):6.0f}ms max {max(lat):6.0f}ms   "
              f"conf when right {statistics.mean(conf_right) if conf_right else 0:.2f}   "
              f"conf when WRONG {statistics.mean(conf_wrong) if conf_wrong else float('nan'):.2f}")
    # The gate that matters: if we only trusted the decider above a threshold, how many
    # turns would it settle, and would any of those be wrong?
    for threshold in (0.6, 0.75, 0.9):
        trusted = [(r[2][0] in r[1]) for r in rows if r[2][1] >= threshold]
        print(f"  decider trusted at >= {threshold}: settles {len(trusted)}/{len(rows)} turns, "
              f"{trusted.count(False)} of them wrong")


if __name__ == "__main__":
    sys.exit(main())

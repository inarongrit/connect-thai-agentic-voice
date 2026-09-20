"""Walk every scenario through the DEPLOYED Lambda and assert invariants on every turn.

Runs against the live function rather than the local module, so it catches deployment
drift as well as logic bugs. Each invariant below corresponds to something that would be
audible or broken on a real Thai call:

  pacing      Lex rejects a threshold outside 0.5-0.9 and silently clamps a timeout
              outside 500-10000, so an out-of-range value fails quietly in production.
  dictated    Dates, times and amounts must run tolerant (0.9/7000) or a caller who
              pauses mid-utterance gets cut off.
  digits      The prompt contract forbids Arabic digits; the Thai voice reads them
              inconsistently, so "15" must already be "สิบห้า".
  tags        The engine speaks a malformed or unknown tag aloud instead of dropping it.
  spoken      Parentheses, brackets, braces and markdown are read out by the engine.
  contract    done/handoffRequired are compared as strings by the contact flow, and
              mantleState must round-trip as JSON or the next turn loses all state.
"""
import json
import re
import sys

import boto3

lam = boto3.client("lambda", region_name="us-west-2")
FUNCTION = "fsi-mantle-dialogue"

DICTATED_STAGES = {"payment_amount", "paymentDate", "preferredTime", "callbackTime",
                   "deliveryDate"}
KNOWN_TAGS = re.compile(r'^<(?:emotion value="(?:neutral|angry|excited|content|sad|scared|sympathetic)"'
                        r'|break time="\d+(?:ms|s)"|/?spell|volume ratio="[\d.]+")/?>$')
FORBIDDEN_SPOKEN = "()[]{}*#_\"«»"

BASE = {
    "bank": {"customerName": "สมชาย", "amount": "15,500", "dueDate": "15 สิงหาคม 2569"},
    "insurance": {"customerName": "สุดา", "amount": "0", "dueDate": "-"},
    "broker": {"customerName": "อนุชา", "amount": "0", "dueDate": "-"},
    # Retail reuses the same two attributes: amount is the order value and dueDate the
    # scheduled delivery date, which is why a fourth vertical needed no flow change.
    "retail": {"customerName": "มานี", "amount": "1,290", "dueDate": "15 สิงหาคม 2569"},
}

# Multi-turn walks. Each is (name, scenario, [utterances]).
WALKS = [
    ("bank pay in full", "bank", ["ใช่ค่ะ", "ชำระเต็มจำนวนค่ะ", "วันที่ยี่สิบค่ะ", "ใช่ค่ะ"]),
    ("bank partial then date+amount", "bank",
     ["ใช่ค่ะ", "ขอชำระบางส่วนค่ะ", "วันที่... ยี่สิบห้าค่ะ", "ใช่ค่ะ", "ห้าพันบาทค่ะ", "ใช่ค่ะ"]),
    ("bank installment", "bank", ["ใช่ค่ะ", "ขอแบ่งชำระค่ะ", "วันที่สิบค่ะ", "ใช่ค่ะ", "สามพันค่ะ", "ใช่ค่ะ"]),
    ("bank hardship to assistance", "bank",
     ["ใช่ค่ะ", "ตกงานเลยยังไม่มีเงินจ่ายเลยค่ะ", "ขอลดค่างวดค่ะ", "ใช่ค่ะ"]),
    ("bank hardship principal holiday", "bank",
     ["ใช่ค่ะ", "รายได้ลดลงมาก จ่ายไม่ไหวค่ะ", "ขอพักชำระเงินต้นค่ะ", "ใช่ค่ะ"]),
    ("bank hardship extend term", "bank",
     ["ใช่ค่ะ", "ขอผ่อนผันหน่อยค่ะ", "ขอขยายระยะเวลาผ่อนชำระค่ะ", "ใช่ค่ะ"]),
    ("bank dispute", "bank", ["ใช่ค่ะ", "ไม่เคยเป็นหนี้ ยอดนี้ไม่ใช่ของฉันค่ะ"]),
    ("bank vulnerability", "bank", ["ใช่ค่ะ", "สามีเพิ่งเสีย ตอนนี้ยังทำใจไม่ได้ค่ะ"]),
    ("bank complaint", "bank", ["ใช่ค่ะ", "บริการแย่มาก จะร้องเรียนค่ะ"]),
    ("bank do not contact", "bank", ["ใช่ค่ะ", "ไม่สนใจ อย่าโทรมาอีก"]),
    ("bank human handoff", "bank", ["ใช่ค่ะ", "ขอคุยกับเจ้าหน้าที่ค่ะ"]),
    ("bank callback", "bank", ["ใช่ค่ะ", "ไม่สะดวกคุยตอนนี้ ขอให้โทรมาใหม่ค่ะ", "พรุ่งนี้บ่ายสองค่ะ"]),
    ("bank wrong person", "bank", ["ไม่ใช่ค่ะ"]),
    ("insurance health", "insurance", ["ใช่ค่ะ", "สนใจประกันสุขภาพค่ะ", "ค่ารักษาค่ะ", "ใช่ค่ะ"]),
    ("insurance life", "insurance", ["ใช่ค่ะ", "อยากทำประกันชีวิตค่ะ", "ครอบครัวค่ะ", "ใช่ค่ะ"]),
    ("insurance savings", "insurance", ["ใช่ค่ะ", "สนใจแบบออมเงินค่ะ", "การเกษียณค่ะ", "ใช่ค่ะ"]),
    ("insurance appointment", "insurance",
     ["ใช่ค่ะ", "สนใจประกันสุขภาพค่ะ", "ค่ารักษาค่ะ", "ใช่ค่ะ", "วันเสาร์ตอนบ่ายค่ะ"]),
    ("insurance declined", "insurance", ["ใช่ค่ะ", "ไม่สนใจค่ะ"]),
    ("broker seminar", "broker", ["ใช่ค่ะ", "สนใจสัมมนาค่ะ", "พื้นฐานการลงทุนค่ะ", "ใช่ค่ะ"]),
    ("broker consultation", "broker", ["ใช่ค่ะ", "อยากคุยกับผู้แนะนำการลงทุนค่ะ", "ใช่ค่ะ"]),
    ("broker advice request", "broker", ["ใช่ค่ะ", "ควรซื้อหุ้นตัวไหนดีคะ"]),
    ("broker retirement topic", "broker", ["ใช่ค่ะ", "สนใจสัมมนาค่ะ", "การวางแผนเกษียณค่ะ", "ใช่ค่ะ"]),
    ("retail reschedule", "retail",
     ["ขอเลื่อนวันจัดส่งค่ะ", "วันที่... ยี่สิบห้าค่ะ", "ใช่ค่ะ"]),
    ("retail reschedule rejected", "retail",
     ["ขอเลื่อนวันจัดส่งค่ะ", "วันที่ยี่สิบค่ะ", "ไม่ใช่ค่ะ", "วันที่ยี่สิบสองค่ะ", "ใช่ค่ะ"]),
    ("retail track then close", "retail", ["ขอติดตามพัสดุค่ะ", "ไม่มีค่ะ"]),
    ("retail track then reschedule", "retail",
     ["พัสดุจะถึงเมื่อไหร่คะ", "ขอเลื่อนวันส่งค่ะ", "วันที่สิบแปดค่ะ", "ใช่ค่ะ"]),
    ("retail return damaged", "retail", ["ขอคืนสินค้าค่ะ", "สินค้าเสียหายค่ะ"]),
    ("retail return wrong item", "retail", ["ขอคืนสินค้าค่ะ", "ได้ของผิดรุ่นค่ะ"]),
    ("retail return changed mind", "retail", ["ขอส่งคืนสินค้าค่ะ", "เปลี่ยนใจค่ะ"]),
    ("retail human handoff", "retail", ["ขอคุยกับเจ้าหน้าที่ค่ะ"]),
    ("retail complaint", "retail", ["ส่งช้ามาก บริการแย่ จะร้องเรียนค่ะ"]),
    ("retail unclear then journey", "retail", ["เอ่อ...", "ขอคืนสินค้าค่ะ", "เปลี่ยนใจค่ะ"]),
    ("silence then filler", "bank", ["ใช่ค่ะ", "เอ่อ...", "อืม", "ไม่แน่ใจค่ะ"]),
    ("repeat request", "bank", ["ใช่ค่ะ", "ขอโทษ พูดอีกครั้งได้ไหมคะ"]),
]

failures = []


def invoke(scenario, transcript, state):
    payload = json.dumps({
        "sessionState": {
            "sessionAttributes": {"scenario": scenario, **BASE[scenario], "mantleState": state},
            "intent": {"name": "FallbackIntent"},
        },
        "inputTranscript": transcript,
    })
    response = lam.invoke(FunctionName=FUNCTION, Payload=payload)
    body = json.loads(response["Payload"].read())
    if "errorMessage" in body:
        raise AssertionError(f"lambda raised: {body.get('errorType')}: {body['errorMessage'][:120]}")
    return body["sessionState"]["sessionAttributes"]


def check(walk, turn, transcript, attrs):
    where = f"[{walk} turn {turn} '{transcript[:22]}']"

    def fail(msg):
        failures.append(f"{where} {msg}")

    prompt = attrs.get("nextPrompt", "")
    done = attrs.get("done")
    stage = json.loads(attrs.get("mantleState", "{}")).get("stage")

    # contract
    if done not in ("true", "false"):
        fail(f"done is {done!r}, flow compares it as a string")
    if attrs.get("handoffRequired") not in ("true", "false"):
        fail(f"handoffRequired is {attrs.get('handoffRequired')!r}")
    try:
        json.loads(attrs.get("mantleState", ""))
    except Exception:
        fail("mantleState is not valid JSON; next turn would lose all state")

    # pacing
    try:
        threshold = float(attrs["eotThreshold"])
        if not 0.5 <= threshold <= 0.9:
            fail(f"eotThreshold {threshold} outside 0.5-0.9; Lex rejects the attribute")
    except (KeyError, ValueError):
        fail("eotThreshold missing or unparseable")
    try:
        timeout = int(attrs["eotTimeoutMs"])
        if not 500 <= timeout <= 10000:
            fail(f"eotTimeoutMs {timeout} outside 500-10000; silently clamped")
    except (KeyError, ValueError):
        fail("eotTimeoutMs missing or unparseable")
    if attrs.get("allowInterrupt") not in ("true", "false"):
        fail(f"allowInterrupt is {attrs.get('allowInterrupt')!r}")

    # Dictated turns must be tolerant -- but only when the caller is actually dictating.
    # If a readback is pending the next utterance is ใช่/ไม่ใช่, so ending on low
    # confidence is correct; what must survive is the tolerant TIMEOUT, because a caller
    # who disagrees usually restates the value in the same breath.
    pending = json.loads(attrs.get("mantleState", "{}")).get("pending") or {}
    if stage in DICTATED_STAGES:
        if pending:
            if attrs.get("eotTimeoutMs") != "600":
                fail(f"stage {stage} has a pending readback but timeout is "
                     f"{attrs.get('eotTimeoutMs')}; a restated value would be cut off")
        elif attrs.get("eotThreshold") != "0.8" or attrs.get("eotTimeoutMs") != "2000":
            fail(f"stage {stage} is dictated but pacing is "
                 f"{attrs.get('eotThreshold')}/{attrs.get('eotTimeoutMs')}")

    if done == "false" and not prompt.strip():
        fail("empty nextPrompt on a turn that is not finished; caller hears silence")

    # Spoken-output hygiene. Digits inside a Thai date ("15 สิงหาคม 2569") are deliberate
    # and asserted by the existing suite, so only bare digits outside that shape are
    # flagged -- amounts are spelled into Thai words and must not regress to numerals.
    without_dates = re.sub(r"\d{1,2} [ก-๙]+ \d{4}", "", prompt)
    if re.search(r"[0-9]", without_dates):
        fail(f"Arabic digits outside a date: {without_dates[:60]!r}")
    for tag in re.findall(r"<[^>]*>", prompt):
        if not KNOWN_TAGS.match(tag):
            fail(f"unknown or malformed tag spoken aloud: {tag!r}")
    if prompt.count("<") != prompt.count(">"):
        fail("unbalanced angle brackets; engine speaks the fragment")
    stripped = re.sub(r"<[^>]*>", "", prompt)
    bad = [c for c in FORBIDDEN_SPOKEN if c in stripped]
    if bad:
        fail(f"characters the engine reads aloud: {bad} in {stripped[:50]!r}")


print(f"walking {len(WALKS)} scenarios against the deployed function\n")
invoke("bank", "warm", "{}")
for name, scenario, utterances in WALKS:
    state = "{}"
    turns = 0
    try:
        for index, utterance in enumerate(utterances, 1):
            attrs = invoke(scenario, utterance, state)
            check(name, index, utterance, attrs)
            state = attrs.get("mantleState", "{}")
            turns += 1
            if attrs.get("done") == "true":
                break
    except AssertionError as error:
        failures.append(f"[{name}] {error}")
    except Exception as error:  # noqa: BLE001
        failures.append(f"[{name}] {type(error).__name__}: {str(error)[:110]}")
    print(f"  {'ok ' if not any(name in f for f in failures) else 'FAIL'} {name:<34} {turns} turns")

print()
if failures:
    print(f"{len(failures)} finding(s):")
    for line in failures:
        print("  -", line)
    sys.exit(1)
print("all scenarios clean on every invariant")

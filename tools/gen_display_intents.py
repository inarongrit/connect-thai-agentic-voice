"""Generate Lex intents and Thai slot types that MIRROR the Python intent vocabulary.

Why this exists
---------------
All real understanding lives in lambda/mantle_dialogue.py: ALLOWED_INTENTS is a set of
Python strings, matched by Thai regexes, and the Lex bot deliberately carries only a
catch-all plus a fallback. That is fast and auditable, but it means opening the Lex console
shows almost nothing, which is a poor story when the point of a demo is to show what the
AWS services do.

These intents exist so the console reflects the design. They are DISPLAY ONLY:

  * No slots. An intent with a required slot makes Lex elicit it, which would take the
    dialogue away from the Python engine. Slot types are emitted as standalone resources
    so they are visible in the console without being wired into an intent.
  * Fulfilment code hook enabled, so if Lex does match one, the turn still goes to the same
    Lambda and the same Python handler drives it.
  * Every generated name is also added to the contact flow's routing conditions. The flow
    previously routed only FallbackIntent and MantleDialogue, and any other intent name hit
    NoMatchingCondition and dropped the caller on error-message. That is the one way this
    change could have broken live calls.

Sample utterances are taken from the same Thai phrases the production regexes match, so the
console shows the real vocabulary rather than invented filler.

Run:  python3 tools/gen_display_intents.py         # writes the template and the flow
"""
import importlib.util
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
IAC = ROOT / "iac"

spec = importlib.util.spec_from_file_location(
    "mantle_dialogue", ROOT / "lambda" / "mantle_dialogue.py"
)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

# Python intent string -> (Lex intent name, Thai description, sample utterances).
# Utterances are real Thai a caller would say, drawn from the matcher patterns.
INTENTS = [
    ("hardship", "Hardship", "ลูกค้าแจ้งว่าผ่อนชำระไม่ไหวหรือขอผ่อนผัน",
     ["ตกงานครับ", "ไม่มีเงินจ่าย", "ขอผ่อนผันหน่อย", "จ่ายไม่ไหวค่ะ", "ขอลดค่างวด"]),
    ("vulnerability", "Vulnerability", "ลูกค้าอยู่ในภาวะเปราะบาง ต้องดูแลเป็นกรณีพิเศษ",
     ["สามีเพิ่งเสีย", "ป่วยหนักอยู่", "เพิ่งผ่าตัด", "ดูแลผู้ป่วยติดเตียง"]),
    ("complaint", "Complaint", "ลูกค้าแจ้งข้อร้องเรียนเรื่องบริการ",
     ["บริการแย่มาก", "จะร้องเรียน", "ไม่พอใจบริการ", "โทรมารบกวนบ่อย"]),
    ("do_not_contact", "DoNotContact", "ลูกค้าขอให้หยุดติดต่อ",
     ["อย่าโทรมาอีก", "ห้ามโทรมา", "ขอยกเลิกการติดต่อ"]),
    ("callback", "Callback", "ลูกค้าขอให้ติดต่อกลับภายหลัง",
     ["ขอให้โทรมาใหม่", "ไม่สะดวกคุยตอนนี้", "โทรมาพรุ่งนี้ได้ไหม"]),
    ("human", "HumanAgent", "ลูกค้าขอคุยกับเจ้าหน้าที่",
     ["ขอคุยกับเจ้าหน้าที่", "ขอสายพนักงาน", "ต่อคนจริงได้ไหม"]),
    ("declined", "Declined", "ลูกค้าปฏิเสธข้อเสนอหรือไม่สนใจ",
     ["ไม่สนใจค่ะ", "ไม่ต้องการ", "ขอบคุณ ไม่เอา"]),
    ("dispute", "Dispute", "ลูกค้าโต้แย้งยอดหนี้",
     ["ยอดนี้ไม่ใช่ของฉัน", "ไม่เคยเป็นหนี้", "ยอดผิด"]),
    ("need_health", "NeedHealth", "ลูกค้าสนใจความคุ้มครองสุขภาพ",
     ["สนใจประกันสุขภาพ", "อยากได้ค่ารักษา", "ประกันโรคร้ายแรง"]),
    ("need_life", "NeedLife", "ลูกค้าสนใจความคุ้มครองชีวิต",
     ["สนใจประกันชีวิต", "อยากทำประกันให้ลูก", "คุ้มครองครอบครัว"]),
    ("need_savings", "NeedSavings", "ลูกค้าสนใจประกันเพื่อการออม",
     ["สนใจแบบออมเงิน", "อยากออมเงิน", "ประกันสะสมทรัพย์"]),
    ("appointment", "Appointment", "ลูกค้านัดหมายกับเจ้าหน้าที่ผู้ได้รับอนุญาต",
     ["นัดวันเสาร์ได้ไหม", "ขอนัดคุยบ่ายนี้", "สะดวกวันจันทร์"]),
    ("product_question", "ProductQuestion", "ลูกค้าถามรายละเอียดผลิตภัณฑ์",
     ["เบี้ยเท่าไหร่", "คุ้มครองอะไรบ้าง", "มีเงื่อนไขอะไร"]),
    ("seminar", "Seminar", "ลูกค้าสนใจรายละเอียดสัมมนาการลงทุน",
     ["สนใจสัมมนา", "ขอรายละเอียดงานสัมมนา", "อยากเข้าอบรม"]),
    ("consultation", "Consultation", "ลูกค้าขอนัดคุยกับผู้แนะนำการลงทุน",
     ["อยากคุยกับผู้แนะนำการลงทุน", "นัดคุยกับที่ปรึกษา"]),
    ("advice_request", "AdviceRequest", "ลูกค้าขอคำแนะนำการลงทุนเฉพาะเจาะจง",
     ["ควรซื้อหุ้นตัวไหน", "ลงทุนอะไรดี", "แนะนำกองทุนหน่อย"]),
    ("reschedule", "Reschedule", "ลูกค้าขอเลื่อนวันจัดส่ง",
     ["ขอเลื่อนวันจัดส่ง", "เปลี่ยนวันส่งได้ไหม", "วันนั้นไม่อยู่บ้าน"]),
    ("track_order", "TrackOrder", "ลูกค้าสอบถามสถานะการจัดส่ง",
     ["พัสดุอยู่ไหน", "ของจะถึงเมื่อไหร่", "ขอติดตามพัสดุ"]),
    ("return_request", "ReturnRequest", "ลูกค้าขอคืนสินค้าหรือคืนเงิน",
     ["ขอคืนสินค้า", "อยากส่งคืน", "ขอคืนเงิน"]),
]

# Closed choice sets, emitted as slot types so the console shows the Thai vocabulary the
# deterministic matchers use. Deliberately NOT attached to any intent -- see the note above.
SLOT_TYPES = [
    ("FsiPaymentMethod", "วิธีชำระที่ลูกค้าเลือก", list(M.BANK_PAYMENT_CHOICES)),
    ("FsiAssistanceOption", "แนวทางช่วยเหลือด้านการชำระ", list(M.BANK_ASSISTANCE_CHOICES)),
    ("RetailJourney", "เรื่องที่ลูกค้าติดต่อเข้ามา", list(M.RETAIL_JOURNEY_CHOICES)),
    ("RetailReturnReason", "สาเหตุการขอคืนสินค้า", list(M.RETAIL_RETURN_CHOICES)),
]

INDENT = " " * 12


def intent_yaml():
    lines = []
    for python_name, lex_name, description, utterances in INTENTS:
        lines.append(f"{INDENT}# mirrors ALLOWED_INTENTS entry {python_name!r}")
        lines.append(f"{INDENT}- Name: {lex_name}")
        lines.append(f"{INDENT}  Description: >-")
        lines.append(f"{INDENT}    {description}")
        lines.append(f"{INDENT}  SampleUtterances:")
        for utterance in utterances:
            lines.append(f"{INDENT}    - Utterance: {utterance}")
        # No slots, so Lex never elicits and the Python engine keeps the dialogue.
        lines.append(f"{INDENT}  FulfillmentCodeHook:")
        lines.append(f"{INDENT}    Enabled: true")
        lines.append(f"{INDENT}    IsActive: true")
    return "\n".join(lines)


def slot_type_yaml():
    lines = []
    for name, description, values in SLOT_TYPES:
        # CloudFormation uses Name here; SlotTypeName is the lexv2-models API spelling
        # and CDK rejects the whole bot with "Supplied properties not correct for
        # CfnBotProps" if you use it.
        lines.append(f"{INDENT}- Name: {name}")
        lines.append(f"{INDENT}  Description: >-")
        lines.append(f"{INDENT}    {description}")
        lines.append(f"{INDENT}  ValueSelectionSetting:")
        # CloudFormation wants the enum form (TOP_RESOLUTION), not the API camel-case
        # TopResolution, which fails AWS::EarlyValidation::PropertyValidation.
        lines.append(f"{INDENT}    ResolutionStrategy: TOP_RESOLUTION")
        lines.append(f"{INDENT}  SlotTypeValues:")
        for value in values:
            lines.append(f"{INDENT}    - SampleValue:")
            lines.append(f"{INDENT}        Value: {value}")
    return "\n".join(lines)


def patch_template():
    path = IAC / "mantle-template.yaml"
    text = path.read_text()
    begin = "# BEGIN GENERATED DISPLAY INTENTS"
    end = "# END GENERATED DISPLAY INTENTS"
    block = (
        f"{INDENT}{begin}\n"
        f"{INDENT}# Generated by tools/gen_display_intents.py -- do not hand-edit.\n"
        f"{intent_yaml()}\n"
        f"{INDENT}{end}"
    )
    if begin in text:
        text = re.sub(
            re.escape(INDENT + begin) + r".*?" + re.escape(INDENT + end),
            block, text, flags=re.S)
    else:
        anchor = f"{INDENT}- Name: FallbackIntent\n"
        if anchor not in text:
            sys.exit("FallbackIntent anchor not found in mantle-template.yaml")
        text = text.replace(anchor, block + "\n" + anchor, 1)

    st_begin = "# BEGIN GENERATED SLOT TYPES"
    st_end = "# END GENERATED SLOT TYPES"
    st_block = (
        f"{INDENT}{st_begin}\n"
        f"{INDENT}# Display only: not referenced by any intent, so Lex never elicits them.\n"
        f"{slot_type_yaml()}\n"
        f"{INDENT}{st_end}"
    )
    if st_begin in text:
        text = re.sub(
            re.escape(INDENT + st_begin) + r".*?" + re.escape(INDENT + st_end),
            st_block, text, flags=re.S)
    else:
        # SlotTypes sits beside Intents under the same locale.
        marker = "          Intents:\n"
        if marker not in text:
            sys.exit("locale Intents marker not found")
        text = text.replace(marker, "          SlotTypes:\n" + st_block + "\n" + marker, 1)
    path.write_text(text)
    print(f"  template: {len(INTENTS)} intents, {len(SLOT_TYPES)} slot types")


def patch_flow():
    """Route every generated intent name back into the normal path.

    This is the part that protects live calls. The Lex blocks previously matched only
    FallbackIntent and MantleDialogue, so a generated intent would have hit
    NoMatchingCondition and dropped the caller on error-message.
    """
    names = ["FallbackIntent", "MantleDialogue"] + [lex for _, lex, _, _ in INTENTS]
    for filename in ("mantle-flow.json", "mantle-inbound-flow.json"):
        path = IAC / filename
        flow = json.loads(path.read_text())
        touched = 0
        for action in flow["Actions"]:
            if action.get("Type") != "ConnectParticipantWithLexBot":
                continue
            transitions = action["Transitions"]
            onward = next(
                (c["NextAction"] for c in transitions.get("Conditions", [])
                 if c["Condition"]["Operands"] == ["FallbackIntent"]), None)
            if onward is None:
                continue
            transitions["Conditions"] = [
                {"NextAction": onward,
                 "Condition": {"Operator": "Equals", "Operands": [name]}}
                for name in names
            ]
            touched += 1
        path.write_text(json.dumps(flow, ensure_ascii=False, indent=2) + "\n")
        print(f"  {filename}: {touched} Lex block(s) now route {len(names)} intent names")


if __name__ == "__main__":
    patch_template()
    patch_flow()
    print("done -- run tools/sync_inline_lambda.py next, then grep to confirm it survived")

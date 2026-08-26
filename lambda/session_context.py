"""Select scenario agents, initialize session state, and persist terminal outcomes."""
import json
import os
import re

import boto3

qconnect = boto3.client("qconnect")
connect = boto3.client("connect")

INSTANCE_ID = os.environ.get("INSTANCE_ID", "")
AGENT_IDS = json.loads(os.environ.get("SCENARIO_AGENT_IDS", "{}"))

_THAI_DIGITS = ("ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า")


def _thai_integer_words(value):
    """Render a non-negative integer as continuous Thai number words."""
    value = int(value)
    if value == 0:
        return _THAI_DIGITS[0]
    if value >= 1_000_000:
        millions, remainder = divmod(value, 1_000_000)
        return _thai_integer_words(millions) + "ล้าน" + (
            _thai_integer_words(remainder) if remainder else ""
        )
    words = []
    for divisor, label in ((100_000, "แสน"), (10_000, "หมื่น"), (1_000, "พัน"), (100, "ร้อย")):
        digit, value = divmod(value, divisor)
        if digit:
            words.append(_THAI_DIGITS[digit] + label)
    tens, units = divmod(value, 10)
    if tens == 1:
        words.append("สิบ")
    elif tens == 2:
        words.append("ยี่สิบ")
    elif tens:
        words.append(_THAI_DIGITS[tens] + "สิบ")
    if units:
        words.append("เอ็ด" if units == 1 and words else _THAI_DIGITS[units])
    return "".join(words)


def _thai_baht_words(amount):
    """Render a decimal currency attribute without exposing punctuation to TTS."""
    raw = re.sub(r"[\s,]", "", str(amount or ""))
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", raw):
        return (str(amount or "ยอดตามที่แจ้ง").strip() + "บาท").replace(" ", "")
    whole_text, dot, fraction = raw.partition(".")
    whole = int(whole_text)
    satang = int((fraction + "00")[:2]) if dot else 0
    spoken = _thai_integer_words(whole) + "บาท"
    return spoken + (_thai_integer_words(satang) + "สตางค์" if satang else "ถ้วน")


BRIEFS = {
    "bank": (
        "ROLE: Collections officer at a bank. CUSTOMER_NAME={name}. "
        "FACTS: AMOUNT_NUMERIC={amount}; AMOUNT_SPOKEN={amount_spoken}; DUE_DATE={due}. "
        "DISCLOSURE: after identity confirmation, begin with ขอบคุณที่ยืนยันตัวนะคะ คุณ followed by CUSTOMER_NAME; then speak AMOUNT_SPOKEN exactly and never vocalize AMOUNT_NUMERIC. "
        "OBJECTIVE: obtain a specific payment commitment with a date. "
        "ALLOWED OPTIONS: full payment, partial payment, or installment plan. "
        "FORBIDDEN: threats, shame, other products, or invented figures."
    ),
    "insurance": (
        "ROLE: Outbound representative at an insurance company. CUSTOMER: {name}. "
        "OBJECTIVE: discover one broad coverage need and one customer priority, share no more than one "
        "approved category fact, recap the customer's own words, then ask permission for a licensed-agent handoff. "
        "GROUNDING: use only approved facts in the AI prompt and customer-provided facts. "
        "ENGAGEMENT: warm and curious through relevance and accurate recap, never hype. "
        "CONDUCT: helpful and concise, one invitation only, accept no immediately, no hard sell, urgency, scarcity, or FOMO. "
        "FORBIDDEN: invented products or benefits, premiums, acceptance promises, closing a sale, loans, or pressure."
    ),
    "broker": (
        "ROLE: Client relations officer at a securities brokerage. CUSTOMER: {name}. "
        "OBJECTIVE: discover the customer's learning topic and general experience before offering tailored seminar "
        "details or a licensed consultation; generic interest never closes the call. "
        "GROUNDING: use only approved educational facts in the AI prompt and customer-provided facts; no live market claims. "
        "ENGAGEMENT: warm and curious through relevance and accurate recap, never hype. "
        "CONDUCT: helpful and concise, one invitation only, accept no immediately, no hard sell, urgency, scarcity, fear, or FOMO. "
        "FORBIDDEN: stock recommendations, returns, price targets, suitability decisions, invented information, loans, or insurance."
    ),
}

INITIAL_STATE = {
    "bank": {
        "version": 2,
        "scenario": "bank",
        "stage": "verify_identity",
        "objective": "verify identity, collect one payment value per turn, and confirm the complete read-back",
        "requiredBeforeClose": [
            "identityConfirmed",
            "paymentType",
            "paymentDate",
            "explicitConfirmation",
        ],
        "outcome": "pending",
        "turnBudget": 9,
        "noiseRecovery": {
            "neverGuess": True,
            "repeatBeforeCallback": 2,
            "fallback": "offer_callback",
        },
    },
    "insurance": {
        "version": 2,
        "scenario": "insurance",
        "stage": "qualify_need",
        "objective": "identify one need and customer priority, obtain handoff permission, then confirm appointment timing",
        "requiredBeforeClose": [
            "productInterest",
            "customerPriority",
            "preferredTime",
            "explicitConfirmation",
        ],
        "outcome": "pending",
        "turnBudget": 9,
        "noiseRecovery": {
            "neverGuess": True,
            "repeatBeforeCallback": 2,
            "fallback": "offer_callback",
        },
    },
    "broker": {
        "version": 2,
        "scenario": "broker",
        "stage": "discover_interest",
        "objective": "discover topic and experience, then send tailored seminar details or confirm licensed-consultation timing",
        "requiredBeforeClose": ["topicInterest", "experienceLevel", "selectedAction"],
        "confirmationRequiredFor": ["consultation"],
        "outcome": "pending",
        "turnBudget": 8,
        "noiseRecovery": {
            "neverGuess": True,
            "repeatBeforeCallback": 2,
            "fallback": "offer_callback",
        },
    },
}


def _parse_session_arn(session_arn):
    tail = session_arn.split(":session/", 1)[1]
    assistant_id, session_id = tail.split("/", 1)
    return assistant_id, session_id


def _session_ids(parameters):
    return _parse_session_arn(parameters.get("sessionArn", ""))


def _clean_attributes(values):
    return {
        key: str(value)[:32767]
        for key, value in values.items()
        if value not in (None, "", "$.Lex.SessionAttributes.undefined")
    }


def _record_outcome(event, parameters):
    scenario = (parameters.get("scenario") or "unknown").lower()
    outcome_type = parameters.get("outcomeType") or "unknown"
    details = _clean_attributes(
        {
            "fsiScenario": scenario,
            "fsiOutcome": outcome_type,
            "fsiOutcomeDetail": parameters.get("outcomeDetail"),
            "fsiPaymentType": parameters.get("paymentType"),
            "fsiPaymentDate": parameters.get("paymentDate"),
            "fsiPaymentAmount": parameters.get("paymentAmount"),
            "fsiProductInterest": parameters.get("productInterest"),
            "fsiPreferredTime": parameters.get("preferredTime"),
            "fsiTopicInterest": parameters.get("topicInterest"),
        }
    )

    session_arn = parameters.get("sessionArn", "")
    if ":session/" in session_arn:
        assistant_id, session_id = _parse_session_arn(session_arn)
        terminal_state = {
            "version": 1,
            "scenario": scenario,
            "stage": "closed",
            "outcome": outcome_type,
            "details": details,
        }
        qconnect.update_session_data(
            assistantId=assistant_id,
            sessionId=session_id,
            data=[
                {
                    "key": "callState",
                    "value": {
                        "stringValue": json.dumps(
                            terminal_state, ensure_ascii=False, separators=(",", ":")
                        )
                    },
                }
            ],
        )

    contact_data = event.get("Details", {}).get("ContactData", {}) or {}
    contact_id = contact_data.get("InitialContactId") or contact_data.get("ContactId")
    if INSTANCE_ID and contact_id and details:
        connect.update_contact_attributes(
            InstanceId=INSTANCE_ID,
            InitialContactId=contact_id,
            Attributes=details,
        )
    return {"status": "ok", "mode": "outcome", "outcome": outcome_type}


def _setup_session(parameters):
    session_arn = parameters.get("sessionArn", "")
    scenario = (parameters.get("scenario") or "bank").lower()
    if scenario not in BRIEFS:
        scenario = "bank"

    name = parameters.get("customerName") or "ลูกค้า"
    amount = parameters.get("amount") or "-"
    amount_spoken = _thai_baht_words(amount)
    due_date = parameters.get("dueDate") or "-"
    assistant_id, session_id = _parse_session_arn(session_arn)

    agent_id = AGENT_IDS.get(scenario)
    if not agent_id:
        raise ValueError(f"missing agent ID for scenario: {scenario}")

    brief = BRIEFS[scenario].format(name=name, amount=amount, amount_spoken=amount_spoken, due=due_date)
    call_state = dict(INITIAL_STATE[scenario])
    call_state["customerName"] = name

    qconnect.update_session(
        assistantId=assistant_id,
        sessionId=session_id,
        aiAgentConfiguration={"SELF_SERVICE": {"aiAgentId": agent_id}},
    )
    qconnect.update_session_data(
        assistantId=assistant_id,
        sessionId=session_id,
        data=[
            {"key": "scenarioBrief", "value": {"stringValue": brief}},
            {"key": "customerName", "value": {"stringValue": name}},
            {
                "key": "callState",
                "value": {
                    "stringValue": json.dumps(
                        call_state, ensure_ascii=False, separators=(",", ":")
                    )
                },
            },
        ],
    )
    return {
        "status": "ok",
        "mode": "setup",
        "scenario": scenario,
        "agentId": agent_id,
        "stage": call_state["stage"],
    }


def handler(event, context):
    parameters = event.get("Details", {}).get("Parameters", {}) or {}
    try:
        if parameters.get("mode") == "outcome":
            return _record_outcome(event, parameters)
        return _setup_session(parameters)
    except (IndexError, ValueError) as error:
        print(f"invalid session setup: {error}")
        return {"status": "skipped", "reason": "invalid session setup"}
    except Exception as error:  # never break the call over context persistence
        print(f"session operation failed: {error}")
        return {"status": "error", "reason": "session operation failed"}

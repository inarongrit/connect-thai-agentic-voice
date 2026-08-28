"""Start either a Thai PSTN callback or an Amazon Connect WebRTC contact."""
import hashlib
import hmac
import json
import os
import re
import secrets as secure_random
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import boto3

connect = boto3.client("connect", region_name=os.environ.get("AWS_REGION", "us-west-2"))

INSTANCE_ID = os.environ["INSTANCE_ID"]
CONTACT_FLOW_ID = os.environ["CONTACT_FLOW_ID"]
SOURCE_PHONE = os.environ["SOURCE_PHONE"]
ORIGIN_SECRET = os.environ.get("ORIGIN_SECRET", "")
MANTLE_CONTACT_FLOW_ID = os.environ.get("MANTLE_CONTACT_FLOW_ID", "")
VOICE_LAB_FLOW_ID = os.environ.get("VOICE_LAB_FLOW_ID", "")

# Voice lab: read arbitrary supplied text aloud in a chosen voice. The text is spoken to
# whoever started the call and is never stored, but it still reaches a paid TTS engine, so
# it is capped and the surrounding fields are allowlisted rather than passed through.
VOICE_LAB_TEXT_LIMIT = 600
VOICE_LAB_ENGINES = {"connect:agentic", "neural", "generative", "standard"}
VOICE_LAB_LANGUAGE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
# A voice name, not free text: the value is only ever read back as a voice identifier, and
# an unknown one makes the flow fall back to its baseline Thai voice.
VOICE_LAB_VOICE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")
MANTLE_ENABLED = os.environ.get("MANTLE_ENABLED", "false").lower() == "true"

VALID_SCENARIOS = {"bank", "insurance", "broker"}
PHONE_RE = re.compile(r"^\+66\d{8,9}$")

SCENARIO_DEFAULTS = {
    "insurance": {"amount": "", "dueDate": ""},
    "broker": {"amount": "", "dueDate": ""},
}

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")
THAI_MONTHS = (
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
)


def _dynamic_bank_facts(current_date=None):
    """Generate one trusted, voice-rich set of Bank facts for a single contact."""
    today = current_date or datetime.now(BANGKOK_TZ).date()
    baht = 10_000 + secure_random.randbelow(90_000)
    satang = 1 + secure_random.randbelow(99)
    days_back = 1 + secure_random.randbelow(5)
    due = today - timedelta(days=days_back)
    return {
        "amount": f"{baht:,}.{satang:02d}",
        "dueDate": f"{due.day} {THAI_MONTHS[due.month - 1]} {due.year + 543}",
    }

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type": "application/json",
}


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, ensure_ascii=False),
    }


def _status_token(contact_id):
    expires = int(time.time()) + 3600
    payload = f"{contact_id}.{expires}"
    signature = hmac.new(ORIGIN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _contact_id_from_token(token):
    try:
        contact_id, expires, supplied_signature = str(token).split(".", 2)
        payload = f"{contact_id}.{expires}"
        expected_signature = hmac.new(ORIGIN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if int(expires) < int(time.time()) or not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("invalid status token")
        if not re.fullmatch(r"[a-f0-9-]{36}", contact_id):
            raise ValueError("invalid status token")
        return contact_id
    except (TypeError, ValueError):
        raise ValueError("invalid status token") from None


def _contact_status(body):
    contact_id = _contact_id_from_token(body.get("statusToken", ""))
    contact = connect.describe_contact(InstanceId=INSTANCE_ID, ContactId=contact_id)["Contact"]
    state = str(contact.get("State", "")).upper()
    ended = bool(contact.get("DisconnectTimestamp")) or state in {"ENDED", "MISSED", "ERROR", "REJECTED"}
    return _resp(200, {
        "status": "completed" if ended else "active",
        "state": state or None,
        "connected": bool(contact.get("ConnectedToSystemTimestamp")),
    })


FEEDBACK_TABLE = os.environ.get("FEEDBACK_TABLE", "")
RATING_FIELDS = ("overall", "voice", "understanding", "relevance", "latency")
COMPLETION_VALUES = {"yes", "partial", "no"}

GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GITHUB_TOKEN_SECRET = os.environ.get("GITHUB_TOKEN_SECRET", "")
GITHUB_FEEDBACK_ENABLED = os.environ.get("GITHUB_FEEDBACK_ENABLED", "false").lower() == "true"
# Which generation of dialogue policy was deployed when this feedback was taken.
# Ratings collected before and after a policy change are not comparable, so the
# cohort has to be recorded with the rating itself.
POLICY_VERSION = os.environ.get("DIALOGUE_POLICY_VERSION", "v2")
# Option A enforces policy in code; Option B is instructed through prompts. The
# Option A (mantle) is the default and the only engine offered in the web UI;
# Option B stays callable server-side for internal comparison only.
ENFORCEMENT = {"mantle": "deterministic", "managed": "prompt-instructed"}

RATING_LABELS = {
    "overall": "ความพึงพอใจโดยรวม",
    "voice": "ความเป็นธรรมชาติของเสียง",
    "understanding": "ระบบเข้าใจคำพูด",
    "relevance": "คำตอบตรงประเด็น",
    "latency": "ความเร็วในการตอบ",
}
BRAIN_LABELS = {"managed": "ตัวเลือก B", "mantle": "ตัวเลือก A"}
BRAIN_TAGS = {"managed": "option-b", "mantle": "option-a"}

dynamodb = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "us-west-2"))
secrets = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-west-2"))

_github_token = None


def _github_token_value():
    global _github_token
    if _github_token is None:
        _github_token = secrets.get_secret_value(SecretId=GITHUB_TOKEN_SECRET)["SecretString"].strip()
    return _github_token


# The GitHub mirror may be a PUBLIC repository, and the comment box is free text:
# a tester can type their own phone number, national ID or email into it. The
# DynamoDB record keeps the raw comment because that store is access-controlled
# and needed for analysis; only the public copy is redacted.
# Threshold is 9+ digits so an ISO date (8 digits) is not mangled, while Thai
# mobile numbers (10), bank accounts (10-12) and national IDs (13) are caught.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_NUMBER_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


def _redact_for_public(comment):
    """Strip contact details a tester may have typed into the comment box."""
    if not comment:
        return comment
    redacted = _EMAIL_RE.sub("[อีเมลถูกซ่อน]", comment)

    def _mask(match):
        digits = sum(character.isdigit() for character in match.group())
        return "[หมายเลขถูกซ่อน]" if digits >= 9 else match.group()

    return _LONG_NUMBER_RE.sub(_mask, redacted)


def _issue_body(contact_id, ratings, completed, comment, context, timing):
    lines = [
        "### บริบทของสายที่ทดสอบ",
        "",
        "| รายการ | ค่า |",
        "| --- | --- |",
        f"| เวลาเริ่มสาย | {timing.get('callStartedAt', '-')} |",
        f"| ระยะเวลา (วินาที) | {timing.get('durationSeconds', '-')} |",
        f"| สถานการณ์ | {context.get('scenario', 'unknown')} |",
        f"| ช่องทาง | {context.get('channelMode', 'unknown')} |",
        f"| ชุดสนทนา | {BRAIN_LABELS.get(context.get('brainMode'), 'ไม่ระบุ')} |",
        f"| Contact ID | `{contact_id}` |",
        "",
        "### คะแนน",
        "",
        "| หัวข้อ | คะแนน |",
        "| --- | --- |",
    ]
    for field in RATING_FIELDS:
        if field in ratings:
            lines.append(f"| {RATING_LABELS[field]} | {ratings[field]} / 5 |")
    if completed:
        lines += ["", f"**ทำงานสำเร็จ:** {completed}"]
    lines += ["", "### ความเห็นของผู้ทดสอบ", "", comment or "_ไม่มีความเห็นเพิ่มเติม_"]
    lines += ["", "---", "_สร้างอัตโนมัติจากแบบฟอร์มความเห็นของเดโม ผู้ทดสอบไม่ระบุตัวตน_"]
    return "\n".join(lines)


def _mirror_to_github(contact_id, ratings, completed, comment, context, timing):
    """Best effort. Feedback is already stored in DynamoDB before this runs."""
    if not (GITHUB_FEEDBACK_ENABLED and GITHUB_REPO and GITHUB_TOKEN_SECRET):
        return None
    import urllib.error
    import urllib.request

    brain = context.get("brainMode", "unknown")
    title = (
        f"[Feedback] {context.get('scenario', 'unknown')} · "
        f"{BRAIN_LABELS.get(brain, 'ไม่ระบุ')} · {context.get('channelMode', 'unknown')} · "
        f"overall {ratings.get('overall', '-')}/5"
    )
    labels = ["demo-feedback"]
    if brain in BRAIN_TAGS:
        labels.append(BRAIN_TAGS[brain])

    payload = json.dumps(
        {
            "title": title[:250],
            "body": _issue_body(contact_id, ratings, completed, comment, context, timing),
            "labels": labels,
        }
    ).encode()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/issues",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {_github_token_value()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "fsi-outbound-demo-feedback",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            return json.loads(response.read()).get("number")
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"GitHub mirror failed: {error}")
        return None


def _rating(value, field):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a whole number from 1 to 5") from None
    if not 1 <= number <= 5:
        raise ValueError(f"{field} must be from 1 to 5")
    return number


def _feedback(body, event):
    if not FEEDBACK_TABLE:
        return _resp(503, {"error": "feedback collection is not configured"})
    contact_id = _contact_id_from_token(body.get("statusToken", ""))

    ratings = {}
    for field in RATING_FIELDS:
        score = _rating(body.get(field), field)
        if score is not None:
            ratings[field] = score
    if "overall" not in ratings:
        raise ValueError("overall rating is required")

    completed = str(body.get("completed", "")).strip().lower()
    if completed and completed not in COMPLETION_VALUES:
        raise ValueError("completed must be yes, partial, or no")
    comment = str(body.get("comment", "")).strip()[:1000]
    tester = str(body.get("testerRole", "")).strip()[:60]

    context = {}
    try:
        context = connect.get_contact_attributes(
            InstanceId=INSTANCE_ID, InitialContactId=contact_id
        ).get("Attributes", {}) or {}
    except Exception as error:  # noqa: BLE001
        print(f"Feedback context lookup failed: {error}")

    timing = {}
    try:
        contact = connect.describe_contact(InstanceId=INSTANCE_ID, ContactId=contact_id)["Contact"]
        started, ended = contact.get("InitiationTimestamp"), contact.get("DisconnectTimestamp")
        if started:
            timing["callStartedAt"] = started.strftime("%Y-%m-%dT%H:%M:%SZ")
        if ended:
            timing["callEndedAt"] = ended.strftime("%Y-%m-%dT%H:%M:%SZ")
        if started and ended:
            timing["durationSeconds"] = str(max(0, int((ended - started).total_seconds())))
    except Exception as error:  # noqa: BLE001
        print(f"Feedback timing lookup failed: {error}")

    item = {
        "contactId": {"S": contact_id},
        "submittedAt": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        "brainMode": {"S": context.get("brainMode") or "unknown"},
        "channelMode": {"S": context.get("channelMode") or "unknown"},
        "scenario": {"S": context.get("scenario") or "unknown"},
        "policyVersion": {"S": POLICY_VERSION},
        "enforcement": {"S": ENFORCEMENT.get(context.get("brainMode") or "", "unknown")},
    }
    if context.get("customerName"):
        item["customerName"] = {"S": context["customerName"][:50]}
    for key, value in timing.items():
        item[key] = {"N": value} if key == "durationSeconds" else {"S": value}
    for field, score in ratings.items():
        item[field] = {"N": str(score)}
    if completed:
        item["completed"] = {"S": completed}
    if comment:
        item["comment"] = {"S": comment}
    if tester:
        item["testerRole"] = {"S": tester}

    dynamodb.put_item(TableName=FEEDBACK_TABLE, Item=item)

    # Public mirror gets the redacted comment; DynamoDB above kept the raw text.
    issue = _mirror_to_github(contact_id, ratings, completed, _redact_for_public(comment), context, timing)
    if issue:
        try:
            dynamodb.update_item(
                TableName=FEEDBACK_TABLE,
                Key={"contactId": item["contactId"], "submittedAt": item["submittedAt"]},
                UpdateExpression="SET githubIssue = :n",
                ExpressionAttributeValues={":n": {"N": str(issue)}},
            )
        except Exception as error:  # noqa: BLE001
            print(f"Could not record GitHub issue number: {error}")

    return _resp(
        200,
        {
            "status": "recorded",
            "issue": issue,
            "context": {
                "brainMode": item["brainMode"]["S"],
                "channelMode": item["channelMode"]["S"],
                "scenario": item["scenario"]["S"],
                "customerName": context.get("customerName") or "",
                "callStartedAt": timing.get("callStartedAt", ""),
                "durationSeconds": timing.get("durationSeconds", ""),
            },
        },
    )


def _attributes(body):
    scenario = str(body.get("scenario", "")).strip().lower()
    name = str(body.get("name", "")).strip()[:50] or "ลูกค้า"
    if scenario not in VALID_SCENARIOS:
        raise ValueError("scenario must be one of: bank, insurance, broker")
    defaults = _dynamic_bank_facts() if scenario == "bank" else SCENARIO_DEFAULTS[scenario]
    return name, {
        "scenario": scenario,
        "customerName": name,
        "amount": defaults["amount"],
        "dueDate": defaults["dueDate"],
        "channelMode": str(body.get("mode", "pstn"))[:16],
        "brainMode": str(body.get("brainMode", "mantle"))[:16],
    }


def _start_webrtc(name, attributes, flow_id, brain_mode):
    result = connect.start_web_rtc_contact(
        InstanceId=INSTANCE_ID,
        ContactFlowId=flow_id,
        ParticipantDetails={"DisplayName": name},
        Attributes=attributes,
    )
    return _resp(
        200,
        {
            "mode": "webrtc",
            "brainMode": brain_mode,
            "contactId": result["ContactId"],
            "statusToken": _status_token(result["ContactId"]),
            "participantId": result["ParticipantId"],
            "participantToken": result["ParticipantToken"],
            "connectionData": result["ConnectionData"],
        },
    )


def _start_pstn(body, attributes, flow_id, brain_mode):
    phone = str(body.get("phone", "")).strip().replace(" ", "").replace("-", "")
    if re.match(r"^0\d{8,9}$", phone):
        phone = "+66" + phone[1:]
    if not PHONE_RE.match(phone):
        return _resp(400, {"error": "invalid phone number - Thai (+66) numbers only"})
    try:
        result = connect.start_outbound_voice_contact(
            DestinationPhoneNumber=phone,
            ContactFlowId=flow_id,
            InstanceId=INSTANCE_ID,
            SourcePhoneNumber=SOURCE_PHONE,
            Attributes=attributes,
        )
    except connect.exceptions.OutboundContactNotPermittedException:
        return _resp(403, {"error": "outbound call not permitted to this number"})
    return _resp(
        200,
        {
            "mode": "pstn",
            "brainMode": brain_mode,
            "contactId": result["ContactId"],
            "statusToken": _status_token(result["ContactId"]),
            "message": "calling " + phone,
        },
    )


def _voice_lab(body):
    """Start a WebRTC call that reads the supplied script aloud, then ends."""
    if not VOICE_LAB_FLOW_ID:
        return _resp(403, {"error": "voice lab is not enabled"})
    text = str(body.get("text", "")).strip()
    if not text:
        return _resp(400, {"error": "text is required"})
    if len(text) > VOICE_LAB_TEXT_LIMIT:
        return _resp(400, {"error": f"text must be {VOICE_LAB_TEXT_LIMIT} characters or fewer"})
    voice = str(body.get("voice", "SUDA")).strip()
    if not VOICE_LAB_VOICE_RE.match(voice):
        return _resp(400, {"error": "voice name is not valid"})
    engine = str(body.get("engine", "connect:agentic")).strip()
    if engine not in VOICE_LAB_ENGINES:
        return _resp(400, {"error": "engine must be one of: " + ", ".join(sorted(VOICE_LAB_ENGINES))})
    language = str(body.get("language", "th-TH")).strip()
    if not VOICE_LAB_LANGUAGE_RE.match(language):
        return _resp(400, {"error": "language must look like th-TH"})
    result = connect.start_web_rtc_contact(
        InstanceId=INSTANCE_ID,
        ContactFlowId=VOICE_LAB_FLOW_ID,
        ParticipantDetails={"DisplayName": "Voice lab"},
        Attributes={
            "labText": text,
            "labVoice": voice,
            "labEngine": engine,
            "labLanguage": language,
            "channelMode": "webrtc",
            "scenario": "voicelab",
        },
    )
    return _resp(200, {
        "mode": "webrtc",
        "voice": voice,
        "engine": engine,
        "language": language,
        "contactId": result["ContactId"],
        "participantId": result["ParticipantId"],
        "participantToken": result["ParticipantToken"],
        "connectionData": result["ConnectionData"],
    })


def handler(event, context):
    headers = {str(key).lower(): str(value) for key, value in (event.get("headers") or {}).items()}
    supplied_secret = headers.get("x-fsi-origin-key", "")
    if not ORIGIN_SECRET or not hmac.compare_digest(supplied_secret, ORIGIN_SECRET):
        return _resp(403, {"error": "forbidden"})

    method = (event.get("requestContext", {}).get("http", {}) or {}).get("method", "")
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid JSON"})

    if str(body.get("action", "")).strip().lower() == "status":
        try:
            return _contact_status(body)
        except ValueError:
            return _resp(403, {"error": "invalid status token"})
        except Exception as error:  # noqa: BLE001
            print(f"Contact status failed: {error}")
            return _resp(500, {"error": "failed to read call status"})

    if str(body.get("action", "")).strip().lower() == "voicelab":
        try:
            return _voice_lab(body)
        except Exception as error:  # noqa: BLE001
            print(f"Voice lab start failed: {error}")
            return _resp(500, {"error": "failed to start voice preview"})

    if str(body.get("action", "")).strip().lower() == "feedback":
        try:
            return _feedback(body, event)
        except ValueError as error:
            message = str(error)
            if "status token" in message:
                return _resp(403, {"error": message})
            return _resp(400, {"error": message})
        except Exception as error:  # noqa: BLE001
            print(f"Feedback save failed: {error}")
            return _resp(500, {"error": "failed to save feedback"})

    mode = str(body.get("mode", "pstn")).strip().lower()
    if mode not in {"pstn", "webrtc"}:
        return _resp(400, {"error": "mode must be pstn or webrtc"})
    brain_mode = str(body.get("brainMode", "mantle")).strip().lower()
    if brain_mode not in {"managed", "mantle"}:
        return _resp(400, {"error": "brainMode must be managed or mantle"})
    if brain_mode == "mantle" and (not MANTLE_ENABLED or not MANTLE_CONTACT_FLOW_ID):
        return _resp(403, {"error": "mantle dialogue path is not enabled"})
    flow_id = MANTLE_CONTACT_FLOW_ID if brain_mode == "mantle" else CONTACT_FLOW_ID
    body["brainMode"] = brain_mode
    try:
        name, attributes = _attributes(body)
        if mode == "webrtc":
            return _start_webrtc(name, attributes, flow_id, brain_mode)
        return _start_pstn(body, attributes, flow_id, brain_mode)
    except ValueError as error:
        return _resp(400, {"error": str(error)})
    except Exception as error:  # noqa: BLE001
        print(f"Start {mode} contact failed: {error}")
        return _resp(500, {"error": "failed to start call"})

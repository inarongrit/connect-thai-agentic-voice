"""Serves the in-progress Thai transcript of a live call to the presenter's panel.

Amazon Connect has no built-in live transcript in the agent workspace. The CCP shows a
transcript only during After Contact Work, and the documented way to see one mid-call is
to consume Contact Lens real-time analysis and render it yourself. This is the smallest
version of that: poll the analysis segments for the call currently in progress and hand
them back as JSON.

Access control matters more here than anywhere else in the demo, because the payload is
the verbatim content of a phone call. The endpoint sits behind the same CloudFront
origin-secret check as the outbound trigger: CloudFront injects X-FSI-Origin-Key, and a
request without it is refused. Reaching the API directly therefore fails, and the
transcript is never exposed to the open internet.

Transcripts are returned unredacted because Contact Lens does not support redaction for
Thai in any mode. That is a platform limitation, so this endpoint must be treated as
handling sensitive content: demo calls only, never real customers.
"""

import hmac
import json
import os

import boto3

INSTANCE_ID = os.environ.get("CONNECT_INSTANCE_ID", "")
ROUTING_PROFILE_ID = os.environ.get("ROUTING_PROFILE_ID", "")
ORIGIN_SECRET = os.environ.get("ORIGIN_SECRET", "")

# Thai has no sentiment analysis, so only the transcript itself is meaningful here.
MAX_SEGMENTS = 200

_connect = boto3.client("connect")
_lens = boto3.client("connect-contact-lens")

# The panel labels turns rather than showing raw participant codes, so the audience can
# follow who is speaking without knowing Connect's vocabulary.
SPEAKER_TH = {
    "CUSTOMER": "ลูกค้า",
    "AGENT": "เจ้าหน้าที่",
    "SYSTEM": "ระบบ",
}


def _authorised(event):
    """Only requests that came through CloudFront are served.

    Compared with compare_digest rather than == so the check does not leak the secret
    through timing, matching how the outbound trigger validates the same header.
    """
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    supplied = headers.get("x-fsi-origin-key", "")
    return bool(ORIGIN_SECRET) and hmac.compare_digest(supplied, ORIGIN_SECRET)


# States that mean the agent is genuinely on a call. A contact can linger on an agent
# after it ends -- one stayed attached for an hour with no state at all -- and following
# it means the panel reports on a call that finished.
LIVE_STATES = {"CONNECTING", "CONNECTED", "ON_HOLD", "ENDED", "MISSED", "ERROR",
               "CONNECTED_ONHOLD", "INCOMING", "PENDING"}
ACTIVE_STATES = {"CONNECTING", "CONNECTED", "ON_HOLD", "CONNECTED_ONHOLD", "INCOMING"}


def _active_contact():
    """The call the agent is on right now.

    The panel is opened beside the CCP during a demo, so it should follow whatever call
    is live without anyone pasting a contact id. Returns None when the agent is idle,
    which is the ordinary state between calls rather than an error.

    A contact still attached but not in an active state is skipped: a finished call stayed
    on the agent long after it ended, and the panel kept reporting an error about it.
    """
    if not (INSTANCE_ID and ROUTING_PROFILE_ID):
        return None
    try:
        data = _connect.get_current_user_data(
            InstanceId=INSTANCE_ID,
            Filters={"RoutingProfiles": [ROUTING_PROFILE_ID]},
        )
    except Exception as exc:
        print(f"agent lookup failed: {type(exc).__name__}: {exc}")
        return None
    candidates = [c for user in data.get("UserDataList", [])
                  for c in user.get("Contacts", []) if c.get("ContactId")]
    for contact in candidates:
        if str(contact.get("ContactState", "")).upper() in ACTIVE_STATES:
            return contact["ContactId"]
    # Nothing in an active state. Fall back to a single attached contact so a state this
    # code has not seen still shows something, but ignore leftovers when there are several.
    return candidates[0]["ContactId"] if len(candidates) == 1 else None


def _segments(contact_id):
    """Transcript turns for a call that is still in progress.

    Contact Lens exposes these only while the contact is live; once it ends the call
    moves to the post-call record, so an empty result late in a call is expected rather
    than a fault.
    """
    turns = []
    token = None
    while True:
        request = {"InstanceId": INSTANCE_ID, "ContactId": contact_id, "MaxResults": 100}
        if token:
            request["NextToken"] = token
        try:
            page = _lens.list_realtime_contact_analysis_segments(**request)
        except Exception as exc:
            # "Real-time contact analysis not found" means this call has no analysis --
            # either it has not started producing segments, or it predates real-time
            # analytics being enabled. That is a waiting state, not a failure, and
            # reporting it as an error left the panel showing เกิดข้อผิดพลาด on a
            # perfectly healthy system.
            if "ResourceNotFoundException" in type(exc).__name__ or \
                    "not found" in str(exc).lower():
                print(f"no real-time analysis yet for {contact_id}")
                return turns, None
            print(f"segment fetch failed: {type(exc).__name__}: {exc}")
            return turns, str(exc)[:160]
        for segment in page.get("Segments", []):
            transcript = segment.get("Transcript")
            if not transcript or not transcript.get("Content"):
                continue
            role = transcript.get("ParticipantRole", "SYSTEM")
            turns.append({
                "speaker": SPEAKER_TH.get(role, role),
                "role": role,
                "text": transcript["Content"],
                "offsetMillis": transcript.get("BeginOffsetMillis", 0),
            })
        token = page.get("NextToken")
        if not token or len(turns) >= MAX_SEGMENTS:
            break
    turns.sort(key=lambda t: t["offsetMillis"])
    return turns[:MAX_SEGMENTS], None


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8",
                    "Cache-Control": "no-store"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def handler(event, context):
    if not _authorised(event):
        return _response(403, {"error": "forbidden"})

    parameters = event.get("queryStringParameters") or {}
    contact_id = (parameters.get("contactId") or "").strip() or _active_contact()
    if not contact_id:
        # Idle is a normal state; the panel shows a waiting message rather than an error.
        return _response(200, {"contactId": None, "status": "idle", "turns": []})

    turns, error = _segments(contact_id)
    return _response(200, {
        "contactId": contact_id,
        "status": "error" if error else ("live" if turns else "waiting"),
        "detail": error,
        "turns": turns,
    })

"""Post-contact analyzer for Thai agentic-voice self-service calls.

Triggered when Contact Lens writes an analysis file to
s3://<recordings bucket>/Analysis/Voice/... It reads the Thai transcript and:

  1. classifies sentiment, customer signal and recommended outcome with Bedrock,
  2. optionally cross-checks sentiment via Translate + Comprehend,
  3. detects a no-progress loop deterministically from repeated agent turns,
  4. writes the results back as Connect contact attributes,
  5. submits a Connect evaluation so the result is visible in the console.

Thai has no native Contact Lens sentiment, summaries, pattern-match rules or
automated evaluations, so this fills those gaps without touching live dialogue
behaviour. Nothing here runs during a call.
"""

import json
import os
import re

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-west-2")
INSTANCE_ID = os.environ["INSTANCE_ID"]
EVALUATION_FORM_ID = os.environ.get("EVALUATION_FORM_ID", "")
EVALUATOR_USER_ARN = os.environ.get("EVALUATOR_USER_ARN", "")
PRIMARY_MODEL_ID = os.environ.get("PRIMARY_MODEL_ID", "us.openai.gpt-5.6-luna")
FALLBACK_MODEL_ID = os.environ.get("FALLBACK_MODEL_ID", "us.openai.gpt-5.6-terra")
ENABLE_COMPREHEND = os.environ.get("ENABLE_COMPREHEND", "true").lower() == "true"

s3 = boto3.client("s3", region_name=REGION)
connect = boto3.client("connect", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
translate = boto3.client("translate", region_name=REGION)
comprehend = boto3.client("comprehend", region_name=REGION)
events = boto3.client("events", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)
cloudwatch = boto3.client("cloudwatch", region_name=REGION)

# Amazon Connect rules on OnContactEvaluationSubmit cannot test a form score or a
# question answer in this instance's API version, so the consequence of a low score
# is raised here where the score and the answer are already known.
EVENT_SOURCE = "fsi.demo.analyzer"
METRIC_NAMESPACE = "FSIDemo/Analyzer"
# A CloudWatch alarm fires on a metric and therefore cannot name the contact.
# The actionable alert is published here, where the contact id is known.
ALERT_TOPIC_ARN = os.environ.get("ALERT_TOPIC_ARN", "")
CONSOLE_BASE_URL = os.environ.get("CONSOLE_BASE_URL", "")

SENTIMENTS = {"POSITIVE", "NEUTRAL", "NEGATIVE", "MIXED"}
SIGNALS = {
    "hardship_financial_difficulty",
    "payment_arrangement_request",
    "dispute",
    "refusal_to_pay",
    "callback_request",
    "vulnerability",
    "none",
}
OUTCOMES = {
    "payment_assistance_referral",
    "partial_payment_proposal",
    "temporary_deferral_request",
    "payment_commitment",
    "declined",
    "callback",
    "dispute",
    "human_transfer",
}
ESCALATION_SIGNALS = {
    "hardship_financial_difficulty",
    "payment_arrangement_request",
    "vulnerability",
}
CONTACT_ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")

PROMPT = (
    "You analyse one Thai financial-services self-service call transcript. "
    "Return ONLY a JSON object with keys sentiment, sentimentScore, primarySignal, "
    "recommendedOutcome, rationaleThai.\n"
    "sentiment must be POSITIVE, NEUTRAL, NEGATIVE or MIXED.\n"
    "sentimentScore is the confidence between 0 and 1.\n"
    f"primarySignal must be one of: {', '.join(sorted(SIGNALS))}.\n"
    f"recommendedOutcome must be one of: {', '.join(sorted(OUTCOMES))}.\n"
    "rationaleThai is one short Thai sentence. Never invent facts that are not in "
    "the transcript.\n\nTRANSCRIPT:\n"
)


def _contact_id(key, analysis):
    metadata = analysis.get("CustomerMetadata") or {}
    if metadata.get("ContactId"):
        return metadata["ContactId"]
    match = CONTACT_ID_RE.search(key)
    return match.group(1) if match else ""


def _turns(analysis):
    result = []
    for entry in analysis.get("Transcript") or []:
        content = (entry.get("Content") or "").strip()
        if not content:
            continue
        who = entry.get("ParticipantId") or entry.get("ParticipantRole") or "UNKNOWN"
        result.append((who, content))
    return result


def _classify(conversation):
    """Ask Bedrock for sentiment and signal, falling back to the second model.

    Do not add a temperature to inferenceConfig: these models reject it with
    "This model doesn't support the temperature field", which silently disables
    classification for every contact.
    """
    errors = []
    for model_id in (PRIMARY_MODEL_ID, FALLBACK_MODEL_ID):
        try:
            response = bedrock.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": PROMPT + conversation}]}],
                inferenceConfig={"maxTokens": 500},
            )
            text = "".join(
                block.get("text", "")
                for block in response["output"]["message"]["content"]
            )
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("model did not return JSON")
            parsed = json.loads(text[start : end + 1])
            sentiment = str(parsed.get("sentiment", "")).upper()
            signal = str(parsed.get("primarySignal", "none"))
            outcome = str(parsed.get("recommendedOutcome", ""))
            if sentiment not in SENTIMENTS:
                raise ValueError(f"unsupported sentiment {sentiment}")
            if signal not in SIGNALS:
                signal = "none"
            if outcome not in OUTCOMES:
                outcome = ""
            try:
                score = float(parsed.get("sentimentScore", 0))
            except (TypeError, ValueError):
                score = 0.0
            return {
                "sentiment": sentiment,
                "sentimentScore": max(0.0, min(1.0, score)),
                "primarySignal": signal,
                "recommendedOutcome": outcome,
                "rationaleThai": str(parsed.get("rationaleThai", ""))[:400],
                "model": model_id,
            }
        except Exception as error:  # noqa: BLE001
            errors.append(f"{model_id}: {error}")
    print(f"Bedrock classification failed: {errors}")
    return None


def _comprehend_sentiment(thai_text):
    """Secondary, non-LLM signal: translate to English then run Comprehend."""
    if not (ENABLE_COMPREHEND and thai_text):
        return {}
    try:
        english = translate.translate_text(
            Text=thai_text[:4000], SourceLanguageCode="th", TargetLanguageCode="en"
        )["TranslatedText"]
        detected = comprehend.detect_sentiment(Text=english[:4900], LanguageCode="en")
        return {
            "sentiment": detected["Sentiment"],
            "negativeScore": round(detected["SentimentScore"]["Negative"], 3),
            "english": english[:400],
        }
    except Exception as error:  # noqa: BLE001
        print(f"Translate/Comprehend cross-check failed: {error}")
        return {}


def _repeated_agent_turn(turns):
    """True when the agent asked the same thing twice with no progress between."""
    seen = [content for who, content in turns if who != "CUSTOMER"]
    normalised = [" ".join(item.split()) for item in seen]
    return len(normalised) != len(set(normalised))


# The dialogue guardrail ends a stalled call with this outcome. The conversation
# was handled safely but the customer was not served, so it must never score as
# fully handled.
UNRESOLVED_OUTCOME = "unresolved_needs_human"


def _handled(signal, recommended, recorded_outcome):
    if recorded_outcome == UNRESOLVED_OUTCOME:
        return "Partially"
    if signal in ("none", ""):
        return "Yes"
    if signal in ESCALATION_SIGNALS:
        if recorded_outcome and recommended and recorded_outcome == recommended:
            return "Yes"
        if recorded_outcome in ("declined", "callback", "", None):
            return "No"
        return "Partially"
    return "Partially" if recorded_outcome != recommended else "Yes"


def _attributes(contact_id):
    try:
        return connect.get_contact_attributes(
            InstanceId=INSTANCE_ID, InitialContactId=contact_id
        ).get("Attributes", {}) or {}
    except ClientError as error:
        print(f"Could not read contact attributes: {error}")
        return {}


def _already_evaluated(contact_id):
    try:
        existing = connect.list_contact_evaluations(
            InstanceId=INSTANCE_ID, ContactId=contact_id
        ).get("EvaluationSummaryList", [])
        return any(item.get("Status") == "SUBMITTED" for item in existing)
    except ClientError as error:
        print(f"Could not list existing evaluations: {error}")
        return False


def _submit_evaluation(contact_id, analysis, handled, looped):
    if not EVALUATION_FORM_ID:
        return None
    if _already_evaluated(contact_id):
        print(f"Evaluation already submitted for {contact_id}; skipping")
        return None
    started = connect.start_contact_evaluation(
        InstanceId=INSTANCE_ID, ContactId=contact_id, EvaluationFormId=EVALUATION_FORM_ID
    )
    evaluation_id = started["EvaluationId"]
    answers = {
        "sentiment": {"Value": {"StringValue": analysis["sentiment"]}},
        "sentiment_score": {"Value": {"NumericValue": round(analysis["sentimentScore"] * 100)}},
        "signal": {"Value": {"StringValue": analysis["primarySignal"]}},
        "signal_handled": {"Value": {"StringValue": handled}},
        "no_progress_loop": {
            "Value": {
                "StringValue": "Yes - repeated the same question" if looped else "No"
            }
        },
    }
    if analysis["recommendedOutcome"]:
        answers["recommended_outcome"] = {
            "Value": {"StringValue": analysis["recommendedOutcome"]}
        }
    if analysis["rationaleThai"]:
        answers["rationale_th"] = {"Value": {"StringValue": analysis["rationaleThai"]}}
    kwargs = {}
    if EVALUATOR_USER_ARN:
        kwargs["SubmittedBy"] = {"ConnectUserArn": EVALUATOR_USER_ARN}
    connect.submit_contact_evaluation(
        InstanceId=INSTANCE_ID, EvaluationId=evaluation_id, Answers=answers, **kwargs
    )
    return evaluation_id


def _raise_consequence(contact_id, analysis, handled, looped, recorded_outcome, secondary):
    """Alert on a missed signal and record the contact for policy improvement.

    A low score has two consequences: somebody is told, and the conversation is
    added to the corpus used to propose policy changes. Nothing here contacts the
    customer; remediation stays a human decision until the classifier is
    calibrated against human-scored samples.
    """
    missed = handled in ("No", "Partially")
    try:
        cloudwatch.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=[
            # Undimensioned so a single alarm can watch it. A dimensioned copy is
            # published separately for per-scenario reporting; an alarm on a
            # dimensioned metric would never match an undimensioned query.
            {"MetricName": "MissedCustomerSignal", "Value": 1 if handled == "No" else 0,
             "Unit": "Count"},
            {"MetricName": "MissedCustomerSignalByScenario", "Value": 1 if handled == "No" else 0,
             "Unit": "Count",
             "Dimensions": [{"Name": "Scenario", "Value": analysis.get("scenario", "unknown")}]},
            {"MetricName": "UnresolvedConversation", "Value": 1 if looped else 0, "Unit": "Count"},
            {"MetricName": "EvaluationsAnalysed", "Value": 1, "Unit": "Count"},
        ])
    except ClientError as error:
        print(f"Could not publish metrics for {contact_id}: {error}")
    if not missed:
        return False
    _publish_alert(contact_id, analysis, handled, looped, recorded_outcome)
    detail = {
        "contactId": contact_id,
        "scenario": analysis.get("scenario", "unknown"),
        "signalHandled": handled,
        "primarySignal": analysis["primarySignal"],
        "recommendedOutcome": analysis["recommendedOutcome"],
        "recordedOutcome": recorded_outcome,
        "sentiment": analysis["sentiment"],
        "comprehendSentiment": secondary.get("sentiment"),
        "noProgressLoop": looped,
        "rationaleThai": analysis["rationaleThai"],
        "model": analysis["model"],
    }
    try:
        events.put_events(Entries=[{
            "Source": EVENT_SOURCE,
            "DetailType": "LowEvaluationScore",
            "Detail": json.dumps(detail, ensure_ascii=False),
        }])
    except ClientError as error:
        print(f"Could not emit improvement event for {contact_id}: {error}")
    return True


def _publish_alert(contact_id, analysis, handled, looped, recorded_outcome):
    """Email the reviewer the contact id, a console link and why it was flagged."""
    if not ALERT_TOPIC_ARN:
        return
    link = f"{CONSOLE_BASE_URL}/contact-trace-records/details/{contact_id}" if CONSOLE_BASE_URL else "(console URL not configured)"
    lines = [
        "A Thai self-service contact was scored as not handling the customer's signal.",
        "",
        f"Contact ID        : {contact_id}",
        f"Open in console   : {link}",
        f"Scenario          : {analysis.get('scenario', 'unknown')}",
        "",
        f"Detected signal   : {analysis['primarySignal']}",
        f"Recommended       : {analysis['recommendedOutcome'] or 'none'}",
        f"Actually recorded : {recorded_outcome or 'none'}",
        f"Signal handled    : {handled}",
        f"Conversation stalled: {'yes' if looped else 'no'}",
        f"Sentiment         : {analysis['sentiment']} ({analysis['sentimentScore']:.2f})",
        "",
        f"Why: {analysis['rationaleThai']}",
        "",
        "Next step: read the transcript on the contact page and decide whether the",
        "customer needs remediation. No customer contact has been made automatically.",
    ]
    subject = f"Missed signal: {analysis['primarySignal']} on {analysis.get('scenario', 'contact')} {contact_id[:8]}"
    try:
        sns.publish(TopicArn=ALERT_TOPIC_ARN, Subject=subject[:100], Message="\n".join(lines))
    except ClientError as error:
        print(f"Could not publish alert for {contact_id}: {error}")


def _process(bucket, key):
    if "Analysis/" not in key or not key.endswith(".json"):
        return {"key": key, "skipped": "not an analysis object"}
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    analysis_file = json.loads(body)
    contact_id = _contact_id(key, analysis_file)
    if not contact_id:
        return {"key": key, "skipped": "no contact id"}

    turns = _turns(analysis_file)
    if not turns:
        return {"contactId": contact_id, "skipped": "empty transcript"}
    conversation = "\n".join(f"[{who}] {content}" for who, content in turns)
    customer_thai = " ".join(content for who, content in turns if who == "CUSTOMER")

    analysis = _classify(conversation)
    if not analysis:
        return {"contactId": contact_id, "skipped": "classification unavailable"}

    secondary = _comprehend_sentiment(customer_thai)
    recorded_outcome = _attributes(contact_id).get("fsiOutcome", "")
    # When the guardrail fires it replaces the repeated prompt, so the transcript
    # contains no duplicate; the outcome is the reliable signal that it stalled.
    looped = _repeated_agent_turn(turns) or recorded_outcome == UNRESOLVED_OUTCOME
    handled = _handled(analysis["primarySignal"], analysis["recommendedOutcome"], recorded_outcome)


    attributes = {
        "sentimentOverall": analysis["sentiment"],
        "sentimentScore": f"{analysis['sentimentScore']:.2f}",
        "sentimentEngine": "bedrock-thai-native",
        "primaryCustomerSignal": analysis["primarySignal"],
        "recommendedOutcome": analysis["recommendedOutcome"] or "none",
        "signalHandled": handled,
        "noProgressLoop": "true" if looped else "false",
        "analysisRationaleTh": analysis["rationaleThai"][:220],
        "analysisLanguage": str(analysis_file.get("LanguageCode", "")),
    }
    if secondary:
        attributes["sentimentComprehendEn"] = secondary.get("sentiment", "n/a")
        attributes["sentimentComprehendNeg"] = str(secondary.get("negativeScore", ""))
    connect.update_contact_attributes(
        InitialContactId=contact_id, InstanceId=INSTANCE_ID, Attributes=attributes
    )

    evaluation_id = None
    try:
        evaluation_id = _submit_evaluation(contact_id, analysis, handled, looped)
    except ClientError as error:
        print(f"Evaluation submission failed for {contact_id}: {error}")

    analysis["scenario"] = _attributes(contact_id).get("scenario", "unknown")
    flagged = _raise_consequence(contact_id, analysis, handled, looped, recorded_outcome, secondary)

    return {
        "contactId": contact_id,
        "sentiment": analysis["sentiment"],
        "primarySignal": analysis["primarySignal"],
        "recommendedOutcome": analysis["recommendedOutcome"],
        "recordedOutcome": recorded_outcome,
        "signalHandled": handled,
        "noProgressLoop": looped,
        "comprehend": secondary.get("sentiment"),
        "evaluationId": evaluation_id,
        "model": analysis["model"],
        "flaggedForReview": flagged,
    }


def handler(event, context):  # noqa: ARG001
    results = []
    for record in event.get("Records", []):
        bucket = record.get("s3", {}).get("bucket", {}).get("name")
        key = record.get("s3", {}).get("object", {}).get("key")
        if not (bucket and key):
            continue
        key = key.replace("%3A", ":").replace("+", " ")
        try:
            results.append(_process(bucket, key))
        except Exception as error:  # noqa: BLE001
            print(f"Failed to process s3://{bucket}/{key}: {error}")
            results.append({"key": key, "error": str(error)})
    print(json.dumps({"processed": results}, ensure_ascii=False))
    return {"processed": results}

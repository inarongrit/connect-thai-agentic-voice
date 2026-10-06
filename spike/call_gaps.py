"""Report what a caller actually experienced on a real call: silent gaps and prompt lengths.

Every pacing change before this was tuned by inference from configured values. Contact Lens
already writes a per-turn transcript with begin/end offsets to S3 for every contact, even
with real-time analytics only, so the gap the caller felt is measurable after the fact:

    silent gap = next SYSTEM turn's BeginOffsetMillis - CUSTOMER turn's EndOffsetMillis

Read-only. Takes the Connect instance id as an argument rather than hardcoding it, so the
script stays publishable.

    python3 spike/call_gaps.py --instance-id <id>                 # latest call
    python3 spike/call_gaps.py --instance-id <id> --last 5        # summary of five
    python3 spike/call_gaps.py --instance-id <id> --contact-id <contact>

Caveats worth keeping in mind when reading the output:
  * SYSTEM lines are ASR of the bot's AUDIO, so spoken Thai amounts come back as wrong
    digits. Treat numbers in the transcript as unreliable; timings are what matter here.
  * A gap measures end-of-turn detection + Lambda + model + TTS start together. Use the
    Lex conversation logs and the Lambda's modelLatencyMs attribute to split it.
"""
import argparse
import datetime
import json
import os
import statistics
import sys

import boto3

LONG_PROMPT_S = 10.0  # a prompt longer than this is a candidate for shortening


def analysis_bucket(connect, instance_id):
    configs = connect.list_instance_storage_configs(
        InstanceId=instance_id, ResourceType="CALL_RECORDINGS"
    )["StorageConfigs"]
    if not configs:
        sys.exit("instance has no CALL_RECORDINGS storage config")
    return configs[0]["S3Config"]["BucketName"]


def recent_contacts(connect, instance_id, hours=24):
    now = datetime.datetime.now(datetime.timezone.utc)
    contacts = connect.search_contacts(
        InstanceId=instance_id,
        TimeRange={
            "Type": "INITIATION_TIMESTAMP",
            "StartTime": now - datetime.timedelta(hours=hours),
            "EndTime": now,
        },
    )["Contacts"]
    # Only finished calls have an analysis file.
    done = [c for c in contacts if c.get("DisconnectTimestamp")]
    return sorted(done, key=lambda c: c["InitiationTimestamp"], reverse=True)


def find_analysis(s3, bucket, contact_id, when):
    """Contact Lens writes IVR-only calls under ivr/ and agent calls without it."""
    day = when.strftime("%Y/%m/%d")
    for prefix in (f"Analysis/Voice/ivr/{day}/", f"Analysis/Voice/{day}/"):
        listing = s3.list_objects_v2(Bucket=bucket, Prefix=prefix + contact_id)
        for item in listing.get("Contents", []):
            if item["Key"].endswith(".json"):
                return item["Key"]
    return None


def measure(turns):
    gaps, prompts, overlaps = [], [], []
    for index, turn in enumerate(turns):
        if turn["ParticipantId"] == "SYSTEM":
            prompts.append((turn["EndOffsetMillis"] - turn["BeginOffsetMillis"]) / 1000)
        if index + 1 < len(turns):
            nxt = turns[index + 1]
            if turn["ParticipantId"] == "CUSTOMER" and nxt["ParticipantId"] == "SYSTEM":
                gaps.append(((nxt["BeginOffsetMillis"] - turn["EndOffsetMillis"]) / 1000,
                             turn["Content"]))
        if turn["ParticipantId"] == "CUSTOMER":
            for other in turns:
                if (other["ParticipantId"] == "SYSTEM"
                        and other["BeginOffsetMillis"] < turn["BeginOffsetMillis"]
                        < other["EndOffsetMillis"]):
                    overlaps.append(turn["Content"])
    return gaps, prompts, overlaps


def report(contact_id, turns, verbose=True):
    gaps, prompts, overlaps = measure(turns)
    if verbose:
        print(f"\n=== {contact_id} ===")
        print(f"{'who':<9} {'start':>7} {'end':>7} {'dur':>6}  content")
        for turn in turns:
            start = turn["BeginOffsetMillis"] / 1000
            end = turn["EndOffsetMillis"] / 1000
            flag = "  <- long" if (turn["ParticipantId"] == "SYSTEM"
                                   and end - start > LONG_PROMPT_S) else ""
            print(f"{turn['ParticipantId']:<9} {start:6.1f}s {end:6.1f}s {end - start:5.1f}s  "
                  f"{turn['Content'][:48]}{flag}")
        print("\nsilent gaps (customer stops -> bot starts):")
        for gap, said in gaps:
            print(f"  {gap:5.2f}s  after \"{said[:30]}\"")
        if overlaps:
            print("customer spoke over the bot (backchannel or barge-in):",
                  ", ".join(f'"{o}"' for o in overlaps))
    return gaps, prompts


def summarise(label, values, unit="s"):
    if not values:
        return f"{label}: none"
    return (f"{label}: mean {statistics.mean(values):.2f}{unit}  "
            f"p50 {statistics.median(values):.2f}{unit}  max {max(values):.2f}{unit}  n={len(values)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--instance-id", default=os.environ.get("CONNECT_INSTANCE_ID"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    parser.add_argument("--contact-id")
    parser.add_argument("--last", type=int, default=1)
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if not args.instance_id:
        sys.exit("pass --instance-id or set CONNECT_INSTANCE_ID")

    connect = boto3.client("connect", region_name=args.region)
    s3 = boto3.client("s3", region_name=args.region)
    bucket = analysis_bucket(connect, args.instance_id)

    if args.contact_id:
        contact = connect.describe_contact(
            InstanceId=args.instance_id, ContactId=args.contact_id)["Contact"]
        targets = [{"Id": args.contact_id, "InitiationTimestamp": contact["InitiationTimestamp"]}]
    else:
        targets = recent_contacts(connect, args.instance_id, args.hours)[: args.last]
    if not targets:
        sys.exit(f"no finished contacts in the last {args.hours} h")

    all_gaps, all_prompts, missing = [], [], []
    for contact in targets:
        key = find_analysis(s3, bucket, contact["Id"], contact["InitiationTimestamp"])
        if not key:
            # Analysis usually lands a few minutes after the call ends.
            missing.append(contact["Id"])
            continue
        body = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        gaps, prompts = report(contact["Id"], body.get("Transcript", []),
                               verbose=len(targets) == 1)
        all_gaps += [g for g, _ in gaps]
        all_prompts += prompts

    print()
    print(summarise("silent gap   ", all_gaps))
    print(summarise("bot prompt   ", all_prompts))
    long_prompts = [p for p in all_prompts if p > LONG_PROMPT_S]
    print(f"prompts over {LONG_PROMPT_S:.0f}s: {len(long_prompts)} of {len(all_prompts)}")
    if missing:
        print(f"no analysis yet for: {', '.join(missing)} (usually lands a few minutes "
              "after the call)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Per-call cost estimator for the Thai FSI voice demo.

Every default rate is DERIVED FROM THIS ACCOUNT'S OWN METERED USAGE
(unblended cost divided by usage quantity, Cost Explorer, 2026-08-01..2026-08-21),
not from a published price list and not from memory. Token consumption for the
two dialogue engines comes from CloudWatch `AWS/Bedrock` counters for the same
period.

Provenance of each number is printed with `--show-sources` so a reviewer can
re-derive it. Rates change and are region/carrier specific: confirm anything
customer-facing in the AWS Pricing Calculator before quoting it.

Option A (GPT-5.6 Luna primary, Terra fallback) token PRICES come from the
published Bedrock model cards, since these models have no AWS Price List API
entry and had not yet produced a Cost Explorer line item:
    Luna : https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-luna.html
    Terra: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-terra.html
Token VOLUMES are measured from CloudWatch. Use --inference-option to switch
between in-region / geo / global rates, or the --*-price flags to override.

Usage:
    python3 tools/cost_per_call.py --minutes 3 --channel pstn-th
    python3 tools/cost_per_call.py --minutes 3 --channel webrtc --engine managed
    python3 tools/cost_per_call.py --compare --calls 1000
    python3 tools/cost_per_call.py --minutes 3 --inference-option global
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

# ── Measured unit rates (USD) ────────────────────────────────────────────────
# cost / quantity from Cost Explorer, us-west-2, 2026-08-01..2026-08-21.
VOICE_RATES = {
    # channel key: (per-minute media rate, per-minute telephony rate, label)
    "webrtc": (0.038, 0.010, "Connect AI minutes + WebRTC audio"),
    "pstn-th": (0.038, 0.069900, "Connect AI minutes + Thailand outbound telephony"),
    "pstn-us": (0.038, 0.004800, "Connect AI minutes + US outbound telephony"),
    "tollfree-in": (0.038, 0.012000, "Connect AI minutes + US toll-free inbound"),
}
TOLLFREE_NUMBER_DAY = 0.060000        # USW2-US-tollfree-numbers, per number-day
TRANSLATE_PER_CHAR = 0.000015         # USW2-TranslateText
COMPREHEND_PER_REQUEST = 0.000100     # USW2-DetectSentiment

# Option B managed path: Claude Haiku 4.5, measured.
HAIKU_INPUT_PER_1K = 0.001000         # USW2-Claude4.5Haiku-input-tokens-cross-region-global
HAIKU_OUTPUT_PER_1K = 0.005000        # USW2-Claude4.5Haiku-output-tokens-cross-region-global

# ── Option A published prices ────────────────────────────────────────────────
# Source: AWS Bedrock model cards, Standard tier, Short Context Window (272K).
#   Luna : https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-luna.html
#   Terra: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-terra.html
# Model cards quote USD per 1M tokens; converted to per-1K below.
# The dialogue Lambda calls `us.openai.gpt-5.6-luna` / `us.openai.gpt-5.6-terra`,
# which are the Geo cross-Region inference IDs, so "geo" is the default option.
# Prompts are ~220 tokens, far inside the 272K short-context tier.
OPTION_A_PRICES_PER_1M = {
    #                 luna_in, luna_out, terra_in, terra_out
    "in-region": (0.22, 1.32, 2.20, 13.20),
    "geo": (0.22, 1.32, 2.20, 13.20),
    "global": (0.20, 1.20, 2.00, 12.00),
}
DEFAULT_INFERENCE_OPTION = "geo"

# ── Measured token/usage drivers ─────────────────────────────────────────────
# CloudWatch AWS/Bedrock sums for 2026-08-01..2026-08-21 over 95 Connect calls.
CALLS_OBSERVED = 95
LUNA_INVOCATIONS = 152
LUNA_INPUT_TOKENS = 33_583
LUNA_OUTPUT_TOKENS = 25_488
TERRA_INVOCATIONS = 42
TERRA_INPUT_TOKENS = 6_070
TERRA_OUTPUT_TOKENS = 3_286
# Option B token totals for the same window (upper bound: the Haiku line item is
# shared with any other Haiku 4.5 workload in this account).
HAIKU_INPUT_TOKENS = 317_654
HAIKU_OUTPUT_TOKENS = 11_648

LUNA_IN_PER_INVOCATION = LUNA_INPUT_TOKENS / LUNA_INVOCATIONS
LUNA_OUT_PER_INVOCATION = LUNA_OUTPUT_TOKENS / LUNA_INVOCATIONS
TERRA_IN_PER_INVOCATION = TERRA_INPUT_TOKENS / TERRA_INVOCATIONS
TERRA_OUT_PER_INVOCATION = TERRA_OUTPUT_TOKENS / TERRA_INVOCATIONS
LUNA_CALLS_PER_CALL = LUNA_INVOCATIONS / CALLS_OBSERVED      # model turns per call
TERRA_FALLBACK_PER_CALL = TERRA_INVOCATIONS / CALLS_OBSERVED  # fallback turns per call
HAIKU_IN_PER_CALL = HAIKU_INPUT_TOKENS / CALLS_OBSERVED
HAIKU_OUT_PER_CALL = HAIKU_OUTPUT_TOKENS / CALLS_OBSERVED

# Post-call Thai analysis, measured per call over the same window. These are low
# because the analyzer only runs on contacts that produced a transcript; use
# --translate-chars / --sentiment-requests to model full-transcript analysis.
TRANSLATE_CHARS_PER_CALL = 885 / CALLS_OBSERVED
SENTIMENT_REQUESTS_PER_CALL = 33 / CALLS_OBSERVED


# ── Assumption-based components (opt-in, not measured) ───────────────────────
# These have no billed usage in the measured window, so they are expressed as
# stated ASSUMPTIONS rather than prices. Override with the matching CLI flags.
#
# Contact Lens post-contact analytics: assumed to land in the same order of
# magnitude as the Connect AI end-customer minute, taken here as 40% of it.
CONTACT_LENS_ASSUMED_FRACTION = 0.40
CONTACT_LENS_ASSUMED_PER_MIN = 0.038 * CONTACT_LENS_ASSUMED_FRACTION
# Serverless overhead per call: ~12 Lambda invocations (trigger, session setup,
# one per dialogue turn, outcome, analyzer), a handful of DynamoDB writes, a few
# S3 puts, one API Gateway request and a CloudFront page view. Individually
# sub-cent, so carried as one rounded allowance instead of a false-precision sum.
SERVERLESS_ASSUMED_PER_CALL = 0.002

ASSUMPTIONS = (
    "Talk time is billed whole-minute-agnostic (linear per-minute, no rounding up).",
    "One outbound call per contact, no retries, no queue or agent handling time.",
    f"Option A averages {LUNA_INVOCATIONS / CALLS_OBSERVED:.2f} Luna turns and "
    f"{TERRA_INVOCATIONS / CALLS_OBSERVED:.2f} Terra fallback turns per call, as measured.",
    "Dialogue prompts stay inside the 272K short-context tier (measured ~220 tokens).",
    f"Contact Lens post-contact analytics ASSUMED at {CONTACT_LENS_ASSUMED_FRACTION:.0%} of the "
    f"Connect AI minute rate (${CONTACT_LENS_ASSUMED_PER_MIN:.4f}/min) — no billed usage to measure.",
    f"Serverless overhead ASSUMED at ${SERVERLESS_ASSUMED_PER_CALL:.4f}/call as a rounded allowance "
    "for Lambda, DynamoDB, S3, CloudFront and API Gateway combined.",
    "Excludes recording/transcript storage growth, data transfer, support plans and taxes.",
    "Telephony rates are country and carrier specific; Thailand outbound measured here.",
)


@dataclass
class Prices:
    """Per-1K-token prices.

    Option A defaults come from the published Bedrock model cards (Geo CRIS,
    Standard tier, short context). Option B comes from this account's metered
    Haiku 4.5 usage.
    """

    luna_input_per_1k: float = OPTION_A_PRICES_PER_1M[DEFAULT_INFERENCE_OPTION][0] / 1000
    luna_output_per_1k: float = OPTION_A_PRICES_PER_1M[DEFAULT_INFERENCE_OPTION][1] / 1000
    terra_input_per_1k: float = OPTION_A_PRICES_PER_1M[DEFAULT_INFERENCE_OPTION][2] / 1000
    terra_output_per_1k: float = OPTION_A_PRICES_PER_1M[DEFAULT_INFERENCE_OPTION][3] / 1000
    haiku_input_per_1k: float = HAIKU_INPUT_PER_1K
    haiku_output_per_1k: float = HAIKU_OUTPUT_PER_1K
    inference_option: str = DEFAULT_INFERENCE_OPTION

    @classmethod
    def for_option(cls, inference_option: str = DEFAULT_INFERENCE_OPTION) -> "Prices":
        """Build Option A prices for an inference option from the model cards."""
        if inference_option not in OPTION_A_PRICES_PER_1M:
            raise ValueError(
                f"unknown inference option: {inference_option}; "
                f"expected one of {sorted(OPTION_A_PRICES_PER_1M)}"
            )
        luna_in, luna_out, terra_in, terra_out = OPTION_A_PRICES_PER_1M[inference_option]
        return cls(
            luna_input_per_1k=luna_in / 1000,
            luna_output_per_1k=luna_out / 1000,
            terra_input_per_1k=terra_in / 1000,
            terra_output_per_1k=terra_out / 1000,
            inference_option=inference_option,
        )


@dataclass
class Breakdown:
    channel: str
    minutes: float
    engine: str
    media: float = 0.0
    telephony: float = 0.0
    dialogue: float = 0.0
    post_call: float = 0.0
    contact_lens: float = 0.0
    serverless: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def voice(self) -> float:
        return self.media + self.telephony

    @property
    def assumed(self) -> float:
        """Components carried as assumptions rather than measured rates."""
        return self.contact_lens + self.serverless

    @property
    def total(self) -> float:
        return self.voice + self.dialogue + self.post_call + self.assumed


def dialogue_cost(engine: str, prices: Prices, model_turns: float, fallback_turns: float) -> float:
    """Cost of the dialogue engine for one call."""
    if engine == "managed":  # Option B, Claude Haiku 4.5 via Q in Connect
        return (
            HAIKU_IN_PER_CALL / 1000 * prices.haiku_input_per_1k
            + HAIKU_OUT_PER_CALL / 1000 * prices.haiku_output_per_1k
        )
    primary = model_turns * (
        LUNA_IN_PER_INVOCATION / 1000 * prices.luna_input_per_1k
        + LUNA_OUT_PER_INVOCATION / 1000 * prices.luna_output_per_1k
    )
    fallback = fallback_turns * (
        TERRA_IN_PER_INVOCATION / 1000 * prices.terra_input_per_1k
        + TERRA_OUT_PER_INVOCATION / 1000 * prices.terra_output_per_1k
    )
    return primary + fallback


def estimate(
    minutes: float,
    channel: str = "pstn-th",
    engine: str = "mantle",
    prices: Prices | None = None,
    model_turns: float = LUNA_CALLS_PER_CALL,
    fallback_turns: float = TERRA_FALLBACK_PER_CALL,
    translate_chars: float = TRANSLATE_CHARS_PER_CALL,
    sentiment_requests: float = SENTIMENT_REQUESTS_PER_CALL,
    post_call_analysis: bool = True,
    include_assumed: bool = False,
    contact_lens_per_min: float = CONTACT_LENS_ASSUMED_PER_MIN,
    serverless_per_call: float = SERVERLESS_ASSUMED_PER_CALL,
) -> Breakdown:
    """Estimate the cost of a single call."""
    if channel not in VOICE_RATES:
        raise ValueError(f"unknown channel: {channel}; expected one of {sorted(VOICE_RATES)}")
    if engine not in {"mantle", "managed"}:
        raise ValueError(f"unknown engine: {engine}; expected mantle or managed")
    if minutes < 0:
        raise ValueError("minutes cannot be negative")
    prices = prices or Prices()
    media_rate, telephony_rate, _ = VOICE_RATES[channel]
    result = Breakdown(channel=channel, minutes=minutes, engine=engine)
    result.media = media_rate * minutes
    result.telephony = telephony_rate * minutes
    result.dialogue = dialogue_cost(engine, prices, model_turns, fallback_turns)
    if post_call_analysis:
        result.post_call = (
            translate_chars * TRANSLATE_PER_CHAR + sentiment_requests * COMPREHEND_PER_REQUEST
        )
    if include_assumed:
        result.contact_lens = contact_lens_per_min * minutes
        result.serverless = serverless_per_call
        result.notes.append(
            f"Includes ASSUMED components: Contact Lens at ${contact_lens_per_min:.4f}/min and "
            f"serverless overhead at ${serverless_per_call:.4f}/call. Neither is measured — see "
            "--show-assumptions."
        )
    if engine == "mantle":
        result.notes.append(
            f"Option A prices: Bedrock model cards, Standard tier, short context (272K), "
            f"{prices.inference_option} inference — Luna ${prices.luna_input_per_1k * 1000:.2f}/"
            f"${prices.luna_output_per_1k * 1000:.2f} and Terra "
            f"${prices.terra_input_per_1k * 1000:.2f}/${prices.terra_output_per_1k * 1000:.2f} per 1M "
            f"in/out. Token volumes measured from CloudWatch."
        )
    if engine == "managed":
        result.notes.append(
            "Option B token volume is an upper bound: the Haiku 4.5 line item is shared "
            "with any other Haiku workload in this account."
        )
    return result


def monthly(breakdown: Breakdown, calls: int, tollfree_numbers: int = 0, days: int = 30) -> dict:
    """Scale a per-call breakdown to a monthly figure plus fixed charges."""
    fixed = tollfree_numbers * days * TOLLFREE_NUMBER_DAY
    variable = breakdown.total * calls
    return {"calls": calls, "variable": variable, "fixed": fixed, "total": variable + fixed}


def _format(breakdown: Breakdown) -> str:
    label = VOICE_RATES[breakdown.channel][2]
    engine_label = "Option A (Luna→Terra)" if breakdown.engine == "mantle" else "Option B (Haiku 4.5)"
    lines = [
        f"Channel        : {breakdown.channel} — {label}",
        f"Engine         : {engine_label}",
        f"Talk time      : {breakdown.minutes:g} min",
        "",
        f"  Connect AI media      ${breakdown.media:8.4f}",
        f"  Telephony             ${breakdown.telephony:8.4f}",
        f"  Dialogue tokens       ${breakdown.dialogue:8.4f}",
        f"  Post-call analysis    ${breakdown.post_call:8.4f}",
    ]
    if breakdown.assumed:
        lines += [
            f"  Contact Lens (assumed)${breakdown.contact_lens:8.4f}",
            f"  Serverless (assumed)  ${breakdown.serverless:8.4f}",
        ]
    lines += [
        f"  {'-' * 34}",
        f"  TOTAL PER CALL        ${breakdown.total:8.4f}",
        "",
        f"Mix: voice {breakdown.voice / breakdown.total:.0%}, "
        f"dialogue {breakdown.dialogue / breakdown.total:.0%}, "
        f"post-call {breakdown.post_call / breakdown.total:.0%}"
        + (f", assumed {breakdown.assumed / breakdown.total:.0%}" if breakdown.assumed else ""),
    ]
    for note in breakdown.notes:
        lines += ["", f"NOTE: {note}"]
    return "\n".join(lines)


def _sources() -> str:
    return "\n".join(
        [
            "Rate provenance — all measured, none from a published price list:",
            "  Cost Explorer us-west-2, 2026-08-01..2026-08-21 (cost / usage quantity):",
            "    Connect AI end-customer minutes   $0.038000 / min",
            "    WebRTC web-calling audio          $0.010000 / min",
            "    Thailand outbound telephony       $0.069900 / min",
            "    US outbound telephony             $0.004800 / min",
            "    US toll-free inbound              $0.012000 / min",
            "    Toll-free number rental           $0.060000 / number-day",
            "    Claude Haiku 4.5 input            $0.001000 / 1K tokens",
            "    Claude Haiku 4.5 output           $0.005000 / 1K tokens",
            "    Amazon Translate                  $0.000015 / character",
            "    Comprehend DetectSentiment        $0.000100 / request",
            "",
            "  CloudWatch AWS/Bedrock, same window, over 95 Connect calls:",
            f"    Luna  {LUNA_INVOCATIONS} invocations, {LUNA_INPUT_TOKENS:,} in / {LUNA_OUTPUT_TOKENS:,} out",
            f"          → {LUNA_IN_PER_INVOCATION:.1f} in / {LUNA_OUT_PER_INVOCATION:.1f} out per turn,"
            f" {LUNA_CALLS_PER_CALL:.2f} turns per call",
            f"    Terra {TERRA_INVOCATIONS} invocations, {TERRA_INPUT_TOKENS:,} in / {TERRA_OUTPUT_TOKENS:,} out",
            f"          → {TERRA_IN_PER_INVOCATION:.1f} in / {TERRA_OUT_PER_INVOCATION:.1f} out per turn,"
            f" {TERRA_FALLBACK_PER_CALL:.2f} fallback turns per call"
            f" ({TERRA_INVOCATIONS / LUNA_INVOCATIONS:.0%} of primary turns)",
            f"    Haiku {HAIKU_INPUT_TOKENS:,} in / {HAIKU_OUTPUT_TOKENS:,} out"
            f" → {HAIKU_IN_PER_CALL:.0f} in / {HAIKU_OUT_PER_CALL:.0f} out per call (upper bound)",
            "",
            "  Published Bedrock model cards (Standard tier, short context 272K, per 1M tokens):",
            "    Luna  in-region/geo $0.22 in, $1.32 out   |  global $0.20 in, $1.20 out",
            "    Terra in-region/geo $2.20 in, $13.20 out  |  global $2.00 in, $12.00 out",
            "    https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-luna.html",
            "    https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-terra.html",
            "    The dialogue Lambda uses the us.* Geo cross-Region IDs, so 'geo' is the default.",
            "",
            "  Not measurable here — add separately if in scope:",
            "    Contact Lens post-contact minutes (no billed usage in this window)",
            "    Lambda / DynamoDB / S3 / CloudFront / API Gateway (shared account, sub-cent per call)",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate cost per call for the Thai FSI voice demo from measured account usage."
    )
    parser.add_argument("--minutes", type=float, default=3.0, help="talk time per call (default 3)")
    parser.add_argument("--channel", default="pstn-th", choices=sorted(VOICE_RATES))
    parser.add_argument("--engine", default="mantle", choices=["mantle", "managed"],
                        help="mantle = Option A Luna/Terra, managed = Option B Haiku 4.5")
    parser.add_argument("--model-turns", type=float, default=LUNA_CALLS_PER_CALL,
                        help=f"Option A primary model turns per call (measured {LUNA_CALLS_PER_CALL:.2f})")
    parser.add_argument("--fallback-turns", type=float, default=TERRA_FALLBACK_PER_CALL,
                        help=f"Option A Terra fallback turns per call (measured {TERRA_FALLBACK_PER_CALL:.2f})")
    parser.add_argument("--inference-option", default=DEFAULT_INFERENCE_OPTION,
                        choices=sorted(OPTION_A_PRICES_PER_1M),
                        help="Option A Bedrock inference option (default geo, matching the us.* model IDs)")
    parser.add_argument("--luna-input-price", type=float, help="override USD per 1K Luna input tokens")
    parser.add_argument("--luna-output-price", type=float, help="override USD per 1K Luna output tokens")
    parser.add_argument("--terra-input-price", type=float, help="override USD per 1K Terra input tokens")
    parser.add_argument("--terra-output-price", type=float, help="override USD per 1K Terra output tokens")
    parser.add_argument("--translate-chars", type=float, default=TRANSLATE_CHARS_PER_CALL,
                        help=f"post-call characters translated (measured {TRANSLATE_CHARS_PER_CALL:.1f};"
                             " raise to model full-transcript analysis)")
    parser.add_argument("--sentiment-requests", type=float, default=SENTIMENT_REQUESTS_PER_CALL,
                        help=f"post-call sentiment requests (measured {SENTIMENT_REQUESTS_PER_CALL:.2f})")
    parser.add_argument("--no-post-call", action="store_true", help="exclude post-call Thai analysis")
    parser.add_argument("--calls", type=int, help="also print a monthly total for this call volume")
    parser.add_argument("--tollfree-numbers", type=int, default=0, help="claimed numbers for fixed rental")
    parser.add_argument("--compare", action="store_true", help="print a duration/channel/engine matrix")
    parser.add_argument("--include-assumed", action="store_true",
                        help="include assumption-based Contact Lens and serverless overhead")
    parser.add_argument("--contact-lens-per-min", type=float, default=CONTACT_LENS_ASSUMED_PER_MIN,
                        help=f"assumed Contact Lens rate (default ${CONTACT_LENS_ASSUMED_PER_MIN:.4f}/min)")
    parser.add_argument("--serverless-per-call", type=float, default=SERVERLESS_ASSUMED_PER_CALL,
                        help=f"assumed serverless allowance (default ${SERVERLESS_ASSUMED_PER_CALL:.4f}/call)")
    parser.add_argument("--show-assumptions", action="store_true",
                        help="print the pricing assumptions and exit")
    parser.add_argument("--show-sources", action="store_true", help="print rate provenance and exit")
    args = parser.parse_args(argv)

    if args.show_assumptions:
        print("Pricing assumptions:")
        for item in ASSUMPTIONS:
            print(f"  - {item}")
        return 0

    if args.show_sources:
        print(_sources())
        return 0

    prices = Prices.for_option(args.inference_option)
    if args.luna_input_price is not None:
        prices.luna_input_per_1k = args.luna_input_price
    if args.luna_output_price is not None:
        prices.luna_output_per_1k = args.luna_output_price
    if args.terra_input_price is not None:
        prices.terra_input_per_1k = args.terra_input_price
    if args.terra_output_price is not None:
        prices.terra_output_per_1k = args.terra_output_price

    if args.compare:
        print(f"{'Channel':13} {'Engine':8} {'Min':>4} {'Voice':>9} {'Dialogue':>9} {'Post':>8} {'TOTAL':>9}")
        for channel in ("webrtc", "pstn-th"):
            for engine in ("mantle", "managed"):
                for minutes in (2, 3, 5):
                    b = estimate(minutes, channel, engine, prices,
                                 args.model_turns, args.fallback_turns,
                                 args.translate_chars, args.sentiment_requests,
                                 post_call_analysis=not args.no_post_call,
                                 include_assumed=args.include_assumed,
                                 contact_lens_per_min=args.contact_lens_per_min,
                                 serverless_per_call=args.serverless_per_call)
                    print(f"{channel:13} {engine:8} {minutes:>4} {b.voice:>9.4f} "
                          f"{b.dialogue:>9.4f} {b.post_call:>8.4f} {b.total:>9.4f}")
        print("\nNOTE: Option A rows priced from the Bedrock model cards "
              f"({prices.inference_option} inference, Standard tier, short context); "
              "token volumes measured from CloudWatch.")
        return 0

    breakdown = estimate(args.minutes, args.channel, args.engine, prices,
                         args.model_turns, args.fallback_turns,
                         args.translate_chars, args.sentiment_requests,
                         post_call_analysis=not args.no_post_call,
                         include_assumed=args.include_assumed,
                         contact_lens_per_min=args.contact_lens_per_min,
                         serverless_per_call=args.serverless_per_call)
    print(_format(breakdown))
    if args.calls:
        scaled = monthly(breakdown, args.calls, args.tollfree_numbers)
        print("")
        print(f"At {scaled['calls']:,} calls/month: variable ${scaled['variable']:,.2f}"
              f" + fixed ${scaled['fixed']:,.2f} = ${scaled['total']:,.2f}")
    print("")
    print("Assumptions (--show-assumptions for the full list):")
    for item in ASSUMPTIONS[:3]:
        print(f"  - {item}")
    if not args.include_assumed:
        print("  - EXCLUDED: Contact Lens post-contact minutes and Lambda/DynamoDB/S3/CloudFront/"
              "API Gateway overhead (add with --include-assumed).")
    print("")
    print("Measured from this account's usage; confirm forward-looking rates in the AWS Pricing Calculator.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

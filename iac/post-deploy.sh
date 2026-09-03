#!/usr/bin/env bash
set -euo pipefail

STACK="${1:?usage: post-deploy.sh <stack-name> [region]}"
REGION="${2:-us-west-2}"
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$BASE_DIR/.." && pwd)"
WEB_DIR="$REPO_ROOT/web"

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

ASSISTANT_ID=$(out AssistantId)
BOT_ID=$(out LexBotId)
DEMO_URL=$(out DemoUrl)
BUCKET=$(out WebBucketName)
SESSION_FUNCTION=$(out SessionContextFunctionName)
INSTANCE_ID=$(out ConnectInstanceIdOutput)
MODEL_ID="global.anthropic.claude-haiku-4-5-20251001-v1:0"

echo "==> Assistant: $ASSISTANT_ID | Bot: $BOT_ID"

DEFAULT_AGENT_ID=$(aws qconnect get-assistant --region "$REGION" \
  --assistant-id "$ASSISTANT_ID" \
  --query 'assistant.aiAgentConfiguration.SELF_SERVICE.aiAgentId' --output text | cut -d: -f1)
ANSWER_PROMPT_ID=$(aws qconnect get-ai-agent --region "$REGION" \
  --assistant-id "$ASSISTANT_ID" --ai-agent-id "$DEFAULT_AGENT_ID" \
  --query 'aiAgent.configuration.selfServiceAIAgentConfiguration.selfServiceAnswerGenerationAIPromptId' \
  --output text)

create_scenario_agent() {
  local key="$1" prompt_name="$2" agent_name="$3" prompt_file="$4"
  echo "==> Creating $key prompt and agent..." >&2
  local prompt_id agent_id
  prompt_id=$(aws qconnect create-ai-prompt --region "$REGION" \
    --assistant-id "$ASSISTANT_ID" --name "$prompt_name" \
    --type SELF_SERVICE_PRE_PROCESSING --template-type TEXT \
    --model-id "$MODEL_ID" --api-format MESSAGES --visibility-status PUBLISHED \
    --template-configuration "file://$BASE_DIR/$prompt_file" \
    --query 'aiPrompt.aiPromptId' --output text)
  prompt_id="${prompt_id%%:*}"
  agent_id=$(aws qconnect create-ai-agent --region "$REGION" \
    --assistant-id "$ASSISTANT_ID" --name "$agent_name" --type SELF_SERVICE \
    --visibility-status PUBLISHED \
    --configuration "{\"selfServiceAIAgentConfiguration\":{\"selfServicePreProcessingAIPromptId\":\"$prompt_id\",\"selfServiceAnswerGenerationAIPromptId\":\"$ANSWER_PROMPT_ID\"}}" \
    --query 'aiAgent.aiAgentId' --output text)
  echo "${agent_id%%:*}"
}

BANK_AGENT=$(create_scenario_agent bank FSIBankCollectionThaiPrompt FSIBankCollectionThaiAgent ai-prompt-bank.json)
INSURANCE_AGENT=$(create_scenario_agent insurance FSIInsuranceThaiPrompt FSIInsuranceThaiAgent ai-prompt-insurance.json)
BROKER_AGENT=$(create_scenario_agent broker FSIBrokerageThaiPrompt FSIBrokerageThaiAgent ai-prompt-broker.json)

# Bank is the fallback/default. Each actual call overrides SELF_SERVICE per session.
aws qconnect update-assistant-ai-agent --region "$REGION" \
  --assistant-id "$ASSISTANT_ID" --ai-agent-type SELF_SERVICE \
  --configuration "aiAgentId=$BANK_AGENT" >/dev/null

python3 - "$INSTANCE_ID" "$BANK_AGENT" "$INSURANCE_AGENT" "$BROKER_AGENT" <<'PY' >/tmp/fsi-session-env.json
import json, sys
instance, bank, insurance, broker = sys.argv[1:]
agents = json.dumps({"bank": bank, "insurance": insurance, "broker": broker}, separators=(",", ":"))
print(json.dumps({"Variables": {"INSTANCE_ID": instance, "SCENARIO_AGENT_IDS": agents}}))
PY
aws lambda update-function-configuration --region "$REGION" \
  --function-name "$SESSION_FUNCTION" --environment file:///tmp/fsi-session-env.json >/dev/null
aws lambda wait function-updated --region "$REGION" --function-name "$SESSION_FUNCTION"
echo "    bank=$BANK_AGENT insurance=$INSURANCE_AGENT broker=$BROKER_AGENT"

# Advanced Thai ASR plus Primary assisted NLU, required by AMAZON.QInConnectIntent.
echo "==> Configuring Thai locale..."
aws lexv2-models update-bot-locale --region "$REGION" \
  --bot-id "$BOT_ID" --bot-version DRAFT --locale-id th_TH \
  --nlu-intent-confidence-threshold 0.4 \
  --speech-recognition-settings speechModelPreference=Advanced \
  --speech-detection-sensitivity Default \
  --generative-ai-settings '{"runtimeSettings":{"nluImprovement":{"enabled":true,"assistedNluMode":"Primary"}}}' >/dev/null
aws lexv2-models build-bot-locale --region "$REGION" \
  --bot-id "$BOT_ID" --bot-version DRAFT --locale-id th_TH >/dev/null
until [ "$(aws lexv2-models describe-bot-locale --region "$REGION" \
  --bot-id "$BOT_ID" --bot-version DRAFT --locale-id th_TH \
  --query botLocaleStatus --output text)" = "Built" ]; do sleep 5; done

NEW_VERSION=$(aws lexv2-models create-bot-version --region "$REGION" \
  --bot-id "$BOT_ID" \
  --bot-version-locale-specification '{"th_TH":{"sourceBotVersion":"DRAFT"}}' \
  --query botVersion --output text)
sleep 10
ALIAS_ID=$(aws lexv2-models list-bot-aliases --region "$REGION" --bot-id "$BOT_ID" \
  --query "botAliasSummaries[?botAliasName=='live'].botAliasId" --output text)
aws lexv2-models update-bot-alias --region "$REGION" \
  --bot-id "$BOT_ID" --bot-alias-id "$ALIAS_ID" --bot-alias-name live \
  --bot-version "$NEW_VERSION" \
  --bot-alias-locale-settings '{"th_TH":{"enabled":true}}' >/dev/null

# Upload web UI with stack-specific endpoint/domain.
echo "==> Uploading web UI..."
# Optional custom domain: export DEMO_DOMAIN before running to use a CNAME/alias
# instead of the CloudFront hostname in the presenter QR page.
DOMAIN="${DEMO_DOMAIN:-${DEMO_URL#https://}}"
cp "$WEB_DIR/index.html" /tmp/fsi-index.html
sed -e "s|__DEMO_DOMAIN__|$DOMAIN|g" "$WEB_DIR/qr.html" >/tmp/fsi-qr.html
aws s3 cp /tmp/fsi-index.html "s3://$BUCKET/index.html" --content-type "text/html; charset=utf-8" --cache-control "no-cache" --region "$REGION"
aws s3 cp "$WEB_DIR/call.html" "s3://$BUCKET/call.html" --content-type "text/html; charset=utf-8" --cache-control "no-cache" --region "$REGION"
aws s3 cp "$WEB_DIR/feedback.js" "s3://$BUCKET/feedback.js" --content-type "application/javascript; charset=utf-8" --cache-control "public,max-age=86400" --region "$REGION"
aws s3 cp "$WEB_DIR/cost.js" "s3://$BUCKET/cost.js" --content-type "application/javascript; charset=utf-8" --cache-control "public,max-age=86400" --region "$REGION"
aws s3 cp "$WEB_DIR/webrtc.bundle.js" "s3://$BUCKET/webrtc.bundle.js" --content-type "application/javascript; charset=utf-8" --cache-control "public,max-age=31536000,immutable" --region "$REGION"
aws s3 cp "$WEB_DIR/livesync.html" "s3://$BUCKET/livesync.html" --content-type "text/html; charset=utf-8" --cache-control "no-cache" --region "$REGION"
aws s3 cp "$WEB_DIR/touchpoint.bundle.js" "s3://$BUCKET/touchpoint.bundle.js" --content-type "application/javascript; charset=utf-8" --cache-control "public,max-age=31536000,immutable" --region "$REGION"
aws s3 cp /tmp/fsi-qr.html "s3://$BUCKET/qr.html" --content-type "text/html; charset=utf-8" --region "$REGION"

echo ""
echo "Done: $DEMO_URL"
echo "QR:   $DEMO_URL/qr.html"
echo "For Thailand +66 outbound calls, request Connect country allowlisting through AWS Support."

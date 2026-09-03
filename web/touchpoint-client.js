import { create } from "@amazon-connect-touchpoint/web";

const OPTIONS = Object.freeze([
  Object.freeze({ id: "reduce_installment", labelTh: "ลดค่างวดชั่วคราว" }),
  Object.freeze({ id: "principal_holiday", labelTh: "พักชำระเงินต้น" }),
  Object.freeze({ id: "extend_term", labelTh: "ขยายระยะเวลาผ่อนชำระ" }),
]);
const ALLOWED = new Set(OPTIONS.map(option => option.id));
let active = null;

function emit(callback, state, detail = {}) {
  if (typeof callback === "function") callback({ state, ...detail });
}

function normalizeSelection(value) {
  const option = typeof value === "string" ? value : value?.option;
  if (!ALLOWED.has(option)) throw new Error("Live Sync returned an unapproved relief option");
  return OPTIONS.find(item => item.id === option);
}

export async function connect({ deploymentKey, apiKey, contactId, onSelection, onState }) {
  if (!deploymentKey || !apiKey || !contactId) {
    throw new Error("deploymentKey, apiKey, and contactId are required for external Live Sync");
  }
  if (active) disconnect();
  emit(onState, "connecting");

  // `external` opens no chat or voice contact. It only binds the existing live contact
  // to this page, so the working Lambda dialogue path remains untouched.
  const touchpoint = await create({
    config: { region: "us-west-2" },
    input: "external",
    languageCode: "th-TH",
    liveSync: {
      deploymentKey,
      apiKey,
      contactId,
      automaticContext: false,
    },
  });

  const action = {
    action: "select_relief_option",
    description: "Select exactly one approved Thai payment-relief option shown on this page.",
    input: { options: OPTIONS.map(({ id, labelTh }) => ({ id, labelTh })) },
    schema: {
      type: "object",
      properties: {
        option: {
          type: "string",
          enum: OPTIONS.map(option => option.id),
          description: "The approved relief option ID selected by speech or tap.",
        },
      },
      required: ["option"],
      additionalProperties: false,
    },
    handler: value => {
      const selected = normalizeSelection(value);
      emit(onSelection, "selected", { selected, source: "live-sync" });
    },
  };

  // Explicit context means the agent sees only this action and this scope, not the page.
  await touchpoint.sendContext({ actions: [action], scopes: ["relief_options"] });
  active = touchpoint;
  emit(onState, "connected");
  return { connected: true, teardown: disconnect };
}

export function disconnect() {
  if (!active) return;
  active.teardown();
  active = null;
}

export async function select(optionId, onSelection) {
  const selected = normalizeSelection({ option: optionId });
  emit(onSelection, "selected", {
    selected,
    source: active ? "tap" : "preview",
  });
  // Touchpoint documents conversationHandler.sendText for custom modality taps. Sending
  // the Thai label feeds the customer's visual choice back into the same conversation.
  if (active && typeof active.conversationHandler?.sendText === "function") {
    await active.conversationHandler.sendText(`ฉันเลือก${selected.labelTh}ค่ะ`);
  }
  return selected;
}

export function previewSelect(optionId, onSelection) {
  const selected = normalizeSelection({ option: optionId });
  emit(onSelection, "selected", { selected, source: "preview" });
  return selected;
}

export function options() {
  return OPTIONS.map(option => ({ ...option }));
}

export function isConnected() {
  return Boolean(active);
}

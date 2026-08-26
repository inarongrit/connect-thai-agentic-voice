/* Post-call AWS cost estimate, shared by the WebRTC landing page and the PSTN
   status page.

   Rates mirror tools/cost_per_call.py. Connect AI minutes, the per-channel
   telephony/audio rate and post-call analysis are derived from this account's
   metered usage; Contact Lens and serverless overhead are stated ASSUMPTIONS.

   The telephony line is CHANNEL-SPECIFIC: a WebRTC call is charged web-calling
   audio minutes, a PSTN call is charged outbound telephony minutes. Exactly one
   of them ever applies, so exactly one is ever shown.

   The dialogue line is the measured cost of the single dialogue engine now in
   use. It never names the model, so the tester-facing page stays free of engine
   identity. */
(function () {
  "use strict";

  var RATES = {
    aiMinute: 0.038,              // Connect AI end-customer minutes, every channel
    dialoguePerCall: 0.00103,     // measured: Luna turns + Terra fallback share
    postCallPerCall: 0.0002,      // Translate + Comprehend cross-check
    contactLensPerMinute: 0.0152, // ASSUMED, 40% of the Connect AI minute
    serverlessPerCall: 0.002      // ASSUMED allowance for Lambda/DDB/S3/CF/APIGW
  };

  var CHANNELS = {
    webrtc: { rate: 0.010, label: "เสียง WebRTC" },
    "pstn-th": { rate: 0.0699, label: "ค่าโทรออกไทย (PSTN)" },
    "pstn-us": { rate: 0.0048, label: "ค่าโทรออกสหรัฐ (PSTN)" }
  };

  var ASSUMPTIONS = [
    "คิดตามเวลาสนทนาจริงแบบต่อนาที ไม่ปัดขึ้นเป็นนาทีเต็ม",
    "คิดค่าช่องทางเดียวตามที่ใช้จริง WebRTC หรือ PSTN ไม่คิดซ้อนกัน",
    "นาที Connect AI คิดทุกช่องทาง ส่วนค่าเสียงหรือค่าโทรคิดแยกตามช่องทาง",
    "ค่าชุดสนทนา AI คิดจากจำนวนรอบเรียกโมเดลที่วัดได้จริงต่อสาย",
    "Contact Lens และค่า Lambda, DynamoDB, S3, CloudFront ประมาณการไว้ ยังไม่ใช่ค่าที่วัดได้",
    "ไม่รวมค่าจัดเก็บไฟล์บันทึกระยะยาว ค่าโอนข้อมูล ภาษี และแพ็กเกจซัพพอร์ต",
    "อัตราค่าโทรต่างกันตามประเทศและผู้ให้บริการ"
  ];

  function normalizeChannel(channel) {
    if (channel === "pstn") return "pstn-th";
    return CHANNELS[channel] ? channel : "webrtc";
  }

  function estimate(seconds, channel) {
    var key = normalizeChannel(channel);
    var minutes = Math.max(0, Number(seconds) || 0) / 60;
    var rows = [
      { label: "นาที Connect AI", value: RATES.aiMinute * minutes, assumed: false },
      { label: CHANNELS[key].label, value: CHANNELS[key].rate * minutes, assumed: false },
      { label: "ชุดสนทนา AI", value: RATES.dialoguePerCall, assumed: false },
      { label: "วิเคราะห์หลังสาย", value: RATES.postCallPerCall, assumed: false },
      { label: "Contact Lens (ประมาณการ)", value: RATES.contactLensPerMinute * minutes, assumed: true },
      { label: "Lambda และบริการรอบข้าง (ประมาณการ)", value: RATES.serverlessPerCall, assumed: true }
    ];
    var total = rows.reduce(function (sum, row) { return sum + row.value; }, 0);
    return { channel: key, minutes: minutes, rows: rows, total: total };
  }

  window.FsiCost = { RATES: RATES, CHANNELS: CHANNELS, ASSUMPTIONS: ASSUMPTIONS, estimate: estimate };

  var STYLE = '.cost-pop{position:fixed;right:clamp(12px,2vw,26px);bottom:clamp(12px,2vh,26px);z-index:60;width:min(340px,calc(100vw - 24px));max-height:min(70vh,560px);overflow:auto;display:none;padding:16px 17px 14px;border:1px solid var(--line-hot,var(--mint));border-radius:3px;background:var(--night-2,var(--night));box-shadow:var(--shadow,0 28px 70px rgba(0,0,0,.5));font-size:.78rem;line-height:1.5;color:var(--paper)}'
    + '.cost-pop.show{display:block;animation:costRise .42s cubic-bezier(.16,1,.3,1)}'
    + '@keyframes costRise{from{opacity:0;transform:translateY(14px) scale(.97)}}'
    + '.cost-pop-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:11px}'
    + '.cost-pop-head small{display:block;color:var(--amber);font:600 9px/1 "Chakra Petch",sans-serif;letter-spacing:.2em;margin-bottom:5px}'
    + '.cost-pop-head strong{font-family:"Chakra Petch",sans-serif;font-size:1rem;font-weight:500}'
    + '.cost-pop-close{appearance:none;flex:none;width:26px;height:26px;border:1px solid var(--line);border-radius:50%;background:transparent;color:var(--muted);cursor:pointer;font-size:14px;line-height:1}'
    + '.cost-pop-close:hover{color:var(--paper);border-color:var(--line-hot,var(--mint))}'
    + '.cost-pop-close:focus-visible{outline:2px solid var(--paper);outline-offset:2px}'
    + '.cost-channel{display:inline-block;margin-bottom:10px;padding:3px 8px;border:1px solid var(--line);border-radius:99px;color:var(--mint);font:600 8px/1.5 "Chakra Petch",sans-serif;letter-spacing:.12em}'
    + '.cost-total{display:flex;align-items:baseline;gap:8px;padding:10px 12px;margin-bottom:11px;border:1px solid var(--line);border-radius:2px}'
    + '.cost-total b{font-family:"Chakra Petch",sans-serif;font-size:1.5rem;font-weight:500;color:var(--mint-soft,var(--mint))}'
    + '.cost-total span{color:var(--muted);font-size:.7rem}'
    + '.cost-rows{display:grid;gap:5px;margin-bottom:11px}'
    + '.cost-row{display:flex;justify-content:space-between;gap:10px;color:var(--muted)}'
    + '.cost-row b{color:var(--paper);font-weight:500;font-variant-numeric:tabular-nums}'
    + '.cost-row.is-assumed{color:var(--amber)}'
    + '.cost-note{padding-top:9px;border-top:1px solid var(--line);color:var(--muted);font-size:.68rem;line-height:1.55}'
    + '.cost-note b{display:block;margin-bottom:4px;color:var(--amber);font:600 8px/1.4 "Chakra Petch",sans-serif;letter-spacing:.16em}'
    + '.cost-note ul{margin:0;padding-left:14px}.cost-note li{margin-bottom:3px}'
    + '@media(max-width:620px){.cost-pop{left:10px;right:10px;bottom:10px;width:auto;max-height:62vh;font-size:.76rem}.cost-total b{font-size:1.3rem}}'
    + '@media(prefers-reduced-motion:reduce){.cost-pop.show{animation:none}}';

  var CHANNEL_BADGES = {
    webrtc: "WEBRTC CHANNEL",
    "pstn-th": "PSTN CHANNEL · TH",
    "pstn-us": "PSTN CHANNEL · US"
  };

  var card = null;
  var elements = {};
  var lastFocus = null;

  function money(value) {
    return "$" + value.toFixed(4);
  }

  function build() {
    if (card) return;
    var style = document.createElement("style");
    style.textContent = STYLE;
    document.head.appendChild(style);

    card = document.createElement("aside");
    card.className = "cost-pop";
    card.id = "cost-pop";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "false");
    card.setAttribute("aria-labelledby", "cost-pop-title");
    card.setAttribute("aria-live", "polite");

    var head = document.createElement("div");
    head.className = "cost-pop-head";
    var headText = document.createElement("div");
    var kicker = document.createElement("small");
    kicker.textContent = "ESTIMATED AWS COST";
    var title = document.createElement("strong");
    title.id = "cost-pop-title";
    title.textContent = "ประมาณการค่าใช้จ่ายของสายนี้";
    headText.append(kicker, title);
    var close = document.createElement("button");
    close.type = "button";
    close.className = "cost-pop-close";
    close.id = "cost-pop-close";
    close.setAttribute("aria-label", "ปิดประมาณการค่าใช้จ่าย");
    close.textContent = "\u00d7";
    close.addEventListener("click", hide);
    head.append(headText, close);

    var channel = document.createElement("span");
    channel.className = "cost-channel";
    channel.id = "cost-channel";

    var total = document.createElement("div");
    total.className = "cost-total";
    var totalValue = document.createElement("b");
    totalValue.id = "cost-total-value";
    totalValue.textContent = "$0.0000";
    var totalMeta = document.createElement("span");
    totalMeta.id = "cost-total-meta";
    totalMeta.textContent = "ต่อสาย";
    total.append(totalValue, totalMeta);

    var rows = document.createElement("div");
    rows.className = "cost-rows";
    rows.id = "cost-rows";

    var note = document.createElement("div");
    note.className = "cost-note";
    var noteTitle = document.createElement("b");
    noteTitle.textContent = "สมมติฐานการคิดราคา";
    var list = document.createElement("ul");
    list.id = "cost-assumptions";
    ASSUMPTIONS.forEach(function (text) {
      var item = document.createElement("li");
      item.textContent = text;
      list.appendChild(item);
    });
    note.append(noteTitle, list);

    card.append(head, channel, total, rows, note);
    document.body.appendChild(card);
    elements = { close: close, channel: channel, totalValue: totalValue, totalMeta: totalMeta, rows: rows };
  }

  function show(seconds, channelKey) {
    build();
    var result = estimate(seconds, channelKey);
    var whole = Math.round(result.minutes * 60);
    elements.channel.textContent = CHANNEL_BADGES[result.channel];
    elements.totalValue.textContent = money(result.total);
    elements.totalMeta.textContent = "ต่อสาย · " + Math.floor(whole / 60) + " นาที "
      + String(whole % 60).padStart(2, "0") + " วินาที";
    elements.rows.replaceChildren.apply(elements.rows, result.rows.map(function (row) {
      var line = document.createElement("div");
      line.className = "cost-row" + (row.assumed ? " is-assumed" : "");
      var label = document.createElement("span");
      label.textContent = row.label;
      var amount = document.createElement("b");
      amount.textContent = money(row.value);
      line.append(label, amount);
      return line;
    }));
    lastFocus = document.activeElement;
    card.classList.add("show");
    window.setTimeout(function () { elements.close.focus({ preventScroll: true }); }, 120);
    return result;
  }

  function hide() {
    if (!card) return;
    card.classList.remove("show");
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && card && card.classList.contains("show")) hide();
  });

  window.FsiCostPopup = { show: show, hide: hide, isOpen: function () { return !!card && card.classList.contains("show"); } };
})();

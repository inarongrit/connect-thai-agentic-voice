/* FSI demo feedback form. Shared by the WebRTC page and the PSTN call page.
   Session context is pre-filled read-only for the tester; the API re-derives
   scenario, channel and model server-side from the Connect contact. */
(function (global) {
  "use strict";

  var SCENARIOS = { bank: "ติดตามสินเชื่อ", insurance: "แนะนำความคุ้มครอง", broker: "อัปเดตการลงทุน" };
  var CHANNELS = { webrtc: "คุยผ่านเว็บไซต์ (WebRTC)", pstn: "สายโทรศัพท์ (PSTN)" };
  var RATINGS = [
    { key: "overall", label: "ความพึงพอใจโดยรวม", required: true },
    { key: "voice", label: "ความเป็นธรรมชาติของเสียง" },
    { key: "understanding", label: "ระบบเข้าใจคำพูดของคุณ" },
    { key: "relevance", label: "คำตอบตรงประเด็น" },
    { key: "latency", label: "ความเร็วในการตอบ" }
  ];
  var COMPLETION = [
    { value: "yes", label: "สำเร็จ" },
    { value: "partial", label: "บางส่วน" },
    { value: "no", label: "ไม่สำเร็จ" }
  ];

  var CSS =
    ".fsi-fb{margin-top:18px;border:1px solid var(--line,rgba(160,220,194,.17));border-radius:18px;padding:18px;background:rgba(8,22,18,.6);font-family:\"IBM Plex Sans Thai\",sans-serif}" +
    ".fsi-fb h3{margin:0 0 4px;font-family:\"Chakra Petch\",sans-serif;font-size:1.02rem;color:var(--paper,#eef8f3);letter-spacing:.02em}" +
    ".fsi-fb .fsi-fb-sub{margin:0 0 14px;font-size:.78rem;color:var(--muted,#8ba79b);line-height:1.5}" +
    ".fsi-fb-ctx{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:16px}" +
    ".fsi-fb-ctx span{display:inline-flex;gap:6px;align-items:baseline;border:1px solid var(--line,rgba(160,220,194,.17));border-radius:999px;padding:5px 11px;font-size:.72rem;color:var(--mint-soft,#b6f5d8);background:rgba(110,235,181,.07)}" +
    ".fsi-fb-ctx span b{color:var(--muted,#8ba79b);font-weight:500}" +
    ".fsi-fb fieldset{border:0;margin:0 0 12px;padding:0}" +
    ".fsi-fb legend{font-size:.78rem;color:var(--paper,#eef8f3);margin-bottom:7px;padding:0}" +
    ".fsi-fb-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:9px;flex-wrap:wrap}" +
    ".fsi-fb-row>span{font-size:.79rem;color:var(--muted,#8ba79b);flex:1 1 150px}" +
    ".fsi-fb-scale{display:flex;gap:5px}" +
    ".fsi-fb-scale label{cursor:pointer}" +
    ".fsi-fb-scale input{position:absolute;opacity:0;width:1px;height:1px}" +
    ".fsi-fb-scale i{display:grid;place-items:center;width:32px;height:32px;border-radius:10px;border:1px solid var(--line,rgba(160,220,194,.17));font-size:.78rem;font-style:normal;color:var(--mint-soft,#b6f5d8);transition:.16s}" +
    ".fsi-fb-scale label:hover i{border-color:var(--line-hot,rgba(104,235,181,.58))}" +
    ".fsi-fb-scale input:focus-visible+i{outline:2px solid var(--mint,#6eebb5);outline-offset:2px}" +
    ".fsi-fb-scale input:checked+i{background:var(--mint,#6eebb5);color:#06231a;border-color:var(--mint,#6eebb5);font-weight:600}" +
    ".fsi-fb textarea{width:100%;box-sizing:border-box;min-height:84px;resize:vertical;border-radius:12px;padding:11px 13px;background:rgba(6,17,14,.75);border:1px solid var(--line,rgba(160,220,194,.17));color:var(--paper,#eef8f3);font-family:inherit;font-size:.85rem;line-height:1.55}" +
    ".fsi-fb textarea:focus{outline:none;border-color:var(--line-hot,rgba(104,235,181,.58))}" +
    ".fsi-fb-send{margin-top:12px;width:100%;border:0;border-radius:13px;padding:13px;background:var(--mint,#6eebb5);color:#06231a;font-family:\"Chakra Petch\",sans-serif;font-size:.95rem;font-weight:600;cursor:pointer;transition:.16s}" +
    ".fsi-fb-send:hover:not(:disabled){filter:brightness(1.08)}" +
    ".fsi-fb-send:disabled{opacity:.55;cursor:not-allowed}" +
    ".fsi-fb-note{margin-top:10px;font-size:.78rem;line-height:1.5;display:none}" +
    ".fsi-fb-note.show{display:block}" +
    ".fsi-fb-note.ok{color:var(--mint,#6eebb5)}" +
    ".fsi-fb-note.bad{color:var(--danger,#ff806e)}" +
    ".fsi-fb.done .fsi-fb-body{display:none}";

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (text != null) node.textContent = text;
    return node;
  }

  function styles() {
    if (document.getElementById("fsi-fb-css")) return;
    var tag = el("style", { id: "fsi-fb-css" });
    tag.textContent = CSS;
    document.head.appendChild(tag);
  }

  function localTime(value) {
    var when = value ? new Date(value) : new Date();
    if (isNaN(when.getTime())) when = new Date();
    return when.toLocaleString("th-TH", { dateStyle: "medium", timeStyle: "short" });
  }

  function chips(context) {
    var wrap = el("div", { class: "fsi-fb-ctx" });
    [
      ["เวลา", localTime(context.startedAt)],
      ["สถานการณ์", SCENARIOS[context.scenario] || context.scenario || "-"],
      ["ชื่อ", context.name || "ลูกค้า"],
      ["ช่องทาง", CHANNELS[context.mode] || context.mode || "-"],
    ].forEach(function (pair) {
      var chip = el("span");
      chip.appendChild(el("b", {}, pair[0]));
      chip.appendChild(document.createTextNode(pair[1]));
      wrap.appendChild(chip);
    });
    return wrap;
  }

  function scale(name, label, required) {
    var row = el("div", { class: "fsi-fb-row", role: "radiogroup", "aria-label": label });
    row.appendChild(el("span", {}, required ? label + " *" : label));
    var group = el("div", { class: "fsi-fb-scale" });
    for (var score = 1; score <= 5; score++) {
      var id = "fsi-" + name + "-" + score;
      var wrap = el("label", { for: id, title: score + " / 5" });
      var input = el("input", { type: "radio", name: name, id: id, value: String(score) });
      wrap.appendChild(input);
      wrap.appendChild(el("i", { "aria-hidden": "true" }, String(score)));
      group.appendChild(wrap);
    }
    row.appendChild(group);
    return row;
  }

  function mount(options) {
    var host = options && options.container;
    if (!host || !options.statusToken) return null;
    styles();
    host.replaceChildren();

    var card = el("div", { class: "fsi-fb" });
    card.appendChild(el("h3", {}, "ให้ความเห็นกับการทดลองครั้งนี้"));
    card.appendChild(el("p", { class: "fsi-fb-sub" }, "ข้อมูลสายด้านล่างบันทึกอัตโนมัติ กรุณาให้คะแนนและความเห็นเพิ่มเติมเท่านั้นค่ะ ความเห็นของคุณอาจถูกเผยแพร่ในระบบติดตามงานสาธารณะเพื่อการปรับปรุง กรุณาไม่กรอกข้อมูลส่วนบุคคลค่ะ"));
    card.appendChild(chips(options.context || {}));

    var body = el("div", { class: "fsi-fb-body" });
    var form = el("form", { novalidate: "novalidate" });

    var ratings = el("fieldset");
    ratings.appendChild(el("legend", {}, "ให้คะแนน 1 (น้อยที่สุด) ถึง 5 (มากที่สุด)"));
    RATINGS.forEach(function (item) { ratings.appendChild(scale(item.key, item.label, item.required)); });
    form.appendChild(ratings);

    var done = el("fieldset");
    done.appendChild(el("legend", {}, "ทำสิ่งที่ต้องการได้สำเร็จหรือไม่"));
    var doneRow = el("div", { class: "fsi-fb-scale", role: "radiogroup", "aria-label": "ผลลัพธ์" });
    COMPLETION.forEach(function (option) {
      var id = "fsi-completed-" + option.value;
      var wrap = el("label", { for: id });
      wrap.appendChild(el("input", { type: "radio", name: "completed", id: id, value: option.value }));
      var pill = el("i", { "aria-hidden": "true" }, option.label);
      pill.style.width = "auto";
      pill.style.padding = "0 13px";
      wrap.appendChild(pill);
      doneRow.appendChild(wrap);
    });
    done.appendChild(doneRow);
    form.appendChild(done);

    var comments = el("fieldset");
    comments.appendChild(el("legend", {}, "ความเห็นหรือสิ่งที่ควรปรับปรุง"));
    var textarea = el("textarea", {
      name: "comment",
      maxlength: "1000",
      placeholder: "เช่น เสียงพูดเร็วเกินไป ระบบฟังเลขไม่ชัด หรือควรถามสั้นลง"
    });
    comments.appendChild(textarea);
    form.appendChild(comments);

    var send = el("button", { class: "fsi-fb-send", type: "submit" }, "ส่งความเห็น");
    form.appendChild(send);
    body.appendChild(form);
    card.appendChild(body);

    var note = el("p", { class: "fsi-fb-note", role: "status", "aria-live": "polite" });
    card.appendChild(note);
    host.appendChild(card);

    function picked(name) {
      var hit = form.querySelector('input[name="' + name + '"]:checked');
      return hit ? hit.value : "";
    }

    function say(kind, message) {
      note.className = "fsi-fb-note show " + kind;
      note.textContent = message;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!picked("overall")) {
        say("bad", "กรุณาให้คะแนนความพึงพอใจโดยรวมก่อนส่งค่ะ");
        var first = form.querySelector('input[name="overall"]');
        if (first) first.focus();
        return;
      }
      var payload = { action: "feedback", statusToken: options.statusToken, comment: textarea.value.trim() };
      RATINGS.forEach(function (item) {
        var value = picked(item.key);
        if (value) payload[item.key] = Number(value);
      });
      if (picked("completed")) payload.completed = picked("completed");

      send.disabled = true;
      say("ok", "กำลังส่งความเห็น…");
      fetch(options.api || "/call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
        .then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (data) {
            if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
            return data;
          });
        })
        .then(function () {
          card.classList.add("done");
          say("ok", "ขอบคุณค่ะ บันทึกความเห็นเรียบร้อยแล้ว");
          if (typeof options.onSent === "function") options.onSent();
        })
        .catch(function (error) {
          send.disabled = false;
          say("bad", "ส่งความเห็นไม่สำเร็จ: " + (error.message || "กรุณาลองอีกครั้ง"));
        });
    });

    return { card: card };
  }

  global.FsiFeedback = { mount: mount };
})(window);

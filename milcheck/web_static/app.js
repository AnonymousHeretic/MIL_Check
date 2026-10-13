// MIL-Check 웹 MVP 화면 동작. 판정 로직은 없다(서버 규칙 엔진만 판정한다).
// 모든 기능은 스크립트 없이도 양식 제출로 동작하며, 여기서는 편의 기능만 더한다.
(function () {
  document.documentElement.classList.add("js");

  // 처리 중 화면: 실제 진행률이 아니라 서버 응답을 기다리는 동안 단계 이름을 순서대로 보여 준다.
  function showLoading(kind) {
    var box = document.getElementById("loading-" + kind);
    if (!box) return;
    box.hidden = false;
    var items = box.querySelectorAll(".lsteps li");
    var i = 0;
    function tick() {
      items.forEach(function (li, k) {
        li.classList.toggle("ok", k < i);
        li.classList.toggle("on", k === i);
        li.querySelector(".dot").textContent = k < i ? "✓" : String(k + 1);
      });
      if (i < items.length - 1) { i += 1; setTimeout(tick, 700); }
    }
    tick();
  }
  document.querySelectorAll("form[data-loading]").forEach(function (form) {
    form.addEventListener("submit", function () { showLoading(form.dataset.loading); });
  });
  window.addEventListener("pageshow", function () {
    document.querySelectorAll(".overlay").forEach(function (o) { o.hidden = true; });
  });

  // 01 문서 입력: 올린 파일 목록과 문서 유형 선택
  var input = document.getElementById("files");
  var list = document.getElementById("filelist");
  var tpl = document.getElementById("filerow");
  var drop = document.getElementById("drop");
  var direct = document.querySelector("textarea[name=direct_text]");
  function guess(name) {
    var rules = [["견적", "quote"], ["호환", "compatibility_statement"], ["공급", "supplier_confirmation"],
      ["확인서", "supplier_confirmation"], ["요구", "request"], ["요청", "request"]];
    for (var k = 0; k < rules.length; k++) if (name.indexOf(rules[k][0]) >= 0) return rules[k][1];
    return "request";
  }
  function summary() {
    var n = input && input.files ? input.files.length : 0;
    var s = document.getElementById("summary");
    if (s) s.textContent = "문서 " + n + "건" + (direct && direct.value.trim() ? " + 직접 입력" : "") +
      " · 처리 시간은 문서 수에 따라 달라집니다";
  }
  function render() {
    if (!input || !list || !tpl) return;
    list.innerHTML = "";
    Array.prototype.forEach.call(input.files, function (f, i) {
      var row = tpl.content.firstElementChild.cloneNode(true);
      row.querySelector(".fname").textContent = f.name;
      var sel = row.querySelector("select");
      sel.name = "doctype_" + i;
      sel.value = guess(f.name);
      list.appendChild(row);
    });
    if (!input.files.length) list.innerHTML = '<p class="muted small">올린 문서가 없습니다.</p>';
    summary();
  }
  if (input) input.addEventListener("change", render);
  if (direct) direct.addEventListener("input", summary);
  if (drop && input) {
    ["dragenter", "dragover"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
    });
    drop.addEventListener("drop", function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      var dt = new DataTransfer();
      Array.prototype.forEach.call(input.files, function (f) { dt.items.add(f); });
      Array.prototype.forEach.call(e.dataTransfer.files, function (f) { dt.items.add(f); });
      input.files = dt.files;
      render();
    });
  }

  // 02 추출 항목 확인
  var form = document.getElementById("confirm-form");
  if (form) {
    var price = document.getElementById("price");
    var ack = document.getElementById("ack");
    var judge = document.getElementById("judge");
    var remaining = document.getElementById("remaining");
    document.getElementById("toggle-edit").addEventListener("click", function () {
      document.body.classList.toggle("editing");
    });
    // 화면에서 현재 확정한 값: 직접 입력 > 선택한 충돌 후보 > 단일 후보
    function currentValue(field) {
      var m = form.querySelector("[name='m:" + field + "']");
      if (m && m.value.trim()) return m.value.trim();
      var picked = form.querySelector("[name='f:" + field + "']:checked") ||
        form.querySelector("input[type=hidden][name='f:" + field + "']");
      return picked ? (picked.dataset.value || "") : "";
    }
    // 추정가격 적용: '부가세 여부'가 부가세 별도(제외)로 확정된 경우에만 복사한다(1.1 환산 없음).
    var applyBtn = document.querySelector("[data-apply-price]");
    var priceMsg = document.getElementById("price-msg");
    if (applyBtn) applyBtn.addEventListener("click", function () {
      if (currentValue("tax_status") !== "vat_excluded") {
        priceMsg.textContent = "부가세 여부가 '부가세 별도(제외)'로 확정된 경우에만 적용할 수 있습니다. " +
          "부가세 포함·미상이면 부가세 제외 금액을 직접 입력하십시오.";
        return;
      }
      priceMsg.textContent = "";
      var v = applyBtn.dataset.applyPrice;
      if (v === "from-manual") v = currentValue("estimated_price_krw");
      v = (v || "").replace(/[^0-9]/g, "");
      if (v) { price.value = v; check(); }
    });
    function check() {
      var left = [];
      var groups = {};
      form.querySelectorAll("input[data-conflict]").forEach(function (r) {
        groups[r.dataset.conflict] = groups[r.dataset.conflict] || r.checked;
      });
      Object.keys(groups).forEach(function (f) {
        var m = form.querySelector("[name='m:" + f + "']");
        if (!groups[f] && !(m && m.value.trim())) left.push("충돌 항목 선택");
      });
      if (!price.value.trim()) left.push("부가세 제외 추정가격 입력");
      if (!ack.checked) left.push("원문 대조 확인 체크");
      left = left.filter(function (v, i) { return left.indexOf(v) === i; });
      remaining.textContent = left.length ? "남은 확인: " + left.join(" · ") : "";
      judge.disabled = left.length > 0;
    }
    // 선택한 수의계약 근거에 해당하는 증빙 행만 보여 준다(다른 근거의 증빙은 판정에 쓰이지 않는다).
    function basisRows() {
      var basis = "";
      var m = form.querySelector("[name='m:sole_source_basis']");
      var picked = form.querySelector("[name='f:sole_source_basis'][type=hidden], [name='f:sole_source_basis']:checked");
      if (m && m.value && (document.body.classList.contains("editing") || !picked)) basis = m.value;
      else if (picked) basis = picked.dataset.value || "";
      form.querySelectorAll("tr[data-bases]").forEach(function (tr) {
        tr.hidden = !!basis && tr.dataset.bases.split(" ").indexOf(basis) < 0;
      });
    }
    form.addEventListener("change", basisRows);
    basisRows();
    form.addEventListener("input", check);
    form.addEventListener("change", check);
    check();
  }

  // 03·04 결과서 생성 · PDF 저장(인쇄) · 확정본 발행
  document.querySelectorAll("[data-show]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".preview").forEach(function (p) {
        p.classList.toggle("show", p.dataset.doc === btn.dataset.show);
      });
      document.querySelectorAll(".tab").forEach(function (t, i) {
        t.classList.toggle("on", i === 3);
        t.classList.toggle("done", i < 3);
      });
      var shown = document.querySelector(".preview.show");
      if (shown) shown.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  // 인쇄(PDF로 저장): 결과서만 별도 영역에 복제해 A4로 인쇄한다.
  function printDoc(kind) {
    var doc = document.getElementById("doc-" + kind);
    if (!doc) return;
    var root = document.getElementById("print-root");
    if (!root) { root = document.createElement("div"); root.id = "print-root"; document.body.appendChild(root); }
    root.innerHTML = "";
    var copy = doc.cloneNode(true);
    copy.removeAttribute("id");
    root.appendChild(copy);
    document.body.classList.add("printing");
    window.print();
    document.body.classList.remove("printing");
    root.innerHTML = "";
  }
  document.querySelectorAll("[data-print]").forEach(function (btn) {
    btn.addEventListener("click", function () { printDoc(btn.dataset.print); });
  });
  document.querySelectorAll("[data-final]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var doc = document.getElementById("doc-" + btn.dataset.final);
      var stamp = doc && doc.querySelector("[data-stamp]");
      if (stamp) { stamp.textContent = "확정본 · 담당자 서명 전"; stamp.classList.add("final"); }
      printDoc(btn.dataset.final);
    });
  });
})();

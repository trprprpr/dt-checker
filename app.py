import streamlit as st
import anthropic
import json
import pandas as pd
from pypdf import PdfReader
import io
import base64
from PIL import Image

st.set_page_config(page_title="Сверка документов", page_icon="🔍", layout="wide")

api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
if not api_key:
    st.error("API ключ не найден. Добавь ANTHROPIC_API_KEY в Secrets.")
    st.stop()

client = anthropic.Anthropic(api_key=api_key)

# ── Helpers ───────────────────────────────────────────────────────────────────
def read_pdf_text(f):
    r = PdfReader(f)
    return "\n".join(p.extract_text() or "" for p in r.pages)

def read_excel(f):
    return pd.read_excel(f).to_string(index=False)

def file_to_b64(f):
    return base64.standard_b64encode(f.read()).decode("utf-8")

def render_disc(d):
    sev = d.get("severity", "info")
    bg  = {"critical":"#ffebee","major":"#fff8e1","info":"#e3f2fd"}.get(sev,"#f5f5f5")
    br  = {"critical":"#c62828","major":"#f9a825","info":"#1565c0"}.get(sev,"#999")
    lb  = {"critical":"🔴 Критичное","major":"🟡 Существенное","info":"🔵 Информационное"}.get(sev,"")
    st.markdown(
        '<div style="background:' + bg + ';border-radius:8px;padding:1rem;'
        'margin-bottom:0.75rem;border-left:4px solid ' + br + '">'
        '<b>' + d.get("field","") + '</b> &nbsp;<span style="font-size:0.8rem;opacity:0.7">' + lb + '</span><br><br>'
        '<span style="color:#555">В target:</span> <b style="color:#c62828">' + d.get("target_value","—") + '</b><br>'
        '<span style="color:#555">В source (' + d.get("source_doc","") + '):</span> <b style="color:#2e7d32">' + d.get("source_value","—") + '</b><br>'
        '<span style="color:#777;font-size:0.9rem">' + d.get("comment","") + '</span></div>',
        unsafe_allow_html=True
    )

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs([
    "📋 Сверка ДТ",
    "📄 Текст: инструкция vs скан",
    "🎨 Макеты упаковки"
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — ДТ
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("Автоматическая сверка таможенной декларации")
    st.caption("Загрузи четыре документа поставки — система найдёт расхождения за 30 секунд")

    c1, c2 = st.columns(2)
    with c1:
        dt_file  = st.file_uploader("📄 Декларация на товары (ДТ)", type="pdf", key="dt")
        inv_file = st.file_uploader("📄 Инвойс (Invoice)", type="pdf", key="inv")
    with c2:
        pl_file  = st.file_uploader("📄 Упаковочный лист (Packing List)", type="pdf", key="pl")
        exp_file = st.file_uploader("📊 Отчёт EXPORT (Excel)", type=["xlsx","xls"], key="exp")

    all_ok = all([dt_file, inv_file, pl_file, exp_file])
    if not all_ok:
        missing = [n for f,n in [(dt_file,"ДТ"),(inv_file,"Инвойс"),(pl_file,"Упаковочный лист"),(exp_file,"EXPORT")] if not f]
        st.info("Загрузи ещё: " + ", ".join(missing))

    if st.button("🚀 Запустить проверку ДТ", disabled=not all_ok, type="primary", use_container_width=True, key="btn_dt"):
        with st.spinner("Читаю документы..."):
            dt_text  = read_pdf_text(dt_file)
            inv_text = read_pdf_text(inv_file)
            pl_text  = read_pdf_text(pl_file)
            exp_text = read_excel(exp_file)

        with st.spinner("Анализирую расхождения... (~30–40 сек)"):
            SYSTEM_DT = """Ты — эксперт по таможенному оформлению импортных поставок в Россию.
Сверь данные в ДТ с инвойсом, упаковочным листом и EXPORT. Найди расхождения.
Верни ТОЛЬКО валидный JSON без markdown:
{
  "invoice_number":"...","dt_number":"...",
  "summary":{"total_checks":0,"discrepancies_found":0,"status":"ok"},
  "discrepancies":[{"field":"...","target_value":"...","source_value":"...","source_doc":"...","severity":"critical","comment":"..."}],
  "checks":[{"field":"...","target_value":"...","source_value":"...","source_doc":"...","status":"ok"}]
}
Severity: critical=адрес/сумма/кол-во/код, major=вес/условия, info=формат.
Проверь: адрес отправителя (гр.2), получатель (гр.8), номер/дата инвойса, номер PL,
условия поставки, страна происхождения, код ТН ВЭД, артикул, наименование, серия,
срок годности, кол-во единиц, вес брутто/нетто по позиции и итого, цена товара,
таможенная стоимость, сумма инвойса, валюта, пошлина 2010, НДС 5010, сбор 1010,
кол-во поддонов, производитель, регистрационное удостоверение."""

            content = "=== ДТ ===\n" + dt_text + "\n\n=== ИНВОЙС ===\n" + inv_text + "\n\n=== PL ===\n" + pl_text + "\n\n=== EXPORT ===\n" + exp_text
            msg = client.messages.create(model="claude-opus-4-5", max_tokens=4096,
                system=SYSTEM_DT, messages=[{"role":"user","content":content}])

        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()

        try:
            result = json.loads(raw)
        except Exception:
            st.error("Не удалось разобрать ответ. Попробуй ещё раз.")
            st.code(raw)
            st.stop()

        summary = result.get("summary", {})
        n_err   = summary.get("discrepancies_found", 0)
        n_total = summary.get("total_checks", 0)

        st.divider()
        st.markdown("### Результат · Инвойс " + str(result.get("invoice_number","—")))
        st.caption("ДТ: " + str(result.get("dt_number","—")))
        c1, c2, c3 = st.columns(3)
        c1.metric("Расхождений", n_err)
        c2.metric("Проверок", n_total)
        c3.metric("Совпадений", n_total - n_err)

        discs = result.get("discrepancies", [])
        if discs:
            st.divider()
            st.markdown("### ⚠️ Найденные расхождения")
            discs.sort(key=lambda x: {"critical":0,"major":1,"info":2}.get(x.get("severity","info"),2))
            for d in discs: render_disc(d)
        else:
            st.success("✅ Расхождений не найдено.")

        checks = result.get("checks", [])
        if checks:
            st.divider()
            with st.expander("📋 Полная таблица проверок"):
                st.dataframe(pd.DataFrame([{
                    "Статус":"✅" if c.get("status")=="ok" else "❌",
                    "Поле":c.get("field",""), "В ДТ":c.get("target_value",""),
                    "Источник":c.get("source_value",""), "Документ":c.get("source_doc","")}
                    for c in checks]), use_container_width=True, hide_index=True)

            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                pd.DataFrame([{"Статус":"OK" if c.get("status")=="ok" else "ОШИБКА",
                    "Поле":c.get("field",""), "В ДТ":c.get("target_value",""),
                    "Эталон":c.get("source_value",""), "Источник":c.get("source_doc","")}
                    for c in checks]).to_excel(w, index=False, sheet_name="Сверка")
            buf.seek(0)
            st.divider()
            st.download_button("⬇️ Скачать отчёт Excel", buf,
                file_name="sverka_" + result.get("dt_number","report").replace("/","_") + ".xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ТЕКСТ: ИНСТРУКЦИЯ vs СКАН
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("Сравнение текста: макет инструкции vs скан Росздрава")
    st.caption("Загрузи эталонный документ (source) и проверяемый документ (target) — система найдёт текстовые расхождения")

    st.markdown("##### Source — эталон (макет инструкции)")
    source_text_file = st.file_uploader(
        "PDF или изображение (PNG, JPG)", type=["pdf","png","jpg","jpeg"], key="src_text",
        help="Утверждённый макет инструкции или регистрационное досье")

    st.markdown("##### Target — проверяемый документ (скан из Росздрава)")
    target_text_file = st.file_uploader(
        "PDF или изображение (PNG, JPG)", type=["pdf","png","jpg","jpeg"], key="tgt_text",
        help="Скан документа из Росздравнадзора или другого регулятора")

    if st.button("🔍 Сравнить тексты", disabled=not(source_text_file and target_text_file),
                 type="primary", use_container_width=True, key="btn_text"):

        def prepare_content(f, label):
            name = f.name.lower()
            if name.endswith(".pdf"):
                f.seek(0)
                text = read_pdf_text(f)
                if text.strip():
                    return [{"type":"text","text": label + ":\n" + text}]
                else:
                    return [{"type":"text","text": label + ": (PDF не содержит текстового слоя, распознавание недоступно)"}]
            else:
                mime = "image/png" if name.endswith(".png") else "image/jpeg"
                f.seek(0)
                b64 = file_to_b64(f)
                return [
                    {"type":"text","text": label + ":"},
                    {"type":"image","source":{"type":"base64","media_type":mime,"data":b64}}
                ]

        with st.spinner("Анализирую документы... (~30–60 сек)"):
            source_content = prepare_content(source_text_file, "SOURCE (эталон)")
            target_text_file.seek(0)
            target_content = prepare_content(target_text_file, "TARGET (проверяемый)")

            SYSTEM_TEXT = """Ты — эксперт по фармацевтической документации. 
Тебе даны два документа: SOURCE (эталон) и TARGET (проверяемый).
Извлеки весь текст из обоих документов и найди все текстовые расхождения.
Верни ТОЛЬКО валидный JSON без markdown:
{
  "summary":{"total_checks":0,"discrepancies_found":0,"status":"ok"},
  "discrepancies":[{
    "field":"название раздела/элемента",
    "source_value":"текст в эталоне",
    "target_value":"текст в проверяемом",
    "severity":"critical",
    "comment":"пояснение"
  }],
  "checks":[{
    "field":"название раздела/элемента",
    "source_value":"текст в эталоне",
    "target_value":"текст в проверяемом",
    "status":"ok"
  }]
}
Severity: critical=другое содержание/пропущен раздел, major=другая формулировка, info=пунктуация/регистр."""

            msg = client.messages.create(
                model="claude-opus-4-5", max_tokens=4096, system=SYSTEM_TEXT,
                messages=[{"role":"user","content": source_content + target_content}])

        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()

        try:
            result = json.loads(raw)
        except Exception:
            st.error("Не удалось разобрать ответ. Попробуй ещё раз.")
            st.code(raw)
            st.stop()

        summary = result.get("summary", {})
        n_err   = summary.get("discrepancies_found", 0)
        n_total = summary.get("total_checks", 0)

        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Расхождений", n_err)
        c2.metric("Проверено элементов", n_total)
        c3.metric("Совпадений", n_total - n_err)

        discs = result.get("discrepancies", [])
        if discs:
            st.markdown("### ⚠️ Найденные текстовые расхождения")
            discs.sort(key=lambda x: {"critical":0,"major":1,"info":2}.get(x.get("severity","info"),2))
            for d in discs: render_disc(d)
        else:
            st.success("✅ Тексты совпадают.")

        checks = result.get("checks", [])
        if checks:
            with st.expander("📋 Полная таблица сравнения"):
                st.dataframe(pd.DataFrame([{
                    "Статус":"✅" if c.get("status")=="ok" else "❌",
                    "Элемент":c.get("field",""),
                    "Эталон (source)":c.get("source_value",""),
                    "Проверяемый (target)":c.get("target_value","")}
                    for c in checks]), use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — МАКЕТЫ УПАКОВКИ
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("Сравнение макетов упаковки")
    st.caption("Загрузи два изображения упаковки — система найдёт визуальные и текстовые расхождения")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### Source — эталонный макет")
        source_pack = st.file_uploader(
            "PNG, JPG или PDF", type=["png","jpg","jpeg","pdf"], key="src_pack")
        if source_pack:
            if not source_pack.name.lower().endswith(".pdf"):
                st.image(source_pack, caption="Source", use_container_width=True)

    with c2:
        st.markdown("##### Target — проверяемый макет")
        target_pack = st.file_uploader(
            "PNG, JPG или PDF", type=["png","jpg","jpeg","pdf"], key="tgt_pack")
        if target_pack:
            if not target_pack.name.lower().endswith(".pdf"):
                st.image(target_pack, caption="Target", use_container_width=True)

    if st.button("🎨 Сравнить макеты", disabled=not(source_pack and target_pack),
                 type="primary", use_container_width=True, key="btn_pack"):

        def pack_to_vision(f):
            name = f.name.lower()
            if name.endswith(".pdf"):
                f.seek(0)
                text = read_pdf_text(f)
                if text.strip():
                    return [{"type":"text","text":"Макет (PDF, текстовый слой):\n" + text}]
                else:
                    return [{"type":"text","text":"Макет (PDF): (PDF не содержит текстового слоя, распознавание недоступно)"}]
            else:
                mime = "image/png" if name.endswith(".png") else "image/jpeg"
                f.seek(0)
                b64 = file_to_b64(f)
                return [{"type":"image","source":{"type":"base64","media_type":mime,"data":b64}}]

        with st.spinner("Сравниваю макеты... (~30–60 сек)"):
            SYSTEM_PACK = """Ты — эксперт по контролю качества фармацевтической упаковки.
Тебе даны два изображения упаковки: SOURCE (эталон) и TARGET (проверяемый).
Найди все визуальные и текстовые расхождения между ними.

Проверь: название препарата, дозировка, МНН, состав, штрихкод/QR-код, 
серия и срок годности (формат поля), способ применения, условия хранения,
производитель, регистрационный номер, цвета и цветовые зоны, логотипы,
предупреждения и пиктограммы, шрифты и размеры текста.

Верни ТОЛЬКО валидный JSON без markdown:
{
  "summary":{"total_checks":0,"discrepancies_found":0,"status":"ok"},
  "discrepancies":[{
    "field":"элемент упаковки",
    "source_value":"в эталоне",
    "target_value":"в проверяемом",
    "severity":"critical",
    "comment":"пояснение"
  }],
  "checks":[{
    "field":"элемент упаковки",
    "source_value":"в эталоне",
    "target_value":"в проверяемом",
    "status":"ok"
  }]
}
Severity: critical=название/дозировка/штрихкод/рег.номер, major=состав/условия хранения/производитель, info=цвет/шрифт/пиктограмма."""

            source_content = [{"type":"text","text":"SOURCE (эталон):"}] + pack_to_vision(source_pack)
            target_pack.seek(0)
            target_content = [{"type":"text","text":"TARGET (проверяемый):"}] + pack_to_vision(target_pack)

            msg = client.messages.create(
                model="claude-opus-4-5", max_tokens=4096, system=SYSTEM_PACK,
                messages=[{"role":"user","content": source_content + target_content}])

        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()

        try:
            result = json.loads(raw)
        except Exception:
            st.error("Не удалось разобрать ответ. Попробуй ещё раз.")
            st.code(raw)
            st.stop()

        summary = result.get("summary", {})
        n_err   = summary.get("discrepancies_found", 0)
        n_total = summary.get("total_checks", 0)

        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Расхождений", n_err)
        c2.metric("Проверено элементов", n_total)
        c3.metric("Совпадений", n_total - n_err)

        discs = result.get("discrepancies", [])
        if discs:
            st.markdown("### ⚠️ Найденные расхождения на упаковке")
            discs.sort(key=lambda x: {"critical":0,"major":1,"info":2}.get(x.get("severity","info"),2))
            for d in discs: render_disc(d)
        else:
            st.success("✅ Макеты совпадают.")

        checks = result.get("checks", [])
        if checks:
            with st.expander("📋 Полная таблица сравнения элементов"):
                st.dataframe(pd.DataFrame([{
                    "Статус":"✅" if c.get("status")=="ok" else "❌",
                    "Элемент":c.get("field",""),
                    "Эталон":c.get("source_value",""),
                    "Проверяемый":c.get("target_value","")}
                    for c in checks]), use_container_width=True, hide_index=True)

            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                pd.DataFrame([{"Статус":"OK" if c.get("status")=="ok" else "ОШИБКА",
                    "Элемент":c.get("field",""),
                    "Эталон":c.get("source_value",""),
                    "Проверяемый":c.get("target_value","")}
                    for c in checks]).to_excel(w, index=False, sheet_name="Упаковка")
            buf.seek(0)
            st.divider()
            st.download_button("⬇️ Скачать отчёт Excel", buf,
                file_name="packaging_check.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)

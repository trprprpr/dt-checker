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
def parse_json_response(raw):
    """Parse JSON, recovering truncated responses by closing open structures."""
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Try to recover truncated JSON: find last complete object in checks/discrepancies
    # Strategy: trim to last complete '}' at top level and close open arrays/objects
    truncated = raw.rstrip()
    # Remove trailing incomplete string/object
    # Find the last complete '}' that closes a checks item
    last_close = truncated.rfind('},')
    if last_close == -1:
        last_close = truncated.rfind('}')
    if last_close > 0:
        candidate = truncated[:last_close + 1]
        # Count open brackets to close them
        opens = candidate.count('[') - candidate.count(']')
        braces = candidate.count('{') - candidate.count('}')
        candidate += ']' * opens + '}' * braces
        try:
            return json.loads(candidate)
        except Exception:
            pass
    return None


def read_pdf_text(f):
    r = PdfReader(f)
    text = "\n".join(p.extract_text() or "" for p in r.pages)
    # Remove null bytes and control characters that cause Anthropic API 400 errors
    return ''.join(c for c in text if c >= ' ' or c in '\n\r\t')

def read_excel(f):
    return pd.read_excel(f).to_string(index=False)

def file_to_b64(f):
    return base64.standard_b64encode(f.read()).decode("utf-8")

def image_to_b64(f):
    """Return (base64_str, mime_type), resizing image if it exceeds 4 MB."""
    f.seek(0)
    raw = f.read()
    name = f.name.lower()
    mime = "image/png" if name.endswith(".png") else "image/jpeg"
    MAX = 4 * 1024 * 1024
    if len(raw) <= MAX:
        return base64.standard_b64encode(raw).decode("utf-8"), mime
    img = Image.open(io.BytesIO(raw))
    scale = (MAX / len(raw)) ** 0.5
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG" if name.endswith(".png") else "JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8"), mime

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
Верни ТОЛЬКО валидный JSON без markdown. Включай в "checks" ТОЛЬКО поля со статусом "error".
Поля где всё совпадает — не включай вообще, это экономит место.

{
  "invoice_number": "...", "dt_number": "...",
  "summary": {"total_checks": 0, "discrepancies_found": 0, "status": "ok"},
  "discrepancies": [
    {
      "field": "название поля",
      "source_value": "значение в эталоне",
      "target_value": "значение в проверяемом",
      "source_doc": "инвойс/PL/EXPORT",
      "severity": "critical",
      "comment": "пояснение"
    }
  ]
}
Severity: critical=адрес/сумма/кол-во/код, major=вес/условия, info=формат.
Проверь: адрес отправителя (гр.2), получатель (гр.8), номер/дата инвойса, номер PL,
условия поставки, страна происхождения, код ТН ВЭД, артикул, наименование, серия,
срок годности, кол-во единиц, вес брутто/нетто по позиции и итого, цена товара,
таможенная стоимость, сумма инвойса, валюта, пошлина 2010, НДС 5010, сбор 1010,
кол-во поддонов, производитель, регистрационное удостоверение."""

            content = "=== ДТ ===\n" + dt_text + "\n\n=== ИНВОЙС ===\n" + inv_text + "\n\n=== PL ===\n" + pl_text + "\n\n=== EXPORT ===\n" + exp_text
            msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=16384,
                system=SYSTEM_DT, messages=[{"role":"user","content":content}])

        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()

        result = parse_json_response(raw)
        if result is None:
            st.error("Не удалось разобрать ответ. Попробуй ещё раз.")
            st.code(raw)
            st.stop()
        if msg.stop_reason == "max_tokens":
            st.warning("⚠️ Ответ был обрезан — показаны частичные результаты.")

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
                b64, mime = image_to_b64(f)
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
Верни ТОЛЬКО валидный JSON без markdown. Включай в "checks" ТОЛЬКО поля со статусом "error".
Поля где всё совпадает — не включай вообще, это экономит место.

{
  "summary": {"total_checks": 0, "discrepancies_found": 0, "status": "ok"},
  "discrepancies": [
    {
      "field": "название поля",
      "source_value": "значение в эталоне",
      "target_value": "значение в проверяемом",
      "severity": "critical",
      "comment": "пояснение"
    }
  ]
}
Severity: critical=другое содержание/пропущен раздел, major=другая формулировка, info=пунктуация/регистр."""

            msg = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=16384, system=SYSTEM_TEXT,
                messages=[{"role":"user","content": source_content + target_content}])

        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()

        result = parse_json_response(raw)
        if result is None:
            st.error("Не удалось разобрать ответ. Попробуй ещё раз.")
            st.code(raw)
            st.stop()
        if msg.stop_reason == "max_tokens":
            st.warning("⚠️ Ответ был обрезан — показаны частичные результаты.")

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

        def pack_to_vision(f, label=""):
            name = f.name.lower()
            f.seek(0)
            if name.endswith(".pdf"):
                text = read_pdf_text(f)
                content = []
                if label:
                    content.append({"type":"text","text": label + ":"})
                if text.strip():
                    content.append({"type":"text","text": text})
                else:
                    content.append({"type":"text","text": "(PDF не содержит текстового слоя)"})
                return content
            else:
                mime = "image/png" if name.endswith(".png") else "image/jpeg"
                f.seek(0)
                b64 = file_to_b64(f)
                content = []
                if label:
                    content.append({"type":"text","text": label + ":"})
                content.append({"type":"image","source":{"type":"base64","media_type":mime,"data":b64}})
                return content

        with st.spinner("Сравниваю макеты... (~30–60 сек)"):
            SYSTEM_PACK = """Ты — эксперт по контролю качества фармацевтической упаковки.
Тебе даны два изображения упаковки: SOURCE (эталон) и TARGET (проверяемый).
Найди все визуальные и текстовые расхождения между ними.

Проверь: название препарата, дозировка, МНН, состав, штрихкод/QR-код, 
серия и срок годности (формат поля), способ применения, условия хранения,
производитель, регистрационный номер, цвета и цветовые зоны, логотипы,
предупреждения и пиктограммы, шрифты и размеры текста.

Верни ТОЛЬКО валидный JSON без markdown. Включай в "checks" ТОЛЬКО поля со статусом "error".
Поля где всё совпадает — не включай вообще, это экономит место.

{
  "summary": {"total_checks": 0, "discrepancies_found": 0, "status": "ok"},
  "discrepancies": [
    {
      "field": "название поля",
      "source_value": "значение в эталоне",
      "target_value": "значение в проверяемом",
      "severity": "critical",
      "comment": "пояснение"
    }
  ]
}
Severity: critical=название/дозировка/штрихкод/рег.номер, major=состав/условия хранения/производитель, info=цвет/шрифт/пиктограмма."""

            source_content = [{"type":"text","text":"SOURCE (эталон):"}] + pack_to_vision(source_pack)
            target_pack.seek(0)
            target_content = [{"type":"text","text":"TARGET (проверяемый):"}] + pack_to_vision(target_pack)

            msg = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=16384, system=SYSTEM_PACK,
                messages=[{"role":"user","content": source_content + target_content}])

        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()

        result = parse_json_response(raw)
        if result is None:
            st.error("Не удалось разобрать ответ. Попробуй ещё раз.")
            st.code(raw)
            st.stop()
        if msg.stop_reason == "max_tokens":
            st.warning("⚠️ Ответ был обрезан — показаны частичные результаты.")

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


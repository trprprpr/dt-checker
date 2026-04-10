import streamlit as st
import anthropic
import json
import pandas as pd
from pypdf import PdfReader
import io

st.set_page_config(page_title="Сверка ДТ", page_icon="🔍", layout="wide")

api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
if not api_key:
    st.error("API ключ не найден. Добавь ANTHROPIC_API_KEY в Secrets.")
    st.stop()

st.title("🔍 Автоматическая сверка ДТ")
st.caption("Загрузи четыре документа поставки — система найдёт расхождения за 30 секунд")
st.divider()

col1, col2 = st.columns(2)
with col1:
    dt_file  = st.file_uploader("📄 Декларация на товары (ДТ)", type="pdf")
    inv_file = st.file_uploader("📄 Инвойс (Invoice)", type="pdf")
with col2:
    pl_file  = st.file_uploader("📄 Упаковочный лист (Packing List)", type="pdf")
    exp_file = st.file_uploader("📊 Отчёт EXPORT (Excel)", type=["xlsx","xls"])

def read_pdf(f):
    r = PdfReader(f)
    return "\n".join(p.extract_text() or "" for p in r.pages)

def read_excel(f):
    df = pd.read_excel(f)
    return df.to_string(index=False)

SYSTEM = """Ты — эксперт по таможенному оформлению импортных поставок в Россию.
Тебе дают тексты четырёх документов одной поставки: ДТ, Инвойс, Упаковочный лист, EXPORT.
Сверь данные в ДТ с остальными документами и найди расхождения.

Верни ТОЛЬКО валидный JSON без пояснений и без markdown:

{
  "invoice_number": "...",
  "dt_number": "...",
  "summary": {
    "total_checks": 0,
    "discrepancies_found": 0,
    "status": "ok"
  },
  "discrepancies": [
    {
      "field": "...",
      "dt_value": "...",
      "source_value": "...",
      "source_doc": "...",
      "severity": "critical",
      "comment": "..."
    }
  ],
  "checks": [
    {
      "field": "...",
      "dt_value": "...",
      "source_value": "...",
      "source_doc": "...",
      "status": "ok"
    }
  ]
}

Severity: critical = адрес/сумма/количество/код, major = вес/условия, info = формат.

Проверь: адрес отправителя (гр.2), адрес получателя (гр.8), номер и дата инвойса,
номер PL, условия поставки, страна происхождения, код ТН ВЭД, артикул, наименование,
серия (batch), срок годности, количество единиц, вес брутто по позиции, вес нетто
по позиции, вес брутто итого, вес нетто итого, цена товара, таможенная стоимость,
общая сумма инвойса, валюта, пошлина 2010 (ставка и сумма), НДС 5010 (ставка и сумма),
сбор 1010, кол-во поддонов, производитель, регистрационное удостоверение."""

st.divider()
all_ok = all([dt_file, inv_file, pl_file, exp_file])

if not all_ok:
    missing = [n for f, n in [(dt_file,"ДТ"),(inv_file,"Инвойс"),(pl_file,"Упаковочный лист"),(exp_file,"EXPORT")] if not f]
    st.info(f"Загрузи ещё: {', '.join(missing)}")

if st.button("🚀 Запустить проверку", disabled=not all_ok, type="primary", use_container_width=True):
    with st.spinner("Читаю документы..."):
        dt_text  = read_pdf(dt_file)
        inv_text = read_pdf(inv_file)
        pl_text  = read_pdf(pl_file)
        exp_text = read_excel(exp_file)

    with st.spinner("Анализирую расхождения... (~30–40 сек)"):
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            system=SYSTEM,
            messages=[{"role": "user", "content": f"""=== ДТ ===\n{dt_text}\n\n=== ИНВОЙС ===\n{inv_text}\n\n=== УПАКОВОЧНЫЙ ЛИСТ ===\n{pl_text}\n\n=== EXPORT ===\n{exp_text}"""}]
        )

    raw = msg.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
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
    st.markdown(f"### Результат · Инвойс {result.get('invoice_number','—')}")
    st.caption(f"ДТ: {result.get('dt_number','—')}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Расхождений", n_err)
    c2.metric("Проверок всего", n_total)
    c3.metric("Совпадений", n_total - n_err)

    discrepancies = result.get("discrepancies", [])
    if discrepancies:
        st.divider()
        st.markdown("### ⚠️ Найденные расхождения")
        order = {"critical": 0, "major": 1, "info": 2}
        discrepancies.sort(key=lambda x: order.get(x.get("severity","info"), 2))
        for d in discrepancies:
            sev = d.get("severity","info")
            color = {"critical":"#ffebee","major":"#fff8e1","info":"#e3f2fd"}.get(sev,"#f5f5f5")
            border = {"critical":"#c62828","major":"#f9a825","info":"#1565c0"}.get(sev,"#999")
            label = {"critical":"🔴 Критичное","major":"🟡 Существенное","info":"🔵 Информационное"}.get(sev,"")
            st.markdown(f"""<div style="background:{color};border-radius:8px;padding:1rem;mar

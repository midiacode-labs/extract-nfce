import base64
import html
import logging
import mimetypes
import os
import tempfile
import time
import uuid
from typing import Any, Dict

import streamlit as st
import streamlit.components.v1 as components

from services.invoice_qualification_service import InvoiceQualificationService
from services.openai_invoice_service import OpenAIInvoiceExtractionService
from services.invoice_service import InvoiceExtractionService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STATE_DEFAULTS: Dict[str, Any] = {
    "input_mode": "upload",
    "extraction_method": "textract",
    "image_bytes": None,
    "image_name": None,
    "image_mime": "image/jpeg",
    "temp_input_path": None,
    "show_preview": True,
    "extracted_data": None,
    "extracted_output_path": None,
    "extraction_duration": None,
    "qualified_data": None,
    "qualified_output_path": None,
    "qualification_duration": None,
    "workflow_duration": None,
    "extraction_usage": None,
    "qualification_usage": None,
    "workflow_usage": None,
    "last_processed_method": None,
    "widget_nonce": 0,
    "camera_zoom_level": 1.8,
}

EXTRACTION_METHOD_LABELS = {
    "textract": "AWS Textract + OpenAI",
    "openai": "OpenAI somente",
}


def init_state() -> None:
    """Initializes the session state used by the Streamlit app."""
    for key, value in STATE_DEFAULTS.items():
        st.session_state.setdefault(key, value)


def reset_workflow() -> None:
    """Clears the current workflow and resets upload widgets."""
    current_nonce = st.session_state.get("widget_nonce", 0)
    for key in list(STATE_DEFAULTS.keys()):
        if key != "widget_nonce":
            st.session_state.pop(key, None)
    st.session_state["widget_nonce"] = current_nonce + 1
    st.rerun()


def format_duration(seconds: float) -> str:
    """Formats a duration in a friendly way for the UI."""
    if seconds < 60:
        return f"{seconds:.2f} segundos"

    minutes = int(seconds // 60)
    remaining_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes} min {remaining_seconds:.1f} seg"

    hours = minutes // 60
    remaining_minutes = minutes % 60
    return f"{hours} h {remaining_minutes} min"


def format_token_count(value: Any) -> str:
    """Formats token counters for display in metrics."""
    if value in (None, ""):
        return "—"
    return f"{int(value):,}".replace(",", ".")


def format_usd_cost(value: Any) -> str:
    """Formats estimated USD cost values for display."""
    if value in (None, ""):
        return "—"
    return f"US$ {float(value):.6f}"


def format_page_count(value: Any) -> str:
    """Formats page counters for display."""
    if value in (None, ""):
        return "—"
    return str(int(value))


def combine_usage_summaries(*summaries: Dict[str, Any] | None) -> Dict[str, Any] | None:
    """Combines provider usage summaries into a workflow total."""
    available = [summary for summary in summaries if summary]
    if not available:
        return None

    openai_summaries = [
        summary for summary in available if summary.get("provider") == "openai"
    ]
    aws_summaries = [
        summary for summary in available if summary.get("provider") == "aws_textract"
    ]

    return {
        "components": available,
        "model": ", ".join(
            summary.get("model", "") for summary in openai_summaries if summary.get("model")
        ),
        "input_tokens": (
            sum(int(summary.get("input_tokens") or 0) for summary in openai_summaries)
            if openai_summaries
            else None
        ),
        "output_tokens": (
            sum(int(summary.get("output_tokens") or 0) for summary in openai_summaries)
            if openai_summaries
            else None
        ),
        "total_tokens": (
            sum(int(summary.get("total_tokens") or 0) for summary in openai_summaries)
            if openai_summaries
            else None
        ),
        "openai_estimated_cost_usd": (
            sum(float(summary.get("estimated_cost_usd") or 0.0) for summary in openai_summaries)
            if openai_summaries
            else None
        ),
        "aws_textract_page_count": (
            sum(int(summary.get("page_count") or 0) for summary in aws_summaries)
            if aws_summaries
            else None
        ),
        "aws_textract_estimated_cost_usd": (
            sum(float(summary.get("estimated_cost_usd") or 0.0) for summary in aws_summaries)
            if aws_summaries
            else None
        ),
        "total_estimated_cost_usd": sum(
            float(summary.get("estimated_cost_usd") or 0.0) for summary in available
        ),
    }


def persist_uploaded_file(uploaded_file) -> str:
    """Persists the uploaded invoice image to a temporary file path."""
    suffix = os.path.splitext(uploaded_file.name or "invoice.jpg")[1] or ".jpg"
    temp_dir = os.path.join(tempfile.gettempdir(), "extract_nfce_streamlit")
    os.makedirs(temp_dir, exist_ok=True)

    file_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}{suffix}")
    with open(file_path, "wb") as file_handle:
        file_handle.write(uploaded_file.getbuffer())
    return file_path


def set_selected_image(uploaded_file) -> bool:
    """Stores the current uploaded or camera-captured image in session state."""
    if uploaded_file is None:
        return False

    image_bytes = uploaded_file.getvalue()
    current_bytes = st.session_state.get("image_bytes")
    if current_bytes == image_bytes and st.session_state.get("temp_input_path"):
        return False

    mime_type = uploaded_file.type or mimetypes.guess_type(uploaded_file.name or "")[0]

    st.session_state["image_bytes"] = image_bytes
    st.session_state["image_name"] = uploaded_file.name or "invoice.jpg"
    st.session_state["image_mime"] = mime_type or "image/jpeg"
    st.session_state["temp_input_path"] = persist_uploaded_file(uploaded_file)
    st.session_state["show_preview"] = True

    st.session_state["extracted_data"] = None
    st.session_state["extracted_output_path"] = None
    st.session_state["extraction_duration"] = None
    st.session_state["qualified_data"] = None
    st.session_state["qualified_output_path"] = None
    st.session_state["qualification_duration"] = None
    st.session_state["workflow_duration"] = None
    st.session_state["extraction_usage"] = None
    st.session_state["qualification_usage"] = None
    st.session_state["workflow_usage"] = None
    st.session_state["last_processed_method"] = None
    return True


def build_extraction_service(method: str):
    """Builds the extraction backend selected in the UI."""
    if method == "openai":
        return OpenAIInvoiceExtractionService()
    return InvoiceExtractionService()


def run_extraction() -> None:
    """Runs the Textract invoice extraction service for the current image."""
    input_path = st.session_state.get("temp_input_path")
    if not input_path:
        st.warning("Envie ou capture uma imagem da nota fiscal primeiro.")
        return

    service = build_extraction_service(st.session_state.get("extraction_method", "textract"))
    started_at = time.perf_counter()
    extracted_data, output_path = service.process_image(input_file=input_path)
    elapsed = time.perf_counter() - started_at

    st.session_state["extracted_data"] = extracted_data
    st.session_state["extracted_output_path"] = output_path
    st.session_state["extraction_duration"] = elapsed
    st.session_state["extraction_usage"] = getattr(service, "get_last_usage", lambda: None)()


def run_qualification() -> None:
    """Runs the JSON qualification service on the extracted payload."""
    extracted_data = st.session_state.get("extracted_data")
    output_path = st.session_state.get("extracted_output_path")
    if not extracted_data or not output_path:
        st.warning("Execute a etapa de extração antes de qualificar o JSON.")
        return

    started_at = time.perf_counter()
    if st.session_state.get("extraction_method") == "openai":
        qualified_data, qualified_output_path = InvoiceQualificationService.save_qualified_snapshot(
            extracted_data,
            source_output_path=output_path,
        )
        st.session_state["qualification_usage"] = None
    else:
        service = InvoiceQualificationService()
        qualified_data, qualified_output_path = service.qualify_and_save(
            extracted_data,
            source_output_path=output_path,
        )
        st.session_state["qualification_usage"] = service.get_last_usage()
    elapsed = time.perf_counter() - started_at

    st.session_state["qualified_data"] = qualified_data
    st.session_state["qualified_output_path"] = qualified_output_path
    st.session_state["qualification_duration"] = elapsed


def run_full_workflow() -> None:
    """Runs extraction and qualification sequentially for the current image."""
    started_at = time.perf_counter()
    run_extraction()
    run_qualification()
    st.session_state["workflow_duration"] = time.perf_counter() - started_at
    st.session_state["workflow_usage"] = combine_usage_summaries(
        st.session_state.get("extraction_usage"),
        st.session_state.get("qualification_usage"),
    )
    st.session_state["last_processed_method"] = st.session_state.get("extraction_method")


def render_image_preview() -> None:
    """Renders the current image preview with a zoom slider."""
    image_bytes = st.session_state.get("image_bytes")
    if not image_bytes:
        return

    if not st.session_state.get("show_preview", True):
        st.caption("Pré-visualização oculta.")
        return

    st.subheader("Pré-visualização da nota")
    zoom_percent = st.slider("Zoom", min_value=60, max_value=200, value=100, step=10)

    encoded_image = base64.b64encode(image_bytes).decode("utf-8")
    mime_type = st.session_state.get("image_mime", "image/jpeg")
    image_html = f"""
    <div
        style="overflow:auto; max-height:70vh; border:1px solid #d9d9d9;
               border-radius:12px; padding:12px; background:#fafafa;"
    >
        <img
            src="data:{mime_type};base64,{encoded_image}"
            alt="Uploaded invoice"
            style="width:{zoom_percent}%; max-width:none; display:block;
                   margin:0 auto; border-radius:8px;"
        />
    </div>
    """
    st.markdown(image_html, unsafe_allow_html=True)


def render_camera_capture_assist() -> None:
    st.caption(
            "Ajuste o zoom e use o guia A4 para enquadrar sem aproximar demais a câmera. "
            "O zoom depende do suporte do navegador/dispositivo."
    )
    st.slider(
            "Zoom da câmera (quando suportado)",
            min_value=1.0,
            max_value=3.0,
            value=float(st.session_state.get("camera_zoom_level", 1.8)),
            step=0.1,
            key="camera_zoom_level",
            help="Em geral, valores entre 1.5x e 2.0x ajudam no foco para papel A4.",
    )

    st.markdown(
            "- Afaste um pouco o celular até o papel ficar nítido.\n"
            "- Centralize a folha dentro do retângulo A4.\n"
            "- Evite sombras sobre o documento."
    )

    target_zoom = float(st.session_state.get("camera_zoom_level", 1.8))
    assist_script = f"""
    <script>
        (() => {{
            const targetZoom = {target_zoom:.1f};
            const A4_RATIO = 1.0 / 1.4142;

            const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

            const ensureGuide = (video) => {{
                const container = video.parentElement;
                if (!container) return;
                if (container.querySelector('.nfce-a4-guide')) return;

                container.style.position = 'relative';

                const guide = parent.document.createElement('div');
                guide.className = 'nfce-a4-guide';
                guide.style.position = 'absolute';
                guide.style.left = '50%';
                guide.style.top = '50%';
                guide.style.transform = 'translate(-50%, -50%)';
                guide.style.width = '75%';
                guide.style.maxWidth = '420px';
                guide.style.aspectRatio = `${{A4_RATIO}}`;
                guide.style.border = '2px dashed rgba(56, 189, 248, 0.95)';
                guide.style.borderRadius = '12px';
                guide.style.boxShadow = '0 0 0 9999px rgba(15, 23, 42, 0.22)';
                guide.style.pointerEvents = 'none';
                guide.style.zIndex = '30';

                const label = parent.document.createElement('div');
                label.textContent = 'Guia A4';
                label.style.position = 'absolute';
                label.style.top = '-28px';
                label.style.left = '0';
                label.style.padding = '4px 8px';
                label.style.borderRadius = '999px';
                label.style.background = 'rgba(15, 23, 42, 0.88)';
                label.style.color = '#f8fafc';
                label.style.font = (
                    '600 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
                );
                guide.appendChild(label);

                container.appendChild(guide);
            }};

            const applyTrackTuning = async (track) => {{
                if (!track || !track.getCapabilities || !track.applyConstraints) return;

                const capabilities = track.getCapabilities();
                const advanced = {{}};

                if (capabilities.zoom) {{
                    const min = capabilities.zoom.min ?? 1.0;
                    const max = capabilities.zoom.max ?? targetZoom;
                    advanced.zoom = clamp(targetZoom, min, max);
                }}

                if (Array.isArray(capabilities.focusMode)) {{
                    if (capabilities.focusMode.includes('continuous')) {{
                        advanced.focusMode = 'continuous';
                    }} else if (capabilities.focusMode.includes('single-shot')) {{
                        advanced.focusMode = 'single-shot';
                    }}
                }}

                const constraints = {{
                    width: {{ ideal: 4096 }},
                    height: {{ ideal: 2160 }},
                }};

                if (Object.keys(advanced).length) {{
                    constraints.advanced = [advanced];
                }}

                try {{
                    await track.applyConstraints(constraints);
                }} catch (_error) {{
                    // Silently ignore unsupported constraints.
                }}
            }};

            const scanAndEnhance = () => {{
                const videos = parent.document.querySelectorAll('video');
                videos.forEach((video) => {{
                    ensureGuide(video);
                    const stream = video.srcObject;
                    if (!stream || !stream.getVideoTracks) return;
                    const [track] = stream.getVideoTracks();
                    if (!track) return;
                    applyTrackTuning(track);
                }});
            }};

            scanAndEnhance();
            const timerId = setInterval(scanAndEnhance, 1000);
            window.addEventListener('beforeunload', () => clearInterval(timerId));
        }})();
    </script>
    """
    components.html(assist_script, height=0)


def format_label(value: str) -> str:
    """Converts snake_case field names into readable labels."""
    return value.replace("_", " ").strip().title()


def format_brl_currency(value: Any) -> str:
    """Formats numeric-like values using Brazilian Real conventions."""
    if value in (None, ""):
        return "—"

    text = str(value).strip()
    if not text:
        return "—"

    number = InvoiceExtractionService.parse_decimal(text.replace("R$", "").strip())
    if number is None:
        return html.escape(text)

    formatted = f"R$ {number:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def format_display_value(key: str, value: Any) -> str:
    """Formats values for display, including BRL money fields."""
    if value in (None, ""):
        return "—"

    money_fields = {"amount", "total_amount", "price", "unit_price"}
    if key.lower() in money_fields:
        return format_brl_currency(value)

    return html.escape(str(value))


def render_info_table(title: str, data: Dict[str, Any]) -> None:
    """Renders a friendly HTML table for a JSON section."""
    st.markdown(f"#### {title}")
    if not data:
        st.info("Nenhum dado detectado nesta seção.")
        return

    rows = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            continue
        display_value = format_display_value(key, value)
        rows.append(
            "<tr>"
            f"<td style='padding:8px; font-weight:600; width:38%;'>{format_label(key)}</td>"
            f"<td style='padding:8px;'>{display_value}</td>"
            "</tr>"
        )

    table_html = """
    <div style="border:1px solid #e6e6e6; border-radius:12px; overflow:hidden; margin-bottom:12px;">
        <table style="width:100%; border-collapse:collapse;">
            <tbody>{rows}</tbody>
        </table>
    </div>
    """.replace("{rows}", "".join(rows))
    st.markdown(table_html, unsafe_allow_html=True)


def render_items_table(items: list[Dict[str, Any]]) -> None:
    """Renders the detected invoice items in a friendly table."""
    st.markdown("#### Itens")
    if not items:
        st.info("Nenhum item detectado.")
        return

    normalized_items = []
    for index, item in enumerate(items, start=1):
        normalized_items.append(
            {
                "#": index,
                "Descrição": item.get("description", "—"),
                "Qtd": item.get("quantity", "—"),
                "Valor Unit.": format_brl_currency(item.get("unit_price", "—")),
                "Valor Total": format_brl_currency(item.get("total_price", "—")),
            }
        )

    st.table(normalized_items)


def render_result_panel(
    title: str,
    data: Dict[str, Any] | None,
    duration: float | None,
    output_path: str | None,
    empty_message: str,
    usage_summary: Dict[str, Any] | None = None,
    method_label: str | None = None,
) -> None:
    """Displays a result block with friendly sections and optional raw JSON."""
    st.subheader(title)
    if data is None:
        st.info(empty_message)
        return

    if duration is not None:
        st.success(f"Concluído em {format_duration(duration)}.")
    if method_label:
        st.caption(f"Método: {method_label}")

    summary_column_1, summary_column_2 = st.columns(2)
    with summary_column_1:
        st.metric("Itens detectados", len(data.get("items", [])))
    with summary_column_2:
        consumer_name = data.get("consumer", {}).get("name") or "Não informado"
        st.metric("Consumidor", consumer_name)

    if usage_summary:
        st.caption("Custos estimados com adicional de 37%.")

        cost_column_1, cost_column_2, cost_column_3 = st.columns(3)
        with cost_column_1:
            st.metric(
                "Custo estimado AWS Textract",
                format_usd_cost(usage_summary.get("aws_textract_estimated_cost_usd")),
            )
        with cost_column_2:
            st.metric(
                "Custo estimado OpenAI",
                format_usd_cost(usage_summary.get("openai_estimated_cost_usd")),
            )
        with cost_column_3:
            st.metric(
                "Custo estimado total",
                format_usd_cost(usage_summary.get("total_estimated_cost_usd")),
            )

        detail_column_1, detail_column_2, detail_column_3 = st.columns(3)
        with detail_column_1:
            st.metric(
                "Páginas Textract",
                format_page_count(usage_summary.get("aws_textract_page_count")),
            )
        with detail_column_2:
            st.metric("Input tokens", format_token_count(usage_summary.get("input_tokens")))
        with detail_column_3:
            st.metric("Output tokens", format_token_count(usage_summary.get("output_tokens")))

    render_info_table("Emitente", data.get("emitter", {}))
    render_info_table("Identificação", data.get("identification", {}))
    render_info_table("Consumidor", data.get("consumer", {}))
    render_info_table("Totais", data.get("totals", {}))
    render_info_table("Chave de Acesso", data.get("access_key", {}))
    render_info_table("Cálculo do Imposto", data.get("tax_calculation", {}))
    render_info_table("Transporte", data.get("transport", {}))
    render_info_table("Mensagem Fiscal", data.get("fiscal_message", {}))
    render_info_table("Informações Adicionais", data.get("additional_info", {}))
    render_items_table(data.get("items", []))

    with st.expander("Exibir JSON bruto", expanded=False):
        st.json(data)

    if output_path:
        st.caption(f"Salvo em: {output_path}")


def render_qualified_result() -> None:
    """Displays only the qualified result after processing completes."""
    render_result_panel(
        title="Resultado qualificado",
        data=st.session_state.get("qualified_data"),
        duration=st.session_state.get("workflow_duration"),
        output_path=st.session_state.get("qualified_output_path"),
        empty_message="O resultado qualificado aparecerá aqui após o processamento da imagem.",
        usage_summary=st.session_state.get("workflow_usage"),
        method_label=EXTRACTION_METHOD_LABELS.get(
            st.session_state.get("extraction_method", "textract"),
            st.session_state.get("extraction_method", "textract"),
        ),
    )


def main() -> None:
    """Runs the Streamlit UI for invoice extraction and qualification."""
    st.set_page_config(page_title="Extrator de Notas Fiscais", page_icon="🧾", layout="wide")
    init_state()

    st.title("🧾 Extrator de Notas Fiscais")
    st.write(
        "Siga os passos abaixo para enviar uma imagem da nota fiscal "
        "e revisar os dados qualificados."
    )
    nonce = st.session_state.get("widget_nonce", 0)

    st.markdown("### Passo 1. Escolha o método de envio")
    st.radio(
        "Como você deseja enviar a imagem da nota fiscal?",
        options=["upload", "camera"],
        format_func=lambda option: "Enviar arquivo" if option == "upload" else "Usar câmera",
        key="input_mode",
        horizontal=True,
    )

    st.markdown("### Passo 2. Escolha o método de extração")
    st.radio(
        "Qual fluxo deve ser usado para extrair a nota fiscal?",
        options=["textract", "openai"],
        format_func=lambda option: EXTRACTION_METHOD_LABELS.get(option, option),
        key="extraction_method",
        horizontal=True,
    )

    st.markdown("### Passo 3. Envie a imagem")
    selected_file = None
    if st.session_state.get("input_mode") == "upload":
        selected_file = st.file_uploader(
            "Enviar imagem da nota fiscal",
            type=["jpg", "jpeg", "png"],
            key=f"file_uploader_{nonce}",
        )
    else:
        render_camera_capture_assist()
        selected_file = st.camera_input(
            "Tire uma foto da nota fiscal",
            key=f"camera_input_{nonce}",
        )

    if selected_file is None and st.session_state.get("image_bytes") is None:
        st.info("Escolha um método de envio e envie a imagem da nota fiscal para continuar.")

    try:
        should_run = set_selected_image(selected_file)
        if (
            not should_run
            and st.session_state.get("temp_input_path")
            and st.session_state.get("last_processed_method")
            != st.session_state.get("extraction_method")
        ):
            should_run = True

        if should_run:
            with st.spinner("Enviando e processando a imagem da nota fiscal..."):
                run_full_workflow()
    except Exception as exc:
        logging.exception("Invoice workflow failed: %s", exc)
        st.session_state["last_processed_method"] = None
        st.error(f"Falha no processamento da nota fiscal: {exc}")

    if st.session_state.get("image_bytes"):
        st.markdown("### Passo 4. Revisar foto")
        st.toggle("Exibir pré-visualização", key="show_preview")
        render_image_preview()

    if st.session_state.get("qualified_data") is not None:
        st.markdown("### Passo 5. Resultado qualificado")
        render_qualified_result()

        st.divider()
        st.markdown("### Passo 6. Recomeçar")
        if st.button("Recomeçar", use_container_width=True):
            reset_workflow()


if __name__ == "__main__":
    main()

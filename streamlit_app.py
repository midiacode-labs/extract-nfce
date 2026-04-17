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

from services.invoice_qualification_service import InvoiceQualificationService
from services.invoice_service import InvoiceExtractionService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STATE_DEFAULTS: Dict[str, Any] = {
    "input_mode": "upload",
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
    "widget_nonce": 0,
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
        return f"{seconds:.2f} seconds"

    minutes = int(seconds // 60)
    remaining_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes} min {remaining_seconds:.1f} sec"

    hours = minutes // 60
    remaining_minutes = minutes % 60
    return f"{hours} h {remaining_minutes} min"


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
    return True


def run_extraction() -> None:
    """Runs the Textract invoice extraction service for the current image."""
    input_path = st.session_state.get("temp_input_path")
    if not input_path:
        st.warning("Please upload or capture an invoice image first.")
        return

    service = InvoiceExtractionService()
    started_at = time.perf_counter()
    extracted_data, output_path = service.process_image(input_file=input_path)
    elapsed = time.perf_counter() - started_at

    st.session_state["extracted_data"] = extracted_data
    st.session_state["extracted_output_path"] = output_path
    st.session_state["extraction_duration"] = elapsed


def run_qualification() -> None:
    """Runs the JSON qualification service on the extracted payload."""
    extracted_data = st.session_state.get("extracted_data")
    output_path = st.session_state.get("extracted_output_path")
    if not extracted_data or not output_path:
        st.warning("Run the extraction step before qualifying the JSON.")
        return

    service = InvoiceQualificationService()
    started_at = time.perf_counter()
    qualified_data, qualified_output_path = service.qualify_and_save(
        extracted_data,
        source_output_path=output_path,
    )
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


def render_image_preview() -> None:
    """Renders the current image preview with a zoom slider."""
    image_bytes = st.session_state.get("image_bytes")
    if not image_bytes:
        return

    if not st.session_state.get("show_preview", True):
        st.caption("Photo preview hidden.")
        return

    st.subheader("Invoice preview")
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

    cleaned = text.replace("R$", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        number = float(cleaned)
    except ValueError:
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
        st.info("No data detected in this section.")
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
    st.markdown("#### Items")
    if not items:
        st.info("No items detected.")
        return

    normalized_items = []
    for index, item in enumerate(items, start=1):
        normalized_items.append(
            {
                "#": index,
                "Description": item.get("description", "—"),
                "Amount": format_brl_currency(item.get("total_price", "—")),
                "Quantity": item.get("quantity", "—"),
            }
        )

    st.table(normalized_items)


def render_result_panel(
    title: str,
    data: Dict[str, Any] | None,
    duration: float | None,
    output_path: str | None,
    empty_message: str,
) -> None:
    """Displays a result block with friendly sections and optional raw JSON."""
    st.subheader(title)
    if data is None:
        st.info(empty_message)
        return

    if duration is not None:
        st.success(f"Completed in {format_duration(duration)}.")

    summary_column_1, summary_column_2 = st.columns(2)
    with summary_column_1:
        st.metric("Detected items", len(data.get("items", [])))
    with summary_column_2:
        consumer_name = data.get("consumer", {}).get("name") or "Not informed"
        st.metric("Consumer", consumer_name)

    render_info_table("Emitter", data.get("emitter", {}))
    render_info_table("Identification", data.get("identification", {}))
    render_info_table("Consumer", data.get("consumer", {}))
    render_info_table("Totals", data.get("totals", {}))
    render_info_table("Access Key", data.get("access_key", {}))
    render_info_table("Tax Calculation", data.get("tax_calculation", {}))
    render_info_table("Transport", data.get("transport", {}))
    render_info_table("Fiscal Message", data.get("fiscal_message", {}))
    render_info_table("Additional Info", data.get("additional_info", {}))
    render_items_table(data.get("items", []))

    with st.expander("Show raw JSON", expanded=False):
        st.json(data)

    if output_path:
        st.caption(f"Saved to: {output_path}")


def render_qualified_result() -> None:
    """Displays only the qualified result after processing completes."""
    render_result_panel(
        title="Qualified result",
        data=st.session_state.get("qualified_data"),
        duration=st.session_state.get("workflow_duration"),
        output_path=st.session_state.get("qualified_output_path"),
        empty_message="The qualified result will appear here after the image is processed.",
    )


def main() -> None:
    """Runs the Streamlit UI for invoice extraction and qualification."""
    st.set_page_config(page_title="Invoice Extractor UI", page_icon="🧾", layout="wide")
    init_state()

    st.title("🧾 Invoice Extractor UI")
    st.write(
        "Follow the guided steps to send an invoice image and review the qualified data."
    )
    nonce = st.session_state.get("widget_nonce", 0)

    st.markdown("### Step 1. Choose Input Method")
    st.radio(
        "How would you like to provide the invoice image?",
        options=["upload", "camera"],
        format_func=lambda option: "Upload file" if option == "upload" else "Use camera",
        key="input_mode",
        horizontal=True,
    )

    st.markdown("### Step 2. Provide The Image")
    selected_file = None
    if st.session_state.get("input_mode") == "upload":
        selected_file = st.file_uploader(
            "Upload invoice image",
            type=["jpg", "jpeg", "png"],
            key=f"file_uploader_{nonce}",
        )
    else:
        selected_file = st.camera_input(
            "Take a photo of the invoice",
            key=f"camera_input_{nonce}",
        )

    if selected_file is None and st.session_state.get("image_bytes") is None:
        st.info("Choose an input method and send the invoice image to continue.")

    try:
        if set_selected_image(selected_file):
            with st.spinner("Uploading and processing the invoice image..."):
                run_full_workflow()
    except Exception as exc:
        logging.exception("Invoice workflow failed: %s", exc)
        st.error(f"Invoice workflow failed: {exc}")

    if st.session_state.get("image_bytes"):
        st.markdown("### Step 3. Review Photo")
        st.toggle("Show photo preview", key="show_preview")
        render_image_preview()

    if st.session_state.get("qualified_data") is not None:
        st.markdown("### Step 4. Qualified Result")
        render_qualified_result()

        st.divider()
        st.markdown("### Step 5. Start Over")
        if st.button("Reset", use_container_width=True):
            reset_workflow()


if __name__ == "__main__":
    main()

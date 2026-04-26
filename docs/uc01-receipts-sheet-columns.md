# UC01 Receipts Sheet Columns

Phase-1 n8n maps the automation-processor response into a Google Sheet with these columns:

| Column | Purpose |
| --- | --- |
| `record_id` | Processor `stored_record_id` for the normalized receipt record. |
| `created_at` | Timestamp when n8n writes the row. |
| `source_channel` | Origin channel, e.g. Gmail or Telegram. |
| `source_message_id` | Source message/email id when available. |
| `source_sender` | Sender identity captured by n8n. |
| `source_filename` | Original uploaded/attached filename. |
| `drive_file_id` | Google Drive file id for the archived receipt. |
| `drive_web_url` | Google Drive web URL for reviewer access. |
| `merchant` | Extracted merchant/vendor name. |
| `receipt_date` | Extracted receipt date in ISO format. |
| `amount` | Extracted total amount. |
| `currency` | Extracted or hinted currency. |
| `tax_amount` | Extracted VAT/tax amount when available. |
| `payment_method` | Deterministic payment method hint. |
| `category_suggestion` | Processor category suggestion. |
| `category_reason` | Reason/note behind the category suggestion. |
| `confidence_score` | Processor overall confidence. |
| `review_required` | Whether human review is required. |
| `review_reasons` | Machine-readable review reason codes. |
| `duplicate_flag` | Whether the receipt looks like a duplicate. |
| `status` | Row/workflow status. |
| `raw_text_excerpt` | Short excerpt of extracted raw text for audit/debug. |

The processor stays credential-free: Google/Gmail/Drive/Sheets/Telegram/ClickUp credentials remain in n8n or their dedicated integrations, not in `services/automation-processor`.

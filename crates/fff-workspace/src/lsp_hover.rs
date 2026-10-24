//! Bounded hover content remains source data, never commands or mutation plans.
use crate::coordinates::{Utf16Position, from_utf16_position};
use serde_json::{Value, json};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn part(value: &Value, bytes: &mut usize, max_bytes: usize) -> Result<Value> {
    let (format, language, text) = if let Some(text) = value.as_str() {
        ("markdown", None, text)
    } else if let Some(kind) = value.get("kind").and_then(Value::as_str) {
        if !matches!(kind, "markdown" | "plaintext") {
            return Err("unsupported hover markup kind".into());
        }
        (
            kind,
            None,
            value["value"].as_str().ok_or("invalid hover text")?,
        )
    } else {
        let language = value["language"].as_str().ok_or("invalid hover content")?;
        if language.is_empty() || language.len() > 64 {
            return Err("invalid hover language label".into());
        }
        (
            "code",
            Some(language),
            value["value"].as_str().ok_or("invalid hover text")?,
        )
    };
    *bytes += text.len();
    if *bytes > max_bytes {
        return Err("hover text exceeds max_bytes; no partial result returned".into());
    }
    let mut result = json!({"format":format,"text":text});
    if let Some(language) = language {
        result["language"] = json!(language);
    }
    Ok(result)
}

pub(crate) fn hover(path: &str, source: &str, value: Value, max_bytes: usize) -> Result<Value> {
    if value.is_null() {
        return Ok(json!({"path":path,"hover":null,"text_bytes":0}));
    }
    let content = value.get("contents").ok_or("missing hover contents")?;
    let mut bytes = 0;
    let parts = match content {
        Value::Array(values) => {
            if values.len() > 32 {
                return Err("hover contains more than 32 parts".into());
            }
            values
                .iter()
                .map(|value| part(value, &mut bytes, max_bytes))
                .collect::<Result<Vec<_>>>()?
        }
        value => vec![part(value, &mut bytes, max_bytes)?],
    };
    let mut result = json!({"contents":parts});
    if let Some(range) = value.get("range") {
        let start: Utf16Position = serde_json::from_value(range["start"].clone())?;
        let end: Utf16Position = serde_json::from_value(range["end"].clone())?;
        let start_byte = from_utf16_position(source, start)?;
        let end_byte = from_utf16_position(source, end)?;
        if start_byte > end_byte {
            return Err("reversed hover range".into());
        }
        result["range"] = json!({"start_byte":start_byte,"end_byte":end_byte,
            "start_line":start.line+1,"end_line":end.line+1});
    }
    Ok(json!({"path":path,"hover":result,"text_bytes":bytes}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hover_preserves_formats_text_and_original_ranges() {
        let content = json!({"contents":["**docs**",{"language":"rust","value":"let café: u32"}],
            "range":{"start":{"line":0,"character":2},"end":{"line":0,"character":6}}});
        let result = hover("lib.rs", "🚀café\r\n", content, 100).unwrap();
        assert_eq!(result["hover"]["contents"][0]["format"], "markdown");
        assert_eq!(result["hover"]["contents"][1]["language"], "rust");
        assert_eq!(result["hover"]["range"]["start_byte"], 4);
        assert_eq!(result["hover"]["range"]["end_byte"], 9);
        assert_eq!(
            result["text_bytes"],
            "**docs**".len() + "let café: u32".len()
        );
        let plain = hover(
            "lib.rs",
            "",
            json!({"contents":{"kind":"plaintext","value":"<script> source data"}}),
            100,
        )
        .unwrap();
        assert_eq!(
            plain["hover"]["contents"][0]["text"],
            "<script> source data"
        );
        assert!(hover("lib.rs", "", Value::Null, 1).unwrap()["hover"].is_null());
    }

    #[test]
    fn hover_rejects_overflow_malformed_content_and_invalid_ranges() {
        assert!(hover("lib.rs", "", json!({"contents":"é"}), 1).is_err());
        for content in [
            json!(vec![""; 33]),
            json!({"kind":"html","value":"x"}),
            json!({"language":"x".repeat(65),"value":"x"}),
            json!(42),
        ] {
            assert!(hover("lib.rs", "", json!({"contents":content}), 100).is_err());
        }
        assert!(hover("lib.rs", "", json!({}), 100).is_err());
        for (start, end) in [(1, 2), (3, 2), (0, 5)] {
            let value = json!({"contents":"x","range":{"start":{"line":0,"character":start},"end":{"line":0,"character":end}}});
            assert!(hover("lib.rs", "🚀a", value, 100).is_err());
        }
    }
}

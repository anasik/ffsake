//! Primary native diagnostics are incomplete analysis data, never fixes.
use crate::coordinates::{Utf16Position, from_utf16_position};
use serde_json::{Value, json};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

/// One all-or-nothing budget across primary reports from the same snapshot.
pub(crate) struct Batch {
    reports: Vec<Value>,
    limit: usize,
    count: usize,
    bytes: usize,
}

impl Batch {
    pub(crate) fn new(limit: usize) -> Self {
        Self {
            reports: Vec::new(),
            limit,
            count: 0,
            bytes: 0,
        }
    }

    pub(crate) fn push(&mut self, path: &str, source: &str, value: Value) -> Result<()> {
        if self.reports.len() >= crate::semantic::MAX_SOURCE_FILES {
            return Err("workspace diagnostics exceeds 64 files".into());
        }
        let report = diagnostics(path, source, value, self.limit - self.count)?;
        let count = self.count
            + report["diagnostic_count"]
                .as_u64()
                .ok_or("invalid diagnostic count")? as usize;
        let bytes = self.bytes
            + report["text_bytes"]
                .as_u64()
                .ok_or("invalid diagnostic bytes")? as usize;
        if bytes > 65_536 {
            return Err(
                "workspace diagnostic text budget exceeded; no partial result returned".into(),
            );
        }
        self.count = count;
        self.bytes = bytes;
        self.reports.push(report);
        Ok(())
    }

    pub(crate) fn finish(self) -> Value {
        json!({"reports":self.reports,"file_count":self.reports.len(),
            "diagnostic_count":self.count,"text_bytes":self.bytes})
    }
}

fn text<'a>(value: &'a Value, maximum: usize, bytes: &mut usize) -> Result<&'a str> {
    let text = value.as_str().ok_or("invalid diagnostic text")?;
    *bytes += text.len();
    if text.is_empty() || text.len() > maximum || *bytes > 65_536 {
        return Err("diagnostic text budget exceeded; no partial result returned".into());
    }
    Ok(text)
}

pub(crate) fn diagnostics(path: &str, source: &str, value: Value, limit: usize) -> Result<Value> {
    if value["kind"] != "full" {
        return Err("expected a fresh full diagnostic report".into());
    }
    if let Some(related) = value.get("relatedDocuments")
        && !related.is_null()
        && related.as_object().is_none_or(|items| !items.is_empty())
    {
        return Err("related diagnostic documents were not negotiated".into());
    }
    let items = value["items"]
        .as_array()
        .ok_or("invalid diagnostic items")?;
    if items.len() > limit {
        return Err("diagnostic limit exceeded; no partial result returned".into());
    }
    let mut output = Vec::new();
    let mut bytes = 0;
    for item in items {
        let start: Utf16Position = serde_json::from_value(item["range"]["start"].clone())?;
        let end: Utf16Position = serde_json::from_value(item["range"]["end"].clone())?;
        let start_byte = from_utf16_position(source, start)?;
        let end_byte = from_utf16_position(source, end)?;
        if start_byte > end_byte {
            return Err("reversed diagnostic range".into());
        }
        let message = text(&item["message"], 8192, &mut bytes)?;
        let severity = match item.get("severity") {
            None | Some(Value::Null) => Value::Null,
            Some(value) if value.as_u64().is_some_and(|n| (1..=4).contains(&n)) => value.clone(),
            _ => return Err("invalid diagnostic severity".into()),
        };
        let code = match item.get("code") {
            None | Some(Value::Null) => Value::Null,
            Some(value) if value.is_string() => json!(text(value, 256, &mut bytes)?),
            Some(value) if value.as_i64().is_some_and(|n| i32::try_from(n).is_ok()) => {
                value.clone()
            }
            _ => return Err("invalid diagnostic code".into()),
        };
        let origin = match item.get("source") {
            None | Some(Value::Null) => Value::Null,
            Some(value) => json!(text(value, 256, &mut bytes)?),
        };
        let tags = match item.get("tags") {
            None | Some(Value::Null) => vec![],
            Some(Value::Array(tags))
                if tags.len() <= 2
                    && tags.iter().all(|tag| matches!(tag.as_u64(), Some(1 | 2))) =>
            {
                tags.clone()
            }
            _ => return Err("invalid diagnostic tags".into()),
        };
        if let Some(related) = item.get("relatedInformation")
            && !related.is_null()
            && related.as_array().is_none_or(|items| !items.is_empty())
        {
            return Err("related diagnostic information was not negotiated".into());
        }
        output.push(json!({"range":{"start_byte":start_byte,"end_byte":end_byte,
            "start_line":start.line+1,"end_line":end.line+1},
            "category":if code == "syntax-error" { "syntax" } else { "semantic" },
            "severity":severity,"code":code,"source":origin,"message":message,"tags":tags}));
    }
    Ok(json!({"path":path,"diagnostics":output,"diagnostic_count":output.len(),"text_bytes":bytes}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(start: u64, end: u64) -> Value {
        json!({"range":{"start":{"line":0,"character":start},
            "end":{"line":0,"character":end}},"message":"expected u32, found bool",
            "severity":1,"code":"E0308","source":"rust-analyzer","tags":[1]})
    }

    fn report(items: Vec<Value>) -> Value {
        json!({"kind":"full","items":items})
    }

    #[test]
    fn workspace_reports_share_count_text_and_coordinate_guards() {
        let mut batch = Batch::new(1);
        batch
            .push("one.rs", "abc", report(vec![item(0, 1)]))
            .unwrap();
        batch.push("empty.rs", "", report(vec![])).unwrap();
        assert!(
            batch
                .push("two.rs", "abc", report(vec![item(0, 1)]))
                .is_err()
        );
        let result = batch.finish();
        assert_eq!(result["file_count"], 2);
        assert_eq!(result["diagnostic_count"], 1);
        assert_eq!(result["reports"][1]["diagnostic_count"], 0);
        let mut batch = Batch::new(100);
        let mut large = item(0, 1);
        large["message"] = json!("x".repeat(8192));
        batch
            .push("one.rs", "abc", report(vec![large.clone(); 4]))
            .unwrap();
        assert!(batch.push("two.rs", "abc", report(vec![large; 4])).is_err());
        assert!(
            batch
                .push("two.rs", "🚀", report(vec![item(1, 2)]))
                .is_err()
        );
        assert!(
            batch
                .push("two.rs", "", json!({"kind":"unchanged"}))
                .is_err()
        );
        let mut batch = Batch::new(1);
        for _ in 0..32 {
            batch.push("empty.rs", "", report(vec![])).unwrap();
        }
        assert!(batch.push("extra.rs", "", report(vec![])).is_err());
    }

    #[test]
    fn native_diagnostics_preserve_primary_data_and_original_ranges() {
        let mut first = item(2, 6);
        first["data"] = json!({"fix":"do not expose a mutation capability"});
        let mut anchor = item(6, 6);
        anchor["code"] = json!("syntax-error");
        let result = diagnostics("lib.rs", "🚀café\r\n", report(vec![first, anchor]), 2).unwrap();
        let items = result["diagnostics"].as_array().unwrap();
        assert_eq!(result["diagnostic_count"], 2);
        assert_eq!(items[0]["range"]["start_byte"], 4);
        assert_eq!(items[0]["range"]["end_byte"], 9);
        assert_eq!(items[0]["category"], "semantic");
        assert_eq!(items[1]["category"], "syntax");
        assert_eq!(items[1]["range"]["start_byte"], 9);
        assert_eq!(items[1]["range"]["end_byte"], 9);
        assert_eq!(items[0]["code"], "E0308");
        assert_eq!(items[0]["severity"], 1);
        assert!(items[0].get("data").is_none());
        assert_eq!(
            diagnostics("lib.rs", "", report(vec![]), 1).unwrap()["diagnostic_count"],
            0
        );
    }

    #[test]
    fn diagnostics_reject_invalid_protocol_ranges_and_truncation() {
        for value in [
            Value::Null,
            json!({"kind":"unchanged"}),
            json!({"kind":"full","items":null}),
            json!({"kind":"full","items":[],"relatedDocuments":{"file:///outside.rs":{}}}),
        ] {
            assert!(diagnostics("lib.rs", "", value, 10).is_err());
        }
        for (start, end) in [(1, 2), (3, 2), (0, 5)] {
            assert!(diagnostics("lib.rs", "🚀a", report(vec![item(start, end)]), 1).is_err());
        }
        assert!(diagnostics("lib.rs", "abc", report(vec![item(0, 1); 2]), 1).is_err());
        for (key, value) in [
            ("severity", json!(5)),
            ("code", json!(true)),
            ("code", json!(2147483648_i64)),
            ("source", json!("x".repeat(257))),
            ("message", json!("é".repeat(4097))),
            ("message", json!("")),
            ("tags", json!([3])),
            ("relatedInformation", json!([{}])),
        ] {
            let mut invalid = item(0, 1);
            invalid[key] = value;
            assert!(
                diagnostics("lib.rs", "abc", report(vec![invalid]), 1).is_err(),
                "{key}"
            );
        }
        let mut large = item(0, 1);
        large["message"] = json!("a".repeat(8192));
        assert!(diagnostics("lib.rs", "abc", report(vec![large; 9]), 9).is_err());
    }
}

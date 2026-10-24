//! Validate hierarchical document symbols before exposing source coordinates.
use crate::coordinates::{Utf16Position, from_utf16_position};
use serde::Serialize;
use serde_json::{Value, json};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Clone, Copy, Serialize)]
struct Span {
    start_byte: usize,
    end_byte: usize,
    start_line: u32,
    end_line: u32,
}

impl Span {
    fn contains(self, other: Self) -> bool {
        self.start_byte <= other.start_byte && other.end_byte <= self.end_byte
    }
}

#[derive(Serialize)]
struct Symbol {
    name: String,
    kind: u32,
    kind_name: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    detail: Option<String>,
    range: Span,
    selection: Span,
    children: Vec<Symbol>,
}

fn kind_name(kind: u32) -> &'static str {
    const NAMES: [&str; 26] = [
        "file",
        "module",
        "namespace",
        "package",
        "class",
        "method",
        "property",
        "field",
        "constructor",
        "enum",
        "interface",
        "function",
        "variable",
        "constant",
        "string",
        "number",
        "boolean",
        "array",
        "object",
        "key",
        "null",
        "enum_member",
        "struct",
        "event",
        "operator",
        "type_parameter",
    ];
    kind.checked_sub(1)
        .and_then(|n| NAMES.get(n as usize))
        .copied()
        .unwrap_or("unknown")
}

struct Normalizer<'a> {
    source: &'a str,
    count: usize,
    limit: usize,
}

impl Normalizer<'_> {
    fn span(&self, value: &Value) -> Result<Span> {
        let start: Utf16Position = serde_json::from_value(value["start"].clone())?;
        let end: Utf16Position = serde_json::from_value(value["end"].clone())?;
        let start_byte = from_utf16_position(self.source, start)?;
        let end_byte = from_utf16_position(self.source, end)?;
        if start_byte > end_byte {
            return Err("reversed document-symbol range".into());
        }
        Ok(Span {
            start_byte,
            end_byte,
            start_line: start.line + 1,
            end_line: end.line + 1,
        })
    }

    fn nodes(
        &mut self,
        values: &[Value],
        depth: usize,
        parent: Option<Span>,
    ) -> Result<Vec<Symbol>> {
        if !values.is_empty() && depth > 32 {
            return Err("document-symbol hierarchy exceeds depth 32".into());
        }
        let mut result = Vec::new();
        for value in values {
            if self.count == self.limit {
                return Err("document-symbol limit exceeded; no partial outline returned".into());
            }
            self.count += 1;
            let name = value["name"]
                .as_str()
                .ok_or("missing document-symbol name")?;
            if name.trim().is_empty() || name.len() > 4096 {
                return Err("invalid or oversized document-symbol name".into());
            }
            let detail = match value.get("detail") {
                None => None,
                Some(Value::String(detail)) if detail.len() <= 4096 => Some(detail.clone()),
                _ => return Err("invalid or oversized document-symbol detail".into()),
            };
            let kind = value["kind"]
                .as_u64()
                .and_then(|n| u32::try_from(n).ok())
                .ok_or("invalid document-symbol kind")?;
            let range = self.span(&value["range"])?;
            let selection = self.span(&value["selectionRange"])?;
            if !range.contains(selection) || parent.is_some_and(|parent| !parent.contains(range)) {
                return Err(
                    "document-symbol selection or child lies outside its parent range".into(),
                );
            }
            let children = match value.get("children") {
                None => Vec::new(),
                Some(Value::Array(children)) => self.nodes(children, depth + 1, Some(range))?,
                _ => return Err("invalid document-symbol children".into()),
            };
            result.push(Symbol {
                name: name.to_string(),
                kind,
                kind_name: kind_name(kind),
                detail,
                range,
                selection,
                children,
            });
        }
        Ok(result)
    }
}

pub(crate) fn outline(path: &str, source: &str, value: Value, limit: usize) -> Result<Value> {
    let mut normalizer = Normalizer {
        source,
        count: 0,
        limit,
    };
    let values = if value.is_null() {
        &[][..]
    } else {
        value
            .as_array()
            .ok_or("expected hierarchical document symbols")?
            .as_slice()
    };
    let symbols = normalizer.nodes(values, 1, None)?;
    Ok(json!({"path":path,"symbols":symbols,"symbol_count":normalizer.count}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn symbol(start: u32, end: u32) -> Value {
        json!({"name":"item","kind":23,"range":{"start":{"line":0,"character":start},
            "end":{"line":0,"character":end}},"selectionRange":{"start":{"line":0,"character":start},
            "end":{"line":0,"character":end}}})
    }

    #[test]
    fn outline_preserves_hierarchy_names_kinds_and_unicode_ranges() {
        let mut parent = symbol(0, 5);
        parent["children"] = json!([symbol(2, 3)]);
        let result = outline("lib.rs", "🚀abc", json!([parent]), 2).unwrap();
        assert_eq!(result["symbol_count"], 2);
        assert_eq!(result["symbols"][0]["kind_name"], "struct");
        assert_eq!(
            result["symbols"][0]["children"][0]["selection"]["start_byte"],
            4
        );
        assert_eq!(
            result["symbols"][0]["children"][0]["selection"]["end_byte"],
            5
        );
        assert_eq!(kind_name(99), "unknown");
    }

    #[test]
    fn outline_rejects_invalid_structure_ranges_and_limits() {
        let valid = symbol(0, 3);
        assert!(outline("lib.rs", "abc", json!([valid.clone(), valid.clone()]), 1).is_err());
        let mut bad = valid.clone();
        bad["children"] = json!([symbol(2, 4)]);
        assert!(outline("lib.rs", "abcd", json!([bad]), 10).is_err());
        let mut bad = valid.clone();
        bad["selectionRange"] = symbol(2, 4)["range"].clone();
        assert!(outline("lib.rs", "abcd", json!([bad]), 10).is_err());
        for (key, value) in [
            ("name", json!(" ")),
            ("name", json!("x".repeat(4097))),
            ("detail", json!("x".repeat(4097))),
            ("kind", json!(-1)),
            ("children", json!({})),
        ] {
            let mut bad = valid.clone();
            bad[key] = value;
            assert!(outline("lib.rs", "abc", json!([bad]), 10).is_err());
        }
        assert!(outline("lib.rs", "🚀a", json!([symbol(1, 3)]), 10).is_err());
        assert!(
            outline(
                "lib.rs",
                "abc",
                json!([{"name":"item","kind":12,"location":{}}]),
                10
            )
            .is_err()
        );
        let mut deep = valid.clone();
        for _ in 0..32 {
            let mut parent = valid.clone();
            parent["children"] = json!([deep]);
            deep = parent;
        }
        assert!(outline("lib.rs", "abc", json!([deep]), 100).is_err());
        assert_eq!(
            outline("empty.rs", "", Value::Null, 10).unwrap()["symbol_count"],
            0
        );
    }
}
